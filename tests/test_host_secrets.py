from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
HOSTS = ["adguard-netbird-01"]
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
              defaultSopsFile = toString cfg.sops.defaultSopsFile;
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
          in lib.genAttrs HOSTS (host: describe flake.nixosConfigurations.${host}.config)
        """
        expression = expression.replace(
            "ROOT_FLAKE", json.dumps(f"git+file://{ROOT}?dir=flake")
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
                self.assertEqual(
                    repository_path(config["defaultSopsFile"]),
                    f"secrets/hosts/{host}.sops.yaml",
                )

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
                bundle = yaml.safe_load((ROOT / path).read_text())
                actual = {entry["recipient"] for entry in bundle["sops"]["age"]}
                self.assertEqual(actual, expected)

    def test_every_declared_secret_exists_in_its_file(self) -> None:
        for host in HOSTS:
            for name, secret in self.evaluated[host]["secrets"].items():
                with self.subTest(host=host, secret=name):
                    path = ROOT / repository_path(secret["sopsFile"])
                    bundle = yaml.safe_load(path.read_text())
                    if isinstance(bundle, dict):
                        self.assertIn(secret["key"], set(bundle) - {"sops"})
                    self.assertEqual(secret["mode"], "0400")
                    self.assertEqual(secret["path"], f"/run/secrets/{name}")

    def test_no_unit_reads_hand_placed_secrets(self) -> None:
        for host in HOSTS:
            for unit, text in self.evaluated[host]["units"].items():
                with self.subTest(host=host, unit=unit):
                    self.assertNotIn("/persist/secrets", text)

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


if __name__ == "__main__":
    unittest.main()
