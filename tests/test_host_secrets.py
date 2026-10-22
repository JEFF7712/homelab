from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
# scripts/deploy_fleet.py activates the registry variant on k3s nodes.
K3S_NODES = {"homelab-01", "homelab-02", "homelab-03", "homelab-04", "homelab-05"}
HOSTS = ["adguard-netbird-01", "nas-01", *sorted(K3S_NODES)]
# Registry pull credentials are rendered from the image lock by the registry
# rollout operations after registry-verify, so CI keeps owning them.
CI_OWNED = {
    "/persist/secrets/k3s-registries.yaml",
    "/persist/secrets/registry-auth.json",
}
OPERATORS = {"operator_flux", "operator_recovery"}


def recipient_anchors() -> dict[str, str]:
    """Map each .sops.yaml key anchor name to its age recipient."""
    events = yaml.parse((ROOT / ".sops.yaml").read_text())
    return {
        event.anchor: event.value
        for event in events
        if isinstance(event, yaml.ScalarEvent) and event.anchor
    }


def rule_recipients(path: str) -> set[str]:
    rules = yaml.safe_load((ROOT / ".sops.yaml").read_text())["creation_rules"]
    for rule in rules:
        if re.search(rule["path_regex"], path):
            if "key_groups" in rule:
                return {key for group in rule["key_groups"] for key in group["age"]}
            return set(rule["age"].split(","))
    raise AssertionError(f"no .sops.yaml rule matches {path}")


def load_bundle(path: Path) -> dict:
    text = path.read_text()
    return json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)


def repository_path(store_path: str) -> str:
    return "secrets/" + store_path.split("/secrets/", 1)[1]


class HostSecretsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("nix") is None:
            raise unittest.SkipTest("nix is required for host evaluation")
        expression = """
          let
            flake = builtins.getFlake ROOT_FLAKE;
            lib = flake.inputs.nixpkgs.lib;
            describe = cfg: {
              defaultSopsFile =
                let attempt = builtins.tryEval (toString cfg.sops.defaultSopsFile);
                in if attempt.success then attempt.value else null;
              ageKeys = cfg.sops.age.sshKeyPaths;
              secrets = lib.mapAttrs (_: s: {
                inherit (s) key path owner mode restartUnits;
                sopsFile = toString s.sopsFile;
              }) cfg.sops.secrets;
              templates = lib.mapAttrs (_: t: {
                inherit (t) path restartUnits;
              }) cfg.sops.templates;
              units = lib.mapAttrs (_: s: builtins.toJSON {
                script = s.script;
                serviceConfig = s.serviceConfig;
                unitConfig = s.unitConfig;
              }) cfg.systemd.services;
            };
            attribute = host: if builtins.elem host K3S_NODES then "${host}-registry" else host;
          in lib.genAttrs HOSTS (host: describe flake.nixosConfigurations.${attribute host}.config)
        """
        expression = expression.replace(
            "ROOT_FLAKE", json.dumps(f"git+file://{ROOT}?dir=flake")
        )
        expression = expression.replace(
            "K3S_NODES", json.dumps(sorted(K3S_NODES)).replace(",", " ")
        ).replace("HOSTS", json.dumps(HOSTS).replace(",", " "))
        result = subprocess.run(
            ["nix", "eval", "--impure", "--json", "--expr", expression],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stderr)
        cls.evaluated = json.loads(result.stdout)

    def test_hosts_decrypt_their_own_file_with_the_persisted_host_key(self) -> None:
        for host in HOSTS:
            with self.subTest(host=host):
                config = self.evaluated[host]
                self.assertEqual(
                    config["ageKeys"], ["/persist/etc/ssh/ssh_host_ed25519_key"]
                )
                own = ROOT / f"secrets/hosts/{host}.sops.yaml"
                if own.exists():
                    self.assertEqual(
                        repository_path(config["defaultSopsFile"]),
                        f"secrets/hosts/{host}.sops.yaml",
                    )
                else:
                    self.assertIsNone(config["defaultSopsFile"])

    def test_file_recipients_are_operators_plus_consuming_hosts(self) -> None:
        anchors = recipient_anchors()
        operators = {anchors[name] for name in OPERATORS}
        consumers: dict[str, set[str]] = {}
        for host in HOSTS:
            for secret in self.evaluated[host]["secrets"].values():
                path = repository_path(secret["sopsFile"])
                if path.startswith(("secrets/hosts/", "secrets/shared/")):
                    consumers.setdefault(path, set()).add(anchors[host])
        self.assertIn("secrets/shared/mqtt-roku-bridge.sops.yaml", consumers)
        for path, hosts in consumers.items():
            with self.subTest(path=path):
                expected = rule_recipients(path)
                self.assertTrue(operators <= expected)
                if path.startswith("secrets/hosts/"):
                    host = path.removeprefix("secrets/hosts/").removesuffix(
                        ".sops.yaml"
                    )
                    self.assertEqual(expected - operators, {anchors[host]})
                else:
                    # Hosts outside HOSTS are migrated in later changes.
                    evaluated = {anchors[host] for host in HOSTS}
                    self.assertEqual((expected - operators) & evaluated, hosts)
                bundle = load_bundle(ROOT / path)
                actual = {entry["recipient"] for entry in bundle["sops"]["age"]}
                self.assertEqual(actual, expected)

    def test_every_declared_secret_exists_in_its_file(self) -> None:
        for host in HOSTS:
            for name, secret in self.evaluated[host]["secrets"].items():
                with self.subTest(host=host, secret=name):
                    path = ROOT / repository_path(secret["sopsFile"])
                    if secret["key"]:
                        bundle = load_bundle(path)
                        self.assertIn(secret["key"], set(bundle) - {"sops"})
                    self.assertEqual(secret["mode"], "0400")
                    self.assertEqual(secret["path"], f"/run/secrets/{name}")

    def test_no_unit_reads_hand_placed_secrets(self) -> None:
        for host in HOSTS:
            for unit, text in self.evaluated[host]["units"].items():
                with self.subTest(host=host, unit=unit):
                    paths = set(re.findall(r"/persist/secrets/[A-Za-z0-9._-]+", text))
                    self.assertLessEqual(paths, CI_OWNED)

    def test_joining_k3s_nodes_share_one_token(self) -> None:
        for host in sorted(K3S_NODES - {"homelab-01"}):
            with self.subTest(host=host):
                secret = self.evaluated[host]["secrets"]["k3s-token"]
                self.assertEqual(
                    repository_path(secret["sopsFile"]), "secrets/shared/k3s.sops.yaml"
                )
                self.assertEqual(secret["restartUnits"], [])
                self.assertIn(
                    "--token-file /run/secrets/k3s-token",
                    self.evaluated[host]["units"]["k3s"],
                )
        self.assertNotIn("k3s-token", self.evaluated["homelab-01"]["secrets"])

    def test_secret_changes_restart_their_consumers(self) -> None:
        config = self.evaluated["adguard-netbird-01"]
        for name in (
            "mosquitto-home-assistant-password",
            "mosquitto-zigbee2mqtt-password",
            "mosquitto-roku-bridge-password",
            "mosquitto-bus-display-password",
        ):
            self.assertEqual(
                config["secrets"][name]["restartUnits"], ["mosquitto.service"]
            )
        self.assertEqual(
            config["secrets"]["roku-bridge-bulbs"]["restartUnits"],
            ["roku-bridge.service"],
        )
        self.assertEqual(
            config["secrets"]["roku-cloud-bulbs"]["restartUnits"],
            ["roku-cloud-bridge.service"],
        )
        for template, unit in (
            ("roku-bridge-mqtt.env", "roku-bridge.service"),
            ("roku-cloud-bridge-mqtt.env", "roku-cloud-bridge.service"),
            ("ledfx-roku-bridge-mqtt.env", "ledfx-roku-bridge.service"),
            ("ledfx-zigbee-bridge-mqtt.env", "ledfx-zigbee-bridge.service"),
            ("zigbee2mqtt-secret.yaml", "zigbee2mqtt-secrets.service"),
        ):
            with self.subTest(template=template):
                self.assertEqual(config["templates"][template]["restartUnits"], [unit])

    def test_bridges_read_owned_secrets_directly(self) -> None:
        config = self.evaluated["adguard-netbird-01"]
        self.assertEqual(config["secrets"]["roku-bridge-bulbs"]["owner"], "roku-bridge")
        for name in ("roku-cloud-bulbs", "roku-cloud-cookies"):
            self.assertEqual(config["secrets"][name]["owner"], "roku-cloud-bridge")
        bridge = config["units"]["roku-bridge"]
        self.assertIn("--config /run/secrets/roku-bridge-bulbs", bridge)
        self.assertIn("/run/secrets/rendered/roku-bridge-mqtt.env", bridge)
        cloud = config["units"]["roku-cloud-bridge"]
        self.assertIn("--cookie-seed /run/secrets/roku-cloud-cookies", cloud)
        self.assertIn("--cookies /var/lib/roku-cloud-bridge/cookies.json", cloud)

    def test_service_secrets_restart_their_consumers(self) -> None:
        ci_host = self.evaluated["homelab-04"]
        approvals = ci_host["units"]["approvals"]
        self.assertIn("operator-token:/run/secrets/approvals-operator-token", approvals)
        self.assertIn('"EnvironmentFile":"/run/secrets/approvals.env"', approvals)
        for name in ("approvals-operator-token", "approvals.env"):
            self.assertEqual(
                ci_host["secrets"][name]["restartUnits"], ["approvals.service"]
            )
        self.assertIn(
            "kiosk-ha-token:/run/secrets/kiosk-ha-token",
            ci_host["units"]["kiosk-rotate"],
        )
        voice_host = self.evaluated["homelab-05"]
        self.assertEqual(
            voice_host["secrets"]["mosquitto-roku-bridge-password"]["restartUnits"],
            [
                "apollo-ble-bridge-kitchen.service",
                "apollo-ble-bridge-living-room.service",
            ],
        )
        for host in ("homelab-04", "nas-01"):
            with self.subTest(host=host):
                self.assertIn(
                    "/run/secrets/platform-backup.env",
                    self.evaluated[host]["units"]["platform-backup"],
                )

    def test_nas_state_and_registry_credentials_use_sops(self) -> None:
        nas = self.evaluated["nas-01"]
        for name in (
            "tofu-state-credentials.json",
            "tofu-state-server.pem",
            "tofu-state-server-key.pem",
        ):
            with self.subTest(secret=name):
                self.assertEqual(nas["secrets"][name]["owner"], "tofu-state")
                self.assertIn(f"/run/secrets/{name}", nas["units"]["tofu-state"])
        self.assertIn(
            "RESTIC_PASSWORD_FILE=/run/secrets/registry-restic-password",
            nas["units"]["registry-backup"],
        )

    def test_ci_host_reads_woodpecker_environments_from_sops(self) -> None:
        ci_host = self.evaluated["homelab-04"]
        server = ci_host["units"]["woodpecker-server"]
        for name in ("woodpecker-server.env", "woodpecker-clone.env"):
            self.assertIn(f"/run/secrets/{name}", server)
        clone = ci_host["secrets"]["woodpecker-clone.env"]
        self.assertEqual(
            repository_path(clone["sopsFile"]), "secrets/woodpecker-clone.sops.env"
        )
        for tier in ("sandbox", "trusted", "deploy"):
            with self.subTest(tier=tier):
                self.assertIn(
                    f"/run/secrets/woodpecker-agent-{tier}.env",
                    ci_host["units"][f"woodpecker-agent-{tier}"],
                )
        self.assertIn(
            "/run/secrets/woodpecker-policy.env", ci_host["units"]["woodpecker-policy"]
        )


if __name__ == "__main__":
    unittest.main()
