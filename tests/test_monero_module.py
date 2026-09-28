from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "flake/modules/monero.nix"
IO_DEVICE = "/dev/disk/by-id/nvme-test-part2"


def evaluate(extra_config: str) -> dict:
    expression = f"""
      let
        flake = builtins.getFlake (toString {json.dumps(str(ROOT / "flake"))});
        system = flake.inputs.nixpkgs.lib.nixosSystem {{
          system = "x86_64-linux";
          modules = [
            {json.dumps(str(MODULE))}
            ({{ ... }}: {{
              boot.isContainer = true;
              system.stateVersion = "26.05";
              networking.hostName = "monero-test";
              homelab.monero.enable = true;
              homelab.monero.ioDevice = {json.dumps(IO_DEVICE)};
              {extra_config}
              fileSystems."/persist" = {{
                device = "persist";
                fsType = "none";
              }};
            }})
          ];
        }};
        cfg = system.config;
      in {{
        dataDir = cfg.services.monero.dataDir;
        prune = cfg.services.monero.prune;
        rpcAddress = cfg.services.monero.rpc.address;
        extraConfig = cfg.services.monero.extraConfig;
        requiredAvailGiB = cfg.homelab.monero.requiredAvailGiB;
        minFreeGiB = cfg.homelab.monero.minFreeGiB;
        firewall = cfg.networking.firewall.extraInputRules;
        preStart = cfg.systemd.services.monero.preStart;
        serviceConfig = cfg.systemd.services.monero.serviceConfig;
        diskCheck = toString cfg.systemd.services.monero-disk-check.serviceConfig.ExecStart;
        timer = cfg.systemd.timers.monero-disk-check.timerConfig.OnCalendar;
        tmpfiles = cfg.systemd.tmpfiles.rules;
        exporter = {{
          enable = cfg.services.prometheus.exporters.node.enable;
          port = cfg.services.prometheus.exporters.node.port;
          collectors = cfg.services.prometheus.exporters.node.enabledCollectors;
          openFirewall = cfg.services.prometheus.exporters.node.openFirewall;
        }};
      }}
    """
    proc = subprocess.run(
        ["nix", "eval", "--impure", "--json", "--expr", expression],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


class MoneroModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("nix") is None:
            raise unittest.SkipTest("nix is required for module evaluation")

    def test_full_node_defaults(self) -> None:
        result = evaluate("")
        self.assertEqual(result["dataDir"], "/persist/monero")
        self.assertFalse(result["prune"])
        self.assertEqual(result["rpcAddress"], "127.0.0.1")
        self.assertIn("tcp dport 18080 accept", result["firewall"])
        self.assertEqual(result["serviceConfig"]["CPUQuota"], "300%")
        self.assertEqual(result["serviceConfig"]["TimeoutStopSec"], "300s")
        self.assertEqual(result["timer"], "daily")
        self.assertTrue(any("/persist/monero" in rule for rule in result["tmpfiles"]))
        self.assertIn("p2p-bind-port=18080", result["extraConfig"])

    def test_preflight_gates_on_available_space(self) -> None:
        result = evaluate("")
        # A failed preflight must block daemon startup, not just warn.
        self.assertIn("monero-storage-preflight", result["preStart"])
        # The gate runs before the upstream config-generation preStart.
        self.assertLess(
            result["preStart"].index("monero-storage-preflight"),
            result["preStart"].index("envsubst"),
        )
        # First sync requires the full budget as available space; an
        # existing database only needs headroom. Total capacity must not
        # be the gate: a mostly-full filesystem has a large total.
        module = MODULE.read_text()
        self.assertIn("lmdb/data.mdb", module)
        self.assertIn("requiredAvailGiB", module)
        self.assertIn("--output=avail", module)
        self.assertNotIn("output=used", module)
        # The daily check runs the same gate.
        self.assertIn("monero-storage-preflight", result["diskCheck"])

    def test_io_limits_are_enforceable_without_ioprio(self) -> None:
        result = evaluate("")
        # The host NVMe scheduler is `none`, which ignores ioprio weights,
        # so scheduler-priority knobs would be decoration. Bandwidth caps
        # are enforced by the cgroup I/O controller regardless of elevator.
        self.assertNotIn("IOSchedulingClass", result["serviceConfig"])
        self.assertNotIn("IOSchedulingPriority", result["serviceConfig"])
        self.assertEqual(
            result["serviceConfig"]["IOReadBandwidthMax"],
            f"{IO_DEVICE} 250M",
        )
        self.assertEqual(
            result["serviceConfig"]["IOWriteBandwidthMax"],
            f"{IO_DEVICE} 150M",
        )

    def test_requires_io_device(self) -> None:
        expression = f"""
          let
            flake = builtins.getFlake (toString {json.dumps(str(ROOT / "flake"))});
            system = flake.inputs.nixpkgs.lib.nixosSystem {{
              system = "x86_64-linux";
              modules = [
                {json.dumps(str(MODULE))}
                ({{ ... }}: {{
                  boot.isContainer = true;
                  system.stateVersion = "26.05";
                  homelab.monero.enable = true;
                }})
              ];
            }};
          in system.config.system.build.toplevel.drvPath
        """
        proc = subprocess.run(
            ["nix", "eval", "--impure", "--raw", "--expr", expression],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ioDevice", proc.stderr)

    def test_host_metrics_cover_unit_and_filesystem(self) -> None:
        result = evaluate("")
        self.assertTrue(result["exporter"]["enable"])
        self.assertEqual(result["exporter"]["port"], 9101)
        self.assertIn("systemd", result["exporter"]["collectors"])
        self.assertIn("filesystem", result["exporter"]["collectors"])
        self.assertFalse(result["exporter"]["openFirewall"])
        self.assertIn("tcp dport 9101 accept", result["firewall"])

    def test_rejects_non_persist_datadir(self) -> None:
        expression = f"""
          let
            flake = builtins.getFlake (toString {json.dumps(str(ROOT / "flake"))});
            system = flake.inputs.nixpkgs.lib.nixosSystem {{
              system = "x86_64-linux";
              modules = [
                {json.dumps(str(MODULE))}
                ({{ ... }}: {{
                  boot.isContainer = true;
                  system.stateVersion = "26.05";
                  homelab.monero.enable = true;
                  homelab.monero.ioDevice = {json.dumps(IO_DEVICE)};
                  homelab.monero.dataDir = "/var/lib/monero";
                }})
              ];
            }};
          in system.config.system.build.toplevel.drvPath
        """
        proc = subprocess.run(
            ["nix", "eval", "--impure", "--raw", "--expr", expression],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("/persist", proc.stderr)


class MoneroPreflightBehaviorTests(unittest.TestCase):
    """Execute the module's storage preflight body against a stubbed df."""

    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("nix") is None:
            raise unittest.SkipTest("nix is required to read module defaults")
        if shutil.which("bash") is None:
            raise unittest.SkipTest("bash is required to execute the preflight")
        result = evaluate("")
        cls.required_avail = result["requiredAvailGiB"]
        cls.min_free = result["minFreeGiB"]

    def _render(self, data_dir: str) -> str:
        src = MODULE.read_text()
        marker = 'pkgs.writeShellScript "monero-storage-preflight"'
        body_start = src.index("''", src.index(marker)) + 2
        body_end = src.index("\n  '';", body_start)
        body = src[body_start:body_end]
        body = body.replace("${lib.escapeShellArg cfg.dataDir}", f"'{data_dir}'")
        body = body.replace("${toString cfg.minFreeGiB}", str(self.min_free))
        body = body.replace(
            "${toString cfg.requiredAvailGiB}", str(self.required_avail)
        )
        body = body.replace("${pkgs.coreutils}/bin/", "")
        return body.replace("''${", "${")

    def _run(self, fresh: bool, avail_gib: int) -> subprocess.CompletedProcess:
        tmp = Path(tempfile.mkdtemp(prefix="monero-preflight-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        data_dir = tmp / "monero"
        data_dir.mkdir()
        if not fresh:
            lmdb = data_dir / "lmdb"
            lmdb.mkdir()
            (lmdb / "data.mdb").touch()
        script = tmp / "preflight.sh"
        script.write_text(self._render(str(data_dir)))
        stub = tmp / "bin"
        stub.mkdir()
        (stub / "df").write_text(
            "#!/usr/bin/env bash\n"
            'if [ "$1" = "--output=avail" ]; then\n'
            '  echo Avail; echo "$STUB_AVAIL_KB"\n'
            "else\n"
            "  exit 2\n"
            "fi\n"
        )
        os.chmod(stub / "df", 0o755)
        env = dict(os.environ)
        env["PATH"] = f"{stub}{os.pathsep}{env['PATH']}"
        env["STUB_AVAIL_KB"] = str(avail_gib * 1024 * 1024)
        return subprocess.run(
            ["bash", str(script)],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
            check=False,
        )

    def test_fresh_database_rejects_mostly_full_filesystem(self) -> None:
        proc = self._run(fresh=True, avail_gib=100)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("first sync", proc.stderr)

    def test_fresh_database_passes_at_budget_boundary(self) -> None:
        self.assertEqual(
            self._run(fresh=True, avail_gib=self.required_avail).returncode, 0
        )
        self.assertEqual(self._run(fresh=True, avail_gib=700).returncode, 0)

    def test_existing_database_passes_above_floor(self) -> None:
        self.assertEqual(self._run(fresh=False, avail_gib=100).returncode, 0)
        self.assertEqual(self._run(fresh=False, avail_gib=self.min_free).returncode, 0)

    def test_existing_database_fails_below_floor(self) -> None:
        proc = self._run(fresh=False, avail_gib=30)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("existing database", proc.stderr)


if __name__ == "__main__":
    unittest.main()
