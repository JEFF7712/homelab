"""LAN gateway: same-name HTTPS on the LAN for selected tunnel hostnames.

config/lan-gateway.json selects hostnames; Unbound answers them with the
gateway VIP and the in-cluster router reproduces their tunnel rules.
"""

from __future__ import annotations

import ipaddress
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

from scripts import lan_gateway
from tests.gitops_render import rendered

ROOT = Path(__file__).resolve().parents[1]
LB_POOL = (ipaddress.IPv4Address("10.0.40.10"), ipaddress.IPv4Address("10.0.40.19"))
BYPASS = "${cloudflare_zero_trust_access_policy.bypass.id}"


def config() -> dict[str, Any]:
    return json.loads((ROOT / "config/lan-gateway.json").read_text())


def enabled_hosts() -> list[str]:
    return [host["name"] for host in config()["hosts"] if host.get("enabled", True)]


def access_inventory() -> dict[str, Any]:
    output = subprocess.run(
        [
            "nix-instantiate",
            "--eval",
            "--strict",
            "--json",
            str(ROOT / "tofu/cloudflare-access/inventory.nix"),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(output)


def objects() -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (doc["kind"], doc["metadata"]["name"]): doc for doc in rendered("lan-gateway")
    }


def server_block(conf: str, name: str) -> str:
    match = re.search(
        rf"  server {{\n    listen 8443 ssl;\n    server_name {re.escape(name)};\n(.*?)\n  }}\n",
        conf,
        re.S,
    )
    if match is None:
        raise AssertionError(f"no server block for {name}")
    return match.group(1)


class RenderedConfigTest(unittest.TestCase):
    def test_committed_config_is_current(self) -> None:
        self.assertEqual(
            (ROOT / lan_gateway.OUTPUT).read_text(),
            lan_gateway.render(ROOT),
            "run python -m scripts.lan_gateway",
        )

    def test_every_enabled_host_has_one_server_and_no_others(self) -> None:
        conf = (ROOT / lan_gateway.OUTPUT).read_text()
        names = re.findall(r"server_name (\S+);", conf)
        self.assertEqual(sorted(names), sorted(enabled_hosts()))

    def test_vaultwarden_admin_stays_blocked_on_the_lan(self) -> None:
        block = server_block((ROOT / lan_gateway.OUTPUT).read_text(), "vault.rupan.dev")
        admin = block.index('location ~ "^/admin" {\n      return 404;')
        self.assertLess(admin, block.index("location / {"))

    def test_home_assistant_receives_no_forwarded_for_header(self) -> None:
        conf = (ROOT / lan_gateway.OUTPUT).read_text()
        self.assertNotIn("X-Forwarded-For", server_block(conf, "ha.rupan.dev"))
        self.assertIn("X-Forwarded-For", server_block(conf, "photos.rupan.dev"))

    def test_unknown_names_are_refused_and_plain_http_redirects(self) -> None:
        conf = (ROOT / lan_gateway.OUTPUT).read_text()
        self.assertIn(
            "listen 8443 ssl default_server;\n    ssl_reject_handshake on;", conf
        )
        self.assertIn("return 308 https://$host$request_uri;", conf)


class RendererTest(unittest.TestCase):
    def write(self, root: Path, hosts: list[dict[str, Any]], rules: list[Any]) -> None:
        (root / "config").mkdir()
        (root / "config/lan-gateway.json").write_text(
            json.dumps({"address": "10.0.40.19", "hosts": hosts})
        )
        tunnel = yaml.safe_dump({"ingress": rules})
        (root / "gitops/cloudflare").mkdir(parents=True)
        (root / lan_gateway.INGRESS).write_text(
            yaml.safe_dump({"data": {"config.yaml": tunnel}})
        )

    def render(self, hosts: list[dict[str, Any]], rules: list[Any]) -> str:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, hosts, rules)
            return lan_gateway.render(root)

    def test_path_rules_keep_tunnel_order_before_the_default(self) -> None:
        conf = self.render(
            [{"name": "a.rupan.dev"}],
            [
                {
                    "hostname": "a.rupan.dev",
                    "path": "^/x$",
                    "service": "http://x.ns:81",
                },
                {"hostname": "b.rupan.dev", "service": "http://b.ns:80"},
                {"hostname": "a.rupan.dev", "service": "http://a.ns"},
                {"service": "http_status:404"},
            ],
        )
        block = server_block(conf, "a.rupan.dev")
        self.assertLess(
            block.index("http://x.ns.svc.cluster.local:81"),
            block.index("http://a.ns.svc.cluster.local:80"),
        )
        self.assertNotIn("b.rupan.dev", conf)

    def test_host_with_only_path_rules_falls_back_to_404(self) -> None:
        conf = self.render(
            [{"name": "a.rupan.dev"}],
            [{"hostname": "a.rupan.dev", "path": "^/x", "service": "http://x.ns:80"}],
        )
        self.assertIn(
            "location / {\n      return 404;", server_block(conf, "a.rupan.dev")
        )

    def test_disabled_hosts_are_omitted(self) -> None:
        conf = self.render(
            [{"name": "a.rupan.dev", "enabled": False}],
            [{"hostname": "a.rupan.dev", "service": "http://a.ns:80"}],
        )
        self.assertNotIn("a.rupan.dev", conf)

    def test_rejects_hosts_the_tunnel_does_not_serve(self) -> None:
        with self.assertRaisesRegex(ValueError, "no Cloudflare Tunnel ingress rule"):
            self.render([{"name": "a.rupan.dev"}], [])

    def test_rejects_rules_shadowed_by_a_pathless_rule(self) -> None:
        with self.assertRaisesRegex(ValueError, "unreachable"):
            self.render(
                [{"name": "a.rupan.dev"}],
                [
                    {"hostname": "a.rupan.dev", "service": "http://a.ns:80"},
                    {
                        "hostname": "a.rupan.dev",
                        "path": "^/x",
                        "service": "http://x.ns:80",
                    },
                ],
            )

    def test_rejects_origins_outside_the_cluster(self) -> None:
        with self.assertRaisesRegex(ValueError, "not a cluster Service"):
            self.render(
                [{"name": "a.rupan.dev"}],
                [{"hostname": "a.rupan.dev", "service": "http://api.example.com:80"}],
            )

    def test_rejects_duplicate_hosts(self) -> None:
        with self.assertRaisesRegex(ValueError, "unique"):
            self.render(
                [{"name": "a.rupan.dev"}, {"name": "a.rupan.dev"}],
                [{"hostname": "a.rupan.dev", "service": "http://a.ns:80"}],
            )


