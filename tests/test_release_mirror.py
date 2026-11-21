from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(os.getenv("SKIP_NIX_EVAL") == "1", "Nix evaluation disabled")
@unittest.skipUnless(shutil.which("nix"), "Nix required")
class ReleaseMirrorIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        expression = f"""
          let
            flake = builtins.getFlake {json.dumps(f"git+{ROOT.as_uri()}?dir=flake")};
            cfg = flake.nixosConfigurations.homelab-04.config;
            source = flake.inputs.forgejo-release-mirror;
            package = source.packages.x86_64-linux.default;
          in {{
            command = cfg.systemd.services.github-release-mirror.serviceConfig.ExecStart;
            service = cfg.systemd.services.github-release-mirror.serviceConfig;
            timer = cfg.systemd.timers.github-release-mirror.timerConfig;
            wantedBy = cfg.systemd.timers.github-release-mirror.wantedBy;
            packagePath = toString package;
            packageDrv = package.drvPath;
            version = package.version;
            revision = source.rev;
            sharesNixpkgs = source.inputs.nixpkgs.outPath == flake.inputs.nixpkgs.outPath;
            mirrorServices = builtins.filter
              (name: builtins.match ".*release-mirror.*" name != null)
              (builtins.attrNames cfg.systemd.services);
          }}
        """
        result = subprocess.run(
            ["nix", "eval", "--impure", "--json", "--expr", expression],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if result.returncode:
            raise RuntimeError(result.stderr)
        cls.config = json.loads(result.stdout)

    def test_service_uses_the_pinned_release_and_explicit_source(self) -> None:
        self.assertEqual(self.config["version"], "0.1.0")
        self.assertEqual(
            self.config["revision"], "b39cb45f3d469662afaaadc9cc13de0dfc4eeec5"
        )
        self.assertTrue(self.config["sharesNixpkgs"])
        self.assertEqual(self.config["mirrorServices"], ["github-release-mirror"])
        self.assertEqual(
            shlex.split(self.config["command"]),
            [
                self.config["packagePath"] + "/bin/forgejo-release-mirror",
                "--forgejo-origin",
                "http://git.internal:3000",
                "--source-repository",
                "JEFF7712/nix-agent",
                "--destination-repository",
                "JEFF7712/nix-agent",
                "--minimum-version",
                "0.13.0",
            ],
        )

    def test_existing_credentials_schedule_and_service_isolation_are_preserved(
        self,
    ) -> None:
        service = self.config["service"]
        self.assertEqual(service["EnvironmentFile"], "/run/secrets/repo-monitor.env")
        self.assertEqual(service["Type"], "oneshot")
        self.assertEqual(service["TimeoutStartSec"], "15min")
        self.assertEqual(service["UMask"], "0077")
        self.assertEqual(service["ProtectSystem"], "strict")
        for setting in (
            "DynamicUser",
            "NoNewPrivileges",
            "ProtectHome",
            "PrivateTmp",
            "PrivateDevices",
            "ProtectKernelTunables",
            "ProtectKernelModules",
            "ProtectControlGroups",
        ):
            self.assertTrue(service[setting], setting)
        self.assertEqual(service["RestrictAddressFamilies"], ["AF_INET", "AF_INET6"])
        self.assertEqual(
            self.config["timer"],
            {
                "OnCalendar": "*:0/5",
                "RandomizedDelaySec": "30s",
                "Persistent": True,
            },
        )
        self.assertEqual(self.config["wantedBy"], ["timers.target"])

    def test_installed_service_executable_supports_preview_and_json_errors(
        self,
    ) -> None:
        subprocess.run(
            ["nix", "build", "--no-link", self.config["packageDrv"] + "^out"],
            capture_output=True,
            text=True,
            check=True,
            timeout=300,
        )
        command = shlex.split(self.config["command"])
        version = subprocess.run(
            [command[0], "--version"], text=True, capture_output=True, check=True
        )
        self.assertEqual(version.stdout.strip(), "forgejo-release-mirror 0.1.0")
        help_result = subprocess.run(
            [command[0], "--help"], text=True, capture_output=True, check=True
        )
        self.assertIn("--dry-run", help_result.stdout)
        environment = os.environ.copy()
        for variable in ("FORGEJO_READ_TOKEN", "GITHUB_TOKEN"):
            environment.pop(variable, None)
        result = subprocess.run(
            [*command, "--dry-run"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        error = json.loads(result.stderr)
        self.assertEqual(error["status"], "failed")
        self.assertIn("FORGEJO_READ_TOKEN", error["error"])
