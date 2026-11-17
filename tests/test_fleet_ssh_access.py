from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFWt07ttKX2X+E5CbwL4To1AuwuwuIaKWUePIrAwGrK0"
)
OPERATOR_KEYS = {
    "AAAAC3NzaC1lZDI1NTE5AAAAILW7MVmIGzW4Eq1NJm4+gsGwQ+iL44bIfyAa/wdQ1srQ",
    "AAAAC3NzaC1lZDI1NTE5AAAAIFTXsL6q+O1d29vTd3TtK2F0MYPggS5KSHryvlIFBS1K",
}
AGENT_VM_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICZzDfJQDvL6axeNiY0b+iObfhjkBxzut8cGfcxr9zva"
    " developer@agent-rupan"
)
LAPTOP_OBSERVER_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBGPgdA467aAFq4re73+3yM3Tws4F7GkecGh3LlN6S7/"
    " agent-observer@laptop-nixos"
)
LAPTOP_OBSERVER_SOURCES = "10.0.10.0/24,10.0.20.0/24,100.64.0.0/10"
OBSERVER = "agent-observer"
# Source each host's sshd saw for the agent VM on 2026-11-16: homelab-04 sees
# the VM's routed address, every other host sees homelab-04's masquerade.
AGENT_VM_SOURCES = {"homelab-04": "172.31.10.2"}
AGENT_VM_MASQUERADE = "10.0.30.14"
FLEET = {
    "adguard-netbird-01",
    "homelab-01",
    "homelab-02",
    "homelab-03",
    "homelab-04",
    "homelab-05",
    "nas-01",
}
# Groups that grant root, device, container, key or shared-data access.
PRIVILEGED_GROUPS = {
    "adm",
    "dialout",
    "disk",
    "docker",
    "input",
    "keys",
    "kvm",
    "libvirtd",
    "podman",
    "root",
    "tty",
    "users",
    "video",
    "wheel",
}