class ExposureTest(unittest.TestCase):
    def test_lan_hosts_have_only_bypass_access_policies(self) -> None:
        """Access never sees LAN traffic, so a gated host would lose its login."""
        applications = access_inventory()["applications"].values()
        for name in enabled_hosts():
            with self.subTest(host=name):
                matching = [
                    app
                    for app in applications
                    for destination in app.get("destinations", [])
                    if destination["uri"].split("/", 1)[0] == name
                ]
                self.assertTrue(matching, f"{name} has no exact Access application")
                for app in matching:
                    self.assertEqual(
                        [policy["id"] for policy in app["policies"]], [BYPASS]
                    )

    def test_wildcard_certificate_covers_every_host(self) -> None:
        names = objects()[("Certificate", "lan-gateway")]["spec"]["dnsNames"]
        self.assertEqual(names, ["*.rupan.dev"])
        for name in enabled_hosts():
            with self.subTest(host=name):
                self.assertRegex(name, r"^[a-z0-9-]+\.rupan\.dev$")


class ResolutionTest(unittest.TestCase):
    def test_unbound_answers_each_host_with_the_gateway_vip(self) -> None:
        overrides = {
            f"{entry['hostname']}.{entry['domain']}": entry
            for entry in json.loads(
                (ROOT / "opnsense_reconciler/unbound-host-overrides.json").read_text()
            )
        }
        for host in config()["hosts"]:
            with self.subTest(host=host["name"]):
                entry = overrides[host["name"]]
                self.assertEqual(entry["server"], config()["address"])
                self.assertEqual(entry["enabled"], "1" if host["enabled"] else "0")

    def test_lan_hosts_stay_out_of_adguard_and_coredns(self) -> None:
        rewrites = json.loads((ROOT / "inventory/adguard-rewrites.json").read_text())
        coredns = (ROOT / "gitops/platform/coredns-custom.yaml").read_text()
        for name in enabled_hosts():
            with self.subTest(host=name):
                self.assertNotIn(name, {entry["domain"] for entry in rewrites})
                self.assertNotIn(name, coredns)

    def test_service_holds_the_configured_vip_alone(self) -> None:
        service = objects()[("Service", "lan-gateway")]
        vip = service["metadata"]["annotations"]["io.cilium/lb-ipam-ips"]
        self.assertEqual(vip, config()["address"])
        self.assertTrue(LB_POOL[0] <= ipaddress.IPv4Address(vip) <= LB_POOL[1])
        self.assertEqual(service["metadata"]["labels"]["bgp-advertise"], "true")
        users = [
            path
            for path in (ROOT / "gitops").rglob("*.yaml")
            if re.search(rf"lb-ipam-ips:\s*{re.escape(vip)}\b", path.read_text())
        ]
        self.assertEqual(users, [ROOT / "gitops/lan-gateway/service.yaml"])
        ports = {port["port"]: port["targetPort"] for port in service["spec"]["ports"]}
        self.assertEqual(ports, {80: "http", 443: "https"})


class WorkloadTest(unittest.TestCase):
    def test_router_runs_in_the_tunnel_namespace(self) -> None:
        """Application network policies admit the cloudflare namespace."""
        for (kind, _), doc in objects().items():
            with self.subTest(kind=kind):
                self.assertEqual(doc["metadata"]["namespace"], "cloudflare")

    def test_router_mounts_generated_config_and_certificate(self) -> None:
        found = objects()
        pod = found[("Deployment", "lan-gateway")]["spec"]["template"]["spec"]
        volumes = {volume["name"]: volume for volume in pod["volumes"]}
        self.assertEqual(
            volumes["tls"]["secret"]["secretName"],
            found[("Certificate", "lan-gateway")]["spec"]["secretName"],
        )
        config_maps = [name for kind, name in found if kind == "ConfigMap"]
        self.assertEqual([volumes["config"]["configMap"]["name"]], config_maps)
        container = pod["containers"][0]
        self.assertRegex(
            container["image"],
            r"^registry\.rupan\.dev/upstream/.+@sha256:[0-9a-f]{64}$",
        )


if __name__ == "__main__":
    unittest.main()
