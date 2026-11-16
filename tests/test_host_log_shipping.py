"""Journal shipping from the fleet hosts that are not k3s nodes.

The k3s nodes ship their journals through the Alloy DaemonSet. nas-01 and
adguard-netbird-01 run Alloy as a NixOS service instead
(flake/modules/host-log-shipping.nix) and push to the loki-push VIP, which a
CiliumNetworkPolicy opens to exactly those two addresses for log pushes only.
"""

from __future__ import annotations

import ipaddress
import json
import re
import shutil
import subprocess
import unittest
from functools import cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
FLEET = REPO_ROOT / "inventory/fleet.json"
OBSERVABILITY = REPO_ROOT / "gitops/observability"
ALLOY_RELEASE = OBSERVABILITY / "alloy/release.yaml"
LOKI_RELEASE = OBSERVABILITY / "loki/release.yaml"
PUSH_SERVICE = OBSERVABILITY / "loki/push-service.yaml"
PUSH_POLICY = OBSERVABILITY / "loki/push-policy.yaml"
KUSTOMIZATION = OBSERVABILITY / "kustomization.yaml"
JOURNAL_RULES = OBSERVABILITY / "loki/rules-configmap.yaml"

PUSH_PATH = "/loki/api/v1/push"
# CiliumLoadBalancerIPPool in flake/modules/k3s-server.nix.
LB_POOL = (ipaddress.ip_address("10.0.40.10"), ipaddress.ip_address("10.0.40.19"))

SHIPPING_APPLY = """
configurations:
builtins.mapAttrs (
  name: system:
  let
    config = system.config;
    shipping = config.homelab.logShipping or { enable = false; };
  in
  if !shipping.enable then
    { enable = false; }
  else
    {
      enable = true;
      alloyEnabled = config.services.alloy.enable;
      hostName = config.networking.hostName;
      lokiAddress = shipping.lokiAddress;
      lokiUrl = shipping.lokiUrl;
      alloyConfig = config.environment.etc."alloy/config.alloy".text;
      serviceConfig = {
        inherit (config.systemd.services.alloy.serviceConfig)
          User
          DynamicUser
          SupplementaryGroups
          MemoryMax
          IPAddressAllow
          ExecStart
          ;
      };
      persisted = map (entry: entry.directory) config.environment.persistence."/persist".directories;
    }
) configurations
"""


