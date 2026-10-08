from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "flake/modules/zot-registry.nix"


class ZotRegistryModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("nix") is None:
            raise unittest.SkipTest("nix is required for module evaluation")

        expression = f"""
          let
            flake = builtins.getFlake (toString {json.dumps(str(ROOT / "flake"))});
            system = flake.inputs.nixpkgs.lib.nixosSystem {{
              system = "x86_64-linux";
              modules = [
                flake.inputs.impermanence.nixosModules.impermanence
                {json.dumps(str(MODULE))}
                ({{ ... }}: {{
                  boot.isContainer = true;
                  system.stateVersion = "26.05";
                  services.homelab-zot-registry = {{
                    enable = true;
                    acmeEmail = "registry-test@example.invalid";
                  }};
                  fileSystems."/persist" = {{
                    device = "persist";
                    fsType = "none";
                  }};
                  fileSystems."/tank/registry" = {{
                    device = "tank/registry";
                    fsType = "zfs";
                  }};
                }})
              ];
            }};
            cfg = system.config;
          in {{
            packageVersion = cfg.services.homelab-zot-registry.package.version;
            packageOutPath = toString cfg.services.homelab-zot-registry.package;
            packageDrv = cfg.services.homelab-zot-registry.package.drvPath;
            storageSetup = builtins.elemAt cfg.systemd.services.zot.serviceConfig.ExecStartPre 0;
            prepareConfig = toString (
              builtins.elemAt cfg.systemd.services.zot.serviceConfig.ExecStartPre 1
            );
            prepareConfigDrv = (
              builtins.elemAt cfg.systemd.services.zot.serviceConfig.ExecStartPre 1
            ).drvPath;
            service = {{
              inherit (cfg.systemd.services.zot)
                after
                requires
                serviceConfig
                unitConfig
                ;
            }};
            nginx = {{
              inherit (cfg.services.nginx.virtualHosts."registry.rupan.dev")
                onlySSL
                useACMEHost
                ;
              proxyPass = cfg.services.nginx.virtualHosts."registry.rupan.dev".locations."/".proxyPass;
            }};
            acme = {{
              inherit (cfg.security.acme.certs."registry.rupan.dev")
                credentialFiles
                dnsProvider
                reloadServices
                ;
            }};
            firewall = cfg.networking.firewall.extraInputRules;
            hosts = cfg.networking.hosts;
            persistence = map (
              directory: directory.directory
            ) cfg.environment.persistence."/persist".directories;
            exporter = cfg.services.prometheus.exporters.node;
            metricsService = cfg.systemd.services.zot-platform-metrics.serviceConfig;
            metricsTimer = cfg.systemd.timers.zot-platform-metrics.timerConfig;
          }}
        """
        completed = subprocess.run(
            ["nix", "eval", "--impure", "--json", "--expr", expression],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr)
        cls.evaluated = json.loads(completed.stdout)
        realised = subprocess.run(
            [
                "nix-store",
                "--realise",
                cls.evaluated["packageDrv"],
                cls.evaluated["prepareConfigDrv"],
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if realised.returncode != 0:
            raise RuntimeError(realised.stderr)

    def test_release_and_service_are_pinned_and_mount_gated(self) -> None:
        service = self.evaluated["service"]

        self.assertEqual(self.evaluated["packageVersion"], "2.1.20")
        self.assertEqual(service["requires"], ["tank-registry.mount"])
        self.assertIn("tank-registry.mount", service["after"])
        self.assertEqual(service["unitConfig"]["RequiresMountsFor"], "/tank/registry")
        self.assertEqual(
            service["unitConfig"]["ConditionPathIsMountPoint"], "/tank/registry"
        )
        self.assertEqual(service["serviceConfig"]["Restart"], "on-failure")
        self.assertEqual(service["serviceConfig"]["ReadWritePaths"], ["/tank/registry"])
        self.assertTrue(self.evaluated["storageSetup"].startswith("+/nix/store/"))

    def test_secrets_are_loaded_as_runtime_credentials(self) -> None:
        service_config = self.evaluated["service"]["serviceConfig"]

        self.assertEqual(
            service_config["LoadCredential"],
            [
                "htpasswd:/persist/zot/htpasswd",
                "access-control.json:/persist/zot/access-control.json",
            ],
        )
        self.assertIn("/run/zot/config.json", service_config["ExecStart"])
        self.assertNotIn("/nix/store", " ".join(service_config["LoadCredential"]))

    def test_tls_proxy_and_dns01_use_runtime_token(self) -> None:
        nginx = self.evaluated["nginx"]
        acme = self.evaluated["acme"]

        self.assertTrue(nginx["onlySSL"])
        self.assertEqual(nginx["useACMEHost"], "registry.rupan.dev")
        self.assertEqual(nginx["proxyPass"], "http://127.0.0.1:5000")
        self.assertEqual(acme["dnsProvider"], "cloudflare")
        self.assertEqual(
            acme["credentialFiles"]["CF_DNS_API_TOKEN_FILE"],
            "/persist/zot/cloudflare-dns-api-token",
        )
        self.assertEqual(acme["reloadServices"], ["nginx.service"])

    def test_firewall_is_allowlisted_and_acme_state_persists(self) -> None:
        firewall = self.evaluated["firewall"]

        for network in (
            "10.0.10.0/24",
            "10.0.30.0/24",
            "10.42.0.0/16",
            "100.64.0.0/10",
        ):
            self.assertIn(f"ip saddr {network} tcp dport 443 accept", firewall)
        self.assertNotIn("networking.firewall.allowedTCPPorts", firewall)
        self.assertIn("/var/lib/acme", self.evaluated["persistence"])

    def test_existing_node_exporter_stack_covers_service_filesystem_and_status(
        self,
    ) -> None:
        exporter = self.evaluated["exporter"]

        self.assertTrue(exporter["enable"])
        self.assertEqual(exporter["port"], 9100)
        self.assertIn("filesystem", exporter["enabledCollectors"])
        self.assertIn("systemd", exporter["enabledCollectors"])
        self.assertIn("textfile", exporter["enabledCollectors"])
        self.assertIn(
            "--collector.textfile.directory=/var/lib/zot-monitoring",
            exporter["extraFlags"],
        )
        self.assertIn(
            "ip saddr 10.0.30.0/24 tcp dport 9100 accept",
            self.evaluated["firewall"],
        )
        self.assertNotIn(
            "ip saddr 100.64.0.0/10 tcp dport 9100 accept",
            self.evaluated["firewall"],
        )
        self.assertIn(
            "zot-monitoring", self.evaluated["metricsService"]["StateDirectory"]
        )
        self.assertEqual(self.evaluated["metricsTimer"]["OnUnitActiveSec"], "1h")

        module = MODULE.read_text()
        self.assertIn("homelab_zot_tls_certificate_expiry_timestamp_seconds", module)
        self.assertIn("homelab_zot_%s_last_success_timestamp_seconds", module)
        self.assertIn("for status in backup import", module)

    def test_registry_name_resolves_locally_on_hosts_and_nodes(self) -> None:
        self.assertEqual(self.evaluated["hosts"]["10.0.30.20"], ["registry.rupan.dev"])

        k3s_server = (ROOT / "flake/modules/k3s-server.nix").read_text()
        self.assertIn('"10.0.30.20" = [', k3s_server)
        self.assertIn('"registry.rupan.dev"', k3s_server)

    def test_backup_sandbox_can_write_its_success_marker(self) -> None:
        nas_data = (ROOT / "flake/modules/nas-data.nix").read_text()

        self.assertIn('"/mnt/backup-2tb/registry-restic"', nas_data)
        self.assertIn('"/persist/zot/status"', nas_data)
        self.assertIn("restic forget --prune", nas_data)

    def test_background_gc_reclaims_blobs_daily(self) -> None:
        module = MODULE.read_text()

        self.assertIn("gc = true;", module)
        self.assertIn('gcInterval = "24h";', module)

    def test_runtime_assembly_verifies_valid_config_and_rejects_anonymous_access(
        self,
    ) -> None:
        package = Path(self.evaluated["packageOutPath"])
        interpreter = subprocess.run(
            ["patchelf", "--print-interpreter", str(package / "bin/zot")],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertTrue(interpreter.startswith("/nix/store/"), interpreter)

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            credentials = temporary / "credentials"
            runtime = temporary / "runtime"
            credentials.mkdir()
            runtime.mkdir()
            (credentials / "htpasswd").write_text(
                "node:$2y$05$Av8c1JZweJwJwkPQSeU5WOnjhzd/X0fE9B7hRocxGd1NQo78ZwH0i\n"
            )
            (credentials / "access-control.json").write_text(
                json.dumps(
                    {
                        "repositories": {
                            "**": {
                                "defaultPolicy": [],
                                "policies": [{"users": ["node"], "actions": ["read"]}],
                            }
                        },
                        "adminPolicy": {
                            "users": ["maintenance"],
                            "actions": ["read", "create", "update", "delete"],
                        },
                    }
                )
            )
            environment = {
                "CREDENTIALS_DIRECTORY": str(credentials),
                "RUNTIME_DIRECTORY": str(runtime),
            }
            subprocess.run(
                [self.evaluated["prepareConfig"]],
                check=True,
                env=environment,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [str(package / "bin/zot"), "verify", str(runtime / "config.json")],
                check=True,
                capture_output=True,
                text=True,
            )

            rendered = json.loads((runtime / "config.json").read_text())
            self.assertEqual(rendered["http"]["address"], "127.0.0.1")
            self.assertEqual(rendered["http"]["compat"], ["docker2s2"])
            self.assertEqual(
                rendered["http"]["accessControl"]["repositories"]["**"][
                    "defaultPolicy"
                ],
                [],
            )
            self.assertEqual(
                rendered["http"]["auth"]["htpasswd"]["path"],
                str(credentials / "htpasswd"),
            )
            self.assertTrue(rendered["storage"]["gc"])
            self.assertTrue(rendered["storage"]["fastRestart"])
            self.assertTrue(rendered["extensions"]["ui"]["enable"])

            rejected_credentials = temporary / "rejected-credentials"
            rejected_runtime = temporary / "rejected-runtime"
            rejected_credentials.mkdir()
            rejected_runtime.mkdir()
            (rejected_credentials / "htpasswd").write_text("not-empty\n")
            (rejected_credentials / "access-control.json").write_text(
                json.dumps(
                    {
                        "repositories": {
                            "**": {
                                "defaultPolicy": [],
                                "anonymousPolicy": ["read"],
                            }
                        }
                    }
                )
            )
            completed = subprocess.run(
                [self.evaluated["prepareConfig"]],
                env={
                    "CREDENTIALS_DIRECTORY": str(rejected_credentials),
                    "RUNTIME_DIRECTORY": str(rejected_runtime),
                },
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse((rejected_runtime / "config.json").exists())


if __name__ == "__main__":
    unittest.main()
