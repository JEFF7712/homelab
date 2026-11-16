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
          specialArgs.fleetInventory = import {json.dumps(str(ROOT / "inventory"))} {{ inherit (flake.inputs.nixpkgs) lib; }};
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
        environmentFile = cfg.services.monero.environmentFile;
        torEnabled = cfg.services.tor.enable;
        onionMap = if cfg.homelab.monero.tor.enable then cfg.services.tor.relay.onionServices.monero.map else [];
        onionEnv = if cfg.homelab.monero.tor.enable then cfg.systemd.services.monero-onion-env.script else "";
        requiredAvailGiB = cfg.homelab.monero.requiredAvailGiB;
        minFreeGiB = cfg.homelab.monero.minFreeGiB;
        firewall = cfg.networking.firewall.extraInputRules;
        egress = cfg.networking.nftables.tables.monero-egress.content;
        egressPreCheck = cfg.networking.nftables.preCheckRuleset;
        torUid = cfg.ids.uids.tor;
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


def _chains(content: str) -> dict[str, list[str]]:
    """Map each nftables chain name to its non-empty, stripped rule lines."""
    chains: dict[str, list[str]] = {}
    current: list[str] | None = None
    for raw in content.splitlines():
        line = raw.strip()
        if line.startswith("chain ") and line.endswith("{"):
            current = chains.setdefault(line.split()[1], [])
        elif line == "}":
            current = None
        elif line and current is not None:
            current.append(line)
    return chains


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

    def test_tor_onion_listener_uses_runtime_address(self) -> None:
        result = evaluate("homelab.monero.tor.enable = true;")
        self.assertTrue(result["torEnabled"])
        self.assertEqual(result["environmentFile"], "/run/monero-onion.env")
        self.assertEqual(result["onionMap"][0]["port"], 18084)
        self.assertEqual(result["onionMap"][0]["target"]["addr"], "127.0.0.1")
        self.assertIn(
            "anonymous-inbound=${MONERO_ONION_ADDRESS}:18084", result["extraConfig"]
        )
        self.assertIn("/var/lib/tor/onion/monero/hostname", result["onionEnv"])

    def test_restricted_rpc_serves_lan_wallets_only(self) -> None:
        result = evaluate("")
        # Wallets get the safe restricted listener; daemon control stays
        # on the localhost-only unrestricted listener.
        self.assertEqual(result["rpcAddress"], "127.0.0.1")
        self.assertIn("rpc-restricted-bind-port=18089", result["extraConfig"])
        self.assertIn("disable-rpc-ban=1", result["extraConfig"])
        rule = next(line for line in result["firewall"].splitlines() if "18089" in line)
        self.assertIn("tcp dport 18089 accept", rule)
        for cidr in ("10.0.10.0/24", "10.0.20.0/24", "100.64.0.0/10"):
            self.assertIn(cidr, rule)
        # No bare accept: every 18089 rule carries a source allowlist, so
        # guest IoT and the internet cannot reach wallet RPC.
        for line in result["firewall"].splitlines():
            if "18089" in line:
                self.assertIn("saddr", line)

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

    def test_memory_limits_are_host_tunable(self) -> None:
        defaults = evaluate("")["serviceConfig"]
        self.assertEqual(defaults["MemoryHigh"], "6G")
        self.assertEqual(defaults["MemoryMax"], "8G")
        tuned = evaluate(
            'homelab.monero.memoryHigh = "1536M"; homelab.monero.memoryMax = "2G";'
        )["serviceConfig"]
        self.assertEqual(tuned["MemoryHigh"], "1536M")
        self.assertEqual(tuned["MemoryMax"], "2G")

    def test_requires_io_device(self) -> None:
        expression = f"""
          let
            flake = builtins.getFlake (toString {json.dumps(str(ROOT / "flake"))});
            system = flake.inputs.nixpkgs.lib.nixosSystem {{
              system = "x86_64-linux";
              specialArgs.fleetInventory = import {json.dumps(str(ROOT / "inventory"))} {{ inherit (flake.inputs.nixpkgs) lib; }};
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

    def test_closed_p2p_binds_loopback_and_hides_port(self) -> None:
        result = evaluate("homelab.monero.openP2P = false;")
        self.assertNotIn("dport 18080", result["firewall"])
        self.assertIn("p2p-bind-ip=127.0.0.1", result["extraConfig"])
        self.assertIn("hide-my-port=1", result["extraConfig"])
        # The default keeps the clearnet listener open and advertised.
        default = evaluate("")["extraConfig"]
        self.assertNotIn("p2p-bind-ip", default)
        self.assertNotIn("hide-my-port", default)

    def test_block_additions_are_logged(self) -> None:
        # Per-block HEIGHT lines are the Loki evidence that the node
        # follows the chain tip.
        self.assertIn("log-level=0,blockchain:INFO", evaluate("")["extraConfig"])

    def test_sandbox_additions(self) -> None:
        service = evaluate("")["serviceConfig"]
        self.assertEqual(service["SystemCallArchitectures"], "native")
        self.assertEqual(
            service["SystemCallFilter"], ["@system-service", "~@privileged"]
        )
        # ZMQ RPC aborts monerod without netlink.
        self.assertIn("AF_NETLINK", service["RestrictAddressFamilies"])
        self.assertEqual(service["ProtectProc"], "invisible")
        self.assertEqual(service["CapabilityBoundingSet"], "")
        self.assertTrue(service["NoNewPrivileges"])
        # RandomX JIT-compiles into W+X pages.
        self.assertNotIn("MemoryDenyWriteExecute", service)

    def test_egress_filter_with_resolved_stub(self) -> None:
        result = evaluate(
            "homelab.monero.tor.enable = true; services.resolved.enable = true;"
        )
        chains = _chains(result["egress"])
        self.assertIn("type filter hook output", chains["output"][0])
        self.assertIn("meta skuid monero jump monero", chains["output"])
        self.assertIn(f"meta skuid {result['torUid']} jump tor", chains["output"])

        monero = chains["monero"]
        self.assertEqual(monero[0], "ct state established,related accept")
        allowances = [line for line in monero if line.endswith("accept")]
        self.assertEqual(
            allowances,
            [
                "ct state established,related accept",
                "ip daddr 127.0.0.1 tcp dport 9050 accept",
                "ip daddr { 127.0.0.53 } meta l4proto { tcp, udp } th dport 53 accept",
            ],
        )
        tor = chains["tor"]
        self.assertEqual(
            [line for line in tor if line.endswith("accept")],
            [
                "ct state established,related accept",
                "ip daddr 127.0.0.1 tcp dport 18084 accept",
            ],
        )
        # Each restricted chain logs (rate-limited) and drops every
        # non-public destination for both families after its allowances.
        for name, chain in (("monero", monero), ("tor", tor)):
            tail = chain[len([rule for rule in chain if rule.endswith("accept")]) :]
            self.assertEqual(
                tail,
                [
                    "ip daddr @non-public4 limit rate 6/minute burst 5 packets "
                    f'log prefix "{name}-egress-drop: " level warn',
                    "ip daddr @non-public4 drop",
                    "ip6 daddr @non-public6 limit rate 6/minute burst 5 packets "
                    f'log prefix "{name}-egress-drop: " level warn',
                    "ip6 daddr @non-public6 drop",
                ],
            )
        for cidr in (
            "10.0.0.0/8",
            "100.64.0.0/10",
            "127.0.0.0/8",
            "169.254.0.0/16",
            "172.16.0.0/12",
            "192.168.0.0/16",
        ):
            self.assertIn(cidr, result["egress"])
        for cidr in ("::/127", "fc00::/7", "fe80::/10"):
            self.assertIn(cidr, result["egress"])
        # The build sandbox has no monero user for `nft --check`.
        self.assertIn("skuid nobody", result["egressPreCheck"])

    def test_egress_filter_uses_nameservers_without_resolved(self) -> None:
        result = evaluate(
            "services.resolved.enable = false;"
            ' networking.nameservers = [ "10.0.30.10" "10.0.30.1" "fd00::1" ];'
        )
        chains = _chains(result["egress"])
        self.assertIn(
            "ip daddr { 10.0.30.10, 10.0.30.1 } meta l4proto { tcp, udp } th dport 53 accept",
            chains["monero"],
        )
        # Without Tor there is no SOCKS allowance and no tor chain.
        self.assertNotIn("tor", chains)
        self.assertFalse(any("9050" in line for line in chains["monero"]))

    def test_rejects_non_persist_datadir(self) -> None:
        expression = f"""
          let
            flake = builtins.getFlake (toString {json.dumps(str(ROOT / "flake"))});
            system = flake.inputs.nixpkgs.lib.nixosSystem {{
              system = "x86_64-linux";
              specialArgs.fleetInventory = import {json.dumps(str(ROOT / "inventory"))} {{ inherit (flake.inputs.nixpkgs) lib; }};
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


class MoneroHostTests(unittest.TestCase):
    """The deployed host: homelab-04 runs monerod behind the onion only."""

    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("nix") is None:
            raise unittest.SkipTest("nix is required for host evaluation")
        proc = subprocess.run(
            [
                "nix",
                "eval",
                "--json",
                f"path:{ROOT}?dir=flake#nixosConfigurations.homelab-04.config",
                "--apply",
                "c: { egress = c.networking.nftables.tables.monero-egress.content;"
                " firewall = c.networking.firewall.extraInputRules;"
                " extra = c.services.monero.extraConfig;"
                " resolved = c.services.resolved.enable; }",
            ],
            capture_output=True,
            text=True,
            timeout=600,
            check=True,
        )
        cls.host = json.loads(proc.stdout)

    def test_clearnet_inbound_is_closed(self) -> None:
        self.assertNotIn("dport 18080", self.host["firewall"])
        self.assertIn("p2p-bind-ip=127.0.0.1", self.host["extra"])
        self.assertIn("anonymous-inbound=", self.host["extra"])

    def test_egress_filter_covers_monerod_and_tor(self) -> None:
        chains = _chains(self.host["egress"])
        self.assertIn("monero", chains)
        self.assertIn("tor", chains)
        # The host resolves through the systemd-resolved stub, so monerod
        # needs no LAN resolver allowance.
        self.assertTrue(self.host["resolved"])
        self.assertFalse(any("10.0.30." in line for line in chains["monero"]))


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