@cache
def shipping_hosts() -> dict[str, dict[str, Any]]:
    if shutil.which("nix") is None:
        raise unittest.SkipTest("nix is required for configuration evaluation")
    result = subprocess.run(
        [
            "nix",
            "eval",
            "--json",
            f"path:{REPO_ROOT}?dir=flake#nixosConfigurations",
            "--apply",
            SHIPPING_APPLY,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    evaluated = json.loads(result.stdout)
    return {name: host for name, host in evaluated.items() if host["enable"]}


def fleet() -> dict[str, Any]:
    return json.loads(FLEET.read_text(encoding="utf-8"))


def non_cluster_hosts() -> dict[str, str]:
    data = fleet()
    return {
        name: host["ip"]
        for name, host in data["hosts"].items()
        if name not in data["cluster_nodes"]
    }


def block(config: str, header: str) -> str:
    """Return one top-level Alloy block, whitespace-normalised."""
    start = config.index(header)
    depth = 0
    for index in range(start, len(config)):
        if config[index] == "{":
            depth += 1
        elif config[index] == "}":
            depth -= 1
            if depth == 0:
                return " ".join(config[start : index + 1].split())
    raise AssertionError(f"unterminated block {header}")


def cluster_alloy_config() -> str:
    release = yaml.safe_load(ALLOY_RELEASE.read_text(encoding="utf-8"))
    return release["spec"]["values"]["alloy"]["configMap"]["content"]


def load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class HostShippingConfigurationTests(unittest.TestCase):
    def test_exactly_the_non_cluster_hosts_ship(self) -> None:
        self.assertEqual(set(shipping_hosts()), set(non_cluster_hosts()))
        self.assertEqual(set(shipping_hosts()), {"nas-01", "adguard-netbird-01"})

    def test_hostname_label_matches_inventory_name(self) -> None:
        # The hostname label comes from the journal's _HOSTNAME, which is
        # networking.hostName; alert descriptions print it.
        for name, host in shipping_hosts().items():
            self.assertEqual(host["hostName"], name)

    def test_endpoint_is_the_push_vip(self) -> None:
        vip = load(PUSH_SERVICE)["metadata"]["annotations"]["io.cilium/lb-ipam-ips"]
        for name, host in shipping_hosts().items():
            with self.subTest(host=name):
                self.assertTrue(host["alloyEnabled"])
                self.assertEqual(host["lokiAddress"], vip)
                self.assertEqual(host["lokiUrl"], f"http://{vip}{PUSH_PATH}")
                self.assertIn(f'url = "http://{vip}{PUSH_PATH}"', host["alloyConfig"])

    def test_labels_and_components_match_the_cluster_daemonset(self) -> None:
        cluster = cluster_alloy_config()
        for name, host in shipping_hosts().items():
            with self.subTest(host=name):
                config = host["alloyConfig"]
                for header in (
                    'loki.source.journal "journal"',
                    'discovery.relabel "journal"',
                ):
                    self.assertEqual(block(config, header), block(cluster, header))
                self.assertIn('loki.write "local"', config)

    def test_journal_rules_select_the_shipped_component(self) -> None:
        rules = JOURNAL_RULES.read_text(encoding="utf-8")
        for alert in ("ZfsPoolIoError", "HostKernelOOMKilled", "HostSSHAuthFailed"):
            self.assertIn(alert, rules)
        self.assertIn('{component="system-journal"}', rules)
        for host in shipping_hosts().values():
            self.assertIn(
                'labels        = { component = "system-journal" }', host["alloyConfig"]
            )

    def test_unit_is_unprivileged_bounded_and_persists_positions(self) -> None:
        for name, host in shipping_hosts().items():
            with self.subTest(host=name):
                service = host["serviceConfig"]
                self.assertEqual(service["User"], "alloy")
                self.assertFalse(service["DynamicUser"])
                self.assertEqual(service["SupplementaryGroups"], ["systemd-journal"])
                self.assertTrue(service["MemoryMax"])
                self.assertEqual(
                    service["IPAddressAllow"], ["localhost", host["lokiAddress"]]
                )
                self.assertIn(
                    "--storage.path=/var/lib/alloy/data", service["ExecStart"]
                )
                self.assertIn("/var/lib/alloy", host["persisted"])


class PushPolicyTests(unittest.TestCase):
    def test_policy_admits_only_the_shipping_hosts_for_pushes(self) -> None:
        policy = load(PUSH_POLICY)
        self.assertEqual(policy["kind"], "CiliumNetworkPolicy")
        rules = policy["spec"]["ingress"]
        self.assertNotIn("egress", policy["spec"])

        external = [rule for rule in rules if "fromEndpoints" not in rule]
        self.assertEqual(len(external), 1)
        rule = external[0]
        self.assertEqual(set(rule), {"fromCIDR", "toPorts"})
        expected = {f"{ip}/32" for ip in non_cluster_hosts().values()}
        self.assertEqual(set(rule["fromCIDR"]), expected)
        self.assertEqual(expected, {"10.0.30.20/32", "10.0.30.10/32"})
        (port,) = rule["toPorts"]
        self.assertEqual(port["ports"], [{"port": "8080", "protocol": "TCP"}])
        self.assertEqual(port["rules"]["http"], [{"method": "POST", "path": PUSH_PATH}])

    def test_in_cluster_access_is_the_observability_namespace_only(self) -> None:
        policy = load(PUSH_POLICY)
        internal = [
            rule for rule in policy["spec"]["ingress"] if "fromEndpoints" in rule
        ]
        self.assertEqual(len(internal), 1)
        self.assertEqual(
            internal[0]["fromEndpoints"],
            [{"matchLabels": {"k8s:io.kubernetes.pod.namespace": "observability"}}],
        )
        for rule in policy["spec"]["ingress"]:
            self.assertNotIn("fromEntities", rule)

    def test_service_preserves_client_address_and_targets_the_policed_pods(
        self,
    ) -> None:
        service = load(PUSH_SERVICE)
        policy = load(PUSH_POLICY)
        spec = service["spec"]
        self.assertEqual(spec["type"], "LoadBalancer")
        self.assertEqual(spec["externalTrafficPolicy"], "Local")
        self.assertFalse(spec["allocateLoadBalancerNodePorts"])
        self.assertEqual(service["metadata"]["labels"]["bgp-advertise"], "true")
        self.assertEqual(
            spec["selector"], policy["spec"]["endpointSelector"]["matchLabels"]
        )
        self.assertEqual([port["targetPort"] for port in spec["ports"]], [8080])
        vip = ipaddress.ip_address(
            service["metadata"]["annotations"]["io.cilium/lb-ipam-ips"]
        )
        self.assertTrue(LB_POOL[0] <= vip <= LB_POOL[1])

    def test_vip_is_not_shared(self) -> None:
        vip = load(PUSH_SERVICE)["metadata"]["annotations"]["io.cilium/lb-ipam-ips"]
        users = [
            path
            for path in (REPO_ROOT / "gitops").rglob("*.yaml")
            if re.search(
                rf"lb-ipam-ips:\s*{re.escape(vip)}\b", path.read_text(encoding="utf-8")
            )
        ]
        self.assertEqual(users, [PUSH_SERVICE])

    def test_gateway_runs_on_bgp_peers(self) -> None:
        values = load(LOKI_RELEASE)["spec"]["values"]
        self.assertEqual(
            values["gateway"]["nodeSelector"],
            {"node-role.kubernetes.io/control-plane": "true"},
        )

    def test_manifests_are_in_the_kustomization(self) -> None:
        resources = load(KUSTOMIZATION)["resources"]
        self.assertIn("loki/push-service.yaml", resources)
        self.assertIn("loki/push-policy.yaml", resources)


if __name__ == "__main__":
    unittest.main()