@unittest.skipIf(os.getenv("SKIP_NIX_EVAL") == "1", "Nix evaluation disabled")
@unittest.skipUnless(shutil.which("nix"), "Nix required")
class FleetSshAccessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        expression = """
          let
            flake = builtins.getFlake __FLAKE_PATH__;
          in builtins.mapAttrs (_: host: let
            cfg = host.config;
            observer = cfg.users.users.agent-observer;
          in {
            keys = cfg.users.users.rupan.openssh.authorizedKeys.keys;
            rupanKeyFiles =
              map toString cfg.users.users.rupan.openssh.authorizedKeys.keyFiles;
            observer = {
              inherit (observer) isNormalUser isSystemUser group extraGroups home;
              keys = observer.openssh.authorizedKeys.keys;
              keyFiles = map toString observer.openssh.authorizedKeys.keyFiles;
              memberOf = builtins.filter
                (name: builtins.elem "agent-observer" cfg.users.groups.${name}.members)
                (builtins.attrNames cfg.users.groups);
            };
            sudoRules =
              map (rule: { inherit (rule) users groups; }) cfg.security.sudo.extraRules;
            sudoConfig = cfg.security.sudo.configFile;
            doas = cfg.security.doas.enable;
            sudoRs = cfg.security.sudo-rs.enable;
            nixTrusted = cfg.nix.settings.trusted-users;
            passwordlessSudo = !cfg.security.sudo.wheelNeedsPassword;
            ciPrivate = if host.config.sops.secrets ? ci-fleet-deploy-key then {
              inherit (host.config.sops.secrets.ci-fleet-deploy-key) key mode owner group;
              source = toString host.config.sops.secrets.ci-fleet-deploy-key.sopsFile;
            } else null;
          }) flake.nixosConfigurations
        """.replace("__FLAKE_PATH__", json.dumps(f"git+file://{ROOT}?dir=flake"))
        result = subprocess.run(
            ["nix", "eval", "--impure", "--json", "--expr", expression],
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        if result.returncode:
            raise AssertionError(result.stderr)
        cls.hosts = json.loads(result.stdout)

    def test_every_fleet_host_is_covered(self) -> None:
        self.assertEqual(set(self.hosts), FLEET)

    def test_rupan_no_longer_trusts_the_agent_vm_key(self) -> None:
        vm_key = AGENT_VM_KEY.split()[1]
        for host, settings in self.hosts.items():
            with self.subTest(host=host):
                self.assertFalse(any(vm_key in key for key in settings["keys"]))
                self.assertEqual(settings["rupanKeyFiles"], [])
                # Two operator keys, the CI fleet key and the legacy NAS key.
                self.assertEqual(len(settings["keys"]), 4)

    def test_observer_is_an_unprivileged_login(self) -> None:
        for host, settings in self.hosts.items():
            with self.subTest(host=host):
                observer = settings["observer"]
                self.assertTrue(observer["isNormalUser"])
                self.assertFalse(observer["isSystemUser"])
                self.assertEqual(observer["group"], OBSERVER)
                self.assertEqual(observer["extraGroups"], ["systemd-journal"])
                # NixOS mirrors extraGroups into group members; nothing else.
                self.assertEqual(observer["memberOf"], ["systemd-journal"])
                groups = {observer["group"], *observer["extraGroups"]}
                self.assertFalse(groups & PRIVILEGED_GROUPS)
                self.assertFalse(observer["home"].startswith("/persist"))

    def test_observer_has_no_privilege_escalation_path(self) -> None:
        for host, settings in self.hosts.items():
            with self.subTest(host=host):
                for rule in settings["sudoRules"]:
                    self.assertNotIn(OBSERVER, rule["users"])
                    self.assertFalse(
                        {group.lstrip("%") for group in rule["groups"]}
                        & {OBSERVER, "systemd-journal"}
                    )
                self.assertNotIn(OBSERVER, settings["sudoConfig"])
                self.assertNotIn("systemd-journal", settings["sudoConfig"])
                self.assertFalse(settings["doas"])
                self.assertFalse(settings["sudoRs"])
                self.assertNotIn(OBSERVER, settings["nixTrusted"])
                self.assertNotIn("@" + OBSERVER, settings["nixTrusted"])

    def test_observer_trusts_only_the_restricted_vm_and_laptop_keys(
        self,
    ) -> None:
        for host, settings in self.hosts.items():
            with self.subTest(host=host):
                source = AGENT_VM_SOURCES.get(host, AGENT_VM_MASQUERADE)
                observer = settings["observer"]
                self.assertEqual(observer["keyFiles"], [])
                self.assertEqual(
                    observer["keys"],
                    [
                        f'restrict,pty,from="{source}" {AGENT_VM_KEY}',
                        f'restrict,pty,from="{LAPTOP_OBSERVER_SOURCES}" {LAPTOP_OBSERVER_KEY}',
                    ],
                )

    def test_legacy_deploy_key_requires_exact_trusted_source_and_restrict(self) -> None:
        for host, settings in self.hosts.items():
            with self.subTest(host=host):
                matching = [key for key in settings["keys"] if DEPLOY_KEY in key]
                self.assertEqual(len(matching), 1)
                key = matching[0]
                self.assertTrue(key.startswith('restrict,from="'))
                sources = re.search(r'from="([^"]+)"', key)
                self.assertIsNotNone(sources)
                self.assertEqual(
                    set(sources.group(1).split(",")), {"10.0.30.14", "10.0.30.20"}
                )
                for override in (
                    "pty",
                    "agent-forwarding",
                    "port-forwarding",
                    "X11-forwarding",
                    "user-rc",
                ):
                    self.assertNotIn("," + override + ",", key)
                self.assertTrue(settings["passwordlessSudo"])

    def test_deliberate_operator_workstation_keys_remain_unrestricted(self) -> None:
        for host, settings in self.hosts.items():
            with self.subTest(host=host):
                for public_key in OPERATOR_KEYS:
                    matching = [key for key in settings["keys"] if public_key in key]
                    self.assertEqual(len(matching), 1)
                    self.assertTrue(matching[0].startswith("ssh-ed25519 "))

    def test_ci_identity_cannot_authenticate_from_other_infrastructure_or_guest(
        self,
    ) -> None:
        from ipaddress import ip_address, ip_network

        dedicated = (ROOT / "config/ci/fleet-deploy.pub").read_text().split()[1]
        for host, settings in self.hosts.items():
            with self.subTest(host=host):
                matching = [key for key in settings["keys"] if dedicated in key]
                self.assertEqual(len(matching), 1)
                self.assertTrue(matching[0].startswith('restrict,from="'))
                sources = re.search(r'from="([^"]+)"', matching[0])
                self.assertIsNotNone(sources)
                networks = [ip_network(s) for s in sources.group(1).split(",")]
                self.assertTrue(any(ip_address("10.0.30.14") in n for n in networks))
                for excluded in (
                    "10.0.30.11",
                    "10.0.30.15",
                    "10.0.30.20",
                    "172.31.10.2",
                ):
                    self.assertFalse(any(ip_address(excluded) in n for n in networks))
                self.assertEqual(
                    any(ip_address("172.17.0.2") in n for n in networks),
                    host == "homelab-04",
                )

    def test_private_identity_is_installed_only_on_trusted_ci_host(self) -> None:
        for host, settings in self.hosts.items():
            with self.subTest(host=host):
                if host != "homelab-04":
                    self.assertIsNone(settings["ciPrivate"])
                    continue
                secret = settings["ciPrivate"]
                self.assertEqual(secret["key"], "private_key")
                self.assertEqual(secret["mode"], "0400")
                self.assertEqual(secret["owner"], "root")
                self.assertEqual(secret["group"], "root")
                self.assertTrue(
                    secret["source"].endswith("/secrets/ci-fleet-deploy.sops.json")
                )


if __name__ == "__main__":
    unittest.main()
