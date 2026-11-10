import json
import subprocess
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock

from scripts.deploy_fleet import FLEET_HOSTS, FleetDeploymentError, parse_targets
from scripts.fleet_reboot import (
    BOOT_PARTS,
    NIGHTLY,
    FleetRebooter,
    main,
    reboot_order,
)

IP_TO_NAME = {host.ip: name for name, host in FLEET_HOSTS.items()}
OLD = "/nix/store/" + "a" * 32 + "-linux-6.18.57"
NEW = "/nix/store/" + "b" * 32 + "-linux-6.18.62"
# 03:30 America/Chicago in November (CST, UTC-6).
IN_WINDOW = datetime(2026, 11, 11, 9, 30, tzinfo=UTC)


class Fleet:
    """Hosts that boot the installed system when a reboot is scheduled."""

    def __init__(self, pending: set[str]) -> None:
        self.pending = set(pending)
        self.down: dict[str, int] = {}
        self.boots_old: set[str] = set()
        self.stale_profile: set[str] = set()
        self.backup = ("success", 3500.0)
        self.busy: dict[str, str] = {}
        self.pools = "all pools are healthy"
        self.commands: list[tuple[str, str]] = []

    def __call__(self, argv, **options):
        if argv[0] == "kubectl":
            self.commands.append(("kubectl", " ".join(argv[1:3])))
            if argv[1:3] == ["get", "nodes"]:
                ready = [{"type": "Ready", "status": "True"}]
                return self.ok(
                    json.dumps(
                        {
                            "items": [
                                {
                                    "metadata": {"name": name},
                                    "status": {"conditions": ready},
                                }
                                for name in FLEET_HOSTS
                                if name.startswith("homelab-")
                            ]
                        }
                    )
                )
            return self.ok("")
        assert argv[0] == "ssh", argv
        name = IP_TO_NAME[argv[-2].split("@")[1]]
        remote = argv[-1]
        self.commands.append((name, remote))
        if remote == "true":
            if self.down.get(name):
                self.down[name] -= 1
                return subprocess.CompletedProcess(argv, 255, "", "down")
            return self.ok("")
        if "homelab-fleet-reboot" in remote:
            # Down for two probes, then booted.
            self.down[name] = 2
            if name not in self.boots_old:
                self.pending.discard(name)
            return self.ok("")
        if remote.startswith("readlink -f /nix/var/nix/profiles/system"):
            profile = OLD if name in self.stale_profile else NEW
            return self.ok(f"{profile}\n{NEW}\n")
        if remote.startswith("readlink -f /run/booted-system"):
            booted = OLD if name in self.pending else NEW
            return self.ok("\n".join(f"{booted}\n{NEW}" for _ in BOOT_PARTS))
        if remote.startswith("ls /run/current-system/kernel-modules"):
            return self.ok("6.18.62\n")
        if remote.startswith("systemctl --failed"):
            return self.ok("")
        if remote.startswith("systemctl is-active"):
            units = remote.split()[2:]
            return self.ok("\n".join(self.busy.get(unit, "inactive") for unit in units))
        if remote.startswith("systemctl show platform-backup"):
            result, age = self.backup
            return self.ok(
                f"Result={result}\nExecMainExitTimestampMonotonic=1000000000\n"
                f"{1000 + age} 123.0\n"
            )
        if remote == "sudo -n zpool status -x":
            return self.ok(self.pools + "\n")
        if "homelab-reboot-watch" in remote:
            return self.ok("")
        raise AssertionError(f"unexpected command on {name}: {remote}")

    @staticmethod
    def ok(stdout: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, stdout, "")

    def reboots(self) -> list[str]:
        return [
            name for name, remote in self.commands if "homelab-fleet-reboot" in remote
        ]


class FleetRebootTest(unittest.TestCase):
    def rebooter(
        self, fleet: Fleet, now: datetime = IN_WINDOW, **kwargs
    ) -> FleetRebooter:
        rebooter = FleetRebooter(
            parse_targets(None),
            runner=fleet,
            sleeper=lambda seconds: None,
            notifier=MagicMock(return_value=True),
            clock_now=lambda: now,
            **kwargs,
        )
        # Deployment must never run from here; the host-type probes have
        # their own tests.
        rebooter.execute = MagicMock(side_effect=AssertionError("deployed"))
        rebooter.deploy_host = MagicMock(side_effect=AssertionError("deployed"))
        rebooter.wait_node_ready = MagicMock()
        rebooter.wait_postgres_ready = MagicMock()
        rebooter.verify_dns_appliance = MagicMock()
        rebooter.verify_nas_appliance = MagicMock()
        rebooter.verify_nas_datasets = MagicMock()
        return rebooter

    def test_window_is_local_time_through_daylight_saving(self) -> None:
        # 08:30 UTC is 02:30 in winter and 03:30 in summer, so the crons
        # run at 08 and 09 UTC and the window decides.
        self.assertFalse(NIGHTLY.open(datetime(2026, 1, 15, 8, 30, tzinfo=UTC)))
        self.assertTrue(NIGHTLY.open(datetime(2026, 1, 15, 9, 30, tzinfo=UTC)))
        self.assertTrue(NIGHTLY.open(datetime(2026, 7, 15, 8, 30, tzinfo=UTC)))
        self.assertFalse(NIGHTLY.open(datetime(2026, 7, 15, 10, 0, tzinfo=UTC)))
        self.assertTrue(NIGHTLY.may_start(datetime(2026, 1, 15, 10, 29, tzinfo=UTC)))
        self.assertFalse(NIGHTLY.may_start(datetime(2026, 1, 15, 10, 30, tzinfo=UTC)))

    def test_order_puts_the_nas_and_then_the_ci_host_last(self) -> None:
        self.assertEqual(
            [host.name for host in reboot_order(parse_targets(None))],
            [
                "homelab-02",
                "homelab-03",
                "homelab-01",
                "homelab-05",
                "adguard-netbird-01",
                "nas-01",
                "homelab-04",
            ],
        )

    def test_outside_the_window_it_does_nothing(self) -> None:
        fleet = Fleet(set(FLEET_HOSTS))
        rebooter = self.rebooter(fleet, now=datetime(2026, 11, 11, 18, tzinfo=UTC))
        self.assertEqual(rebooter.maintain()["status"], "outside-window")
        self.assertEqual(fleet.commands, [])

    def test_reboots_only_pending_hosts(self) -> None:
        fleet = Fleet({"homelab-02", "homelab-01", "nas-01"})
        rebooter = self.rebooter(fleet)
        result = rebooter.maintain()
        self.assertEqual(fleet.reboots(), ["homelab-02", "homelab-01", "nas-01"])
        statuses = {entry["host"]: entry["status"] for entry in result["hosts"]}
        self.assertEqual(statuses["homelab-03"], "current")
        self.assertEqual(statuses["nas-01"], "rebooted")
        # k3s nodes are drained before and uncordoned after; Postgres
        # primaries are checked after the stateful node.
        kubectl = [remote for name, remote in fleet.commands if name == "kubectl"]
        self.assertEqual(
            [entry for entry in kubectl if entry != "get nodes"],
            [
                "cordon homelab-02",
                "drain homelab-02",
                "uncordon homelab-02",
                "cordon homelab-01",
                "drain homelab-01",
                "uncordon homelab-01",
            ],
        )
        rebooter.wait_postgres_ready.assert_called_once()
        rebooter.verify_nas_datasets.assert_called_once()
        rebooter.verify_nas_appliance.assert_called_once()

    def test_the_ci_host_is_handed_to_a_watcher(self) -> None:
        fleet = Fleet({"homelab-04"})
        result = self.rebooter(fleet).maintain()
        watch = [
            (name, remote)
            for name, remote in fleet.commands
            if "homelab-reboot-watch" in remote
        ]
        self.assertEqual(len(watch), 1)
        name, remote = watch[0]
        self.assertEqual(name, "homelab-01")
        self.assertIn("6.18.62", remote)
        self.assertIn("uncordon homelab-04", remote)
        # The reboot is scheduled after the watcher, with time for the job to
        # exit, and this run never waits for it.
        self.assertEqual(fleet.reboots(), ["homelab-04"])
        self.assertIn("--on-active=90s", fleet.commands[-1][1])
        self.assertNotIn(("homelab-04", "true"), fleet.commands)
        self.assertEqual(result["hosts"][-1]["status"], "handed-off")

    def test_never_boots_a_system_that_is_not_running(self) -> None:
        # A cron run skips the deployment authorization check, so a boot
        # profile that a deployment did not activate is left alone.
        fleet = Fleet({"homelab-02", "homelab-03"})
        fleet.stale_profile.add("homelab-02")
        result = self.rebooter(fleet).maintain()
        self.assertEqual(fleet.reboots(), ["homelab-03"])
        self.assertEqual(result["hosts"][0]["status"], "deferred")

    def test_ci_host_waits_while_it_writes_a_backup(self) -> None:
        fleet = Fleet({"homelab-04"})
        fleet.busy["platform-backup.service"] = "activating"
        result = self.rebooter(fleet).maintain()
        self.assertEqual(fleet.reboots(), [])
        self.assertEqual(result["hosts"][-1]["status"], "deferred")

    def test_nas_waits_for_a_recent_backup_and_idle_drills(self) -> None:
        for change, reason in [
            (lambda fleet: setattr(fleet, "backup", ("success", 9000.0)), "two hours"),
            (lambda fleet: setattr(fleet, "backup", ("exit-code", 60.0)), "succeed"),
            (
                lambda fleet: fleet.busy.update(
                    {"platform-restore-drill.service": "active"}
                ),
                "platform-restore-drill.service is active",
            ),
            (
                lambda fleet: setattr(fleet, "pools", "pool: tank\n state: DEGRADED"),
                "ZFS",
            ),
        ]:
            with self.subTest(reason=reason):
                fleet = Fleet({"nas-01", "homelab-02"})
                change(fleet)
                rebooter = self.rebooter(fleet)
                result = rebooter.maintain()
                self.assertEqual(fleet.reboots(), ["homelab-02"])
                nas = next(e for e in result["hosts"] if e["host"] == "nas-01")
                self.assertEqual(nas["status"], "deferred")
                self.assertIn(reason, nas["reason"])
                self.assertIn(
                    "Reboot deferred",
                    [call.args[0] for call in rebooter.notifier.call_args_list],
                )

    def test_a_host_on_the_old_kernel_stops_the_run_cordoned(self) -> None:
        fleet = Fleet({"homelab-02", "homelab-03"})
        fleet.boots_old.add("homelab-02")
        rebooter = self.rebooter(fleet)
        with self.assertRaisesRegex(FleetDeploymentError, "stays cordoned"):
            rebooter.maintain()
        self.assertEqual(fleet.reboots(), ["homelab-02"])
        self.assertNotIn(("kubectl", "uncordon homelab-02"), fleet.commands)
        title, _, priority, _ = rebooter.notifier.call_args.args
        self.assertEqual((title, priority), ("[ALERT] Fleet reboot halted", "urgent"))

    def test_a_host_that_never_returns_times_out(self) -> None:
        fleet = Fleet({"homelab-02"})
        rebooter = self.rebooter(fleet)
        original = fleet.__call__

        def never_up(argv, **options):
            result = original(argv, **options)
            if argv[-1] == "true" and "homelab-02" in fleet.reboots():
                return subprocess.CompletedProcess(argv, 255, "", "down")
            return result

        rebooter.runner = never_up
        with self.assertRaisesRegex(FleetDeploymentError, "homelab-02 to boot"):
            rebooter.maintain()

    def test_no_host_starts_after_half_past_four(self) -> None:
        fleet = Fleet(set(FLEET_HOSTS))
        result = self.rebooter(
            fleet, now=datetime(2026, 11, 11, 10, 40, tzinfo=UTC)
        ).maintain()
        self.assertEqual(fleet.reboots(), [])
        self.assertEqual(
            {entry["status"] for entry in result["hosts"]}, {"window-closing"}
        )

    def test_dry_run_reboots_nothing(self) -> None:
        fleet = Fleet(set(FLEET_HOSTS))
        result = self.rebooter(fleet, dry_run=True).maintain()
        self.assertEqual(fleet.reboots(), [])
        self.assertNotIn("cordon", " ".join(remote for _, remote in fleet.commands))
        self.assertEqual(
            {entry["status"] for entry in result["hosts"]}, {"would-reboot"}
        )

    def test_ignore_window_requires_dry_run(self) -> None:
        self.assertEqual(main(["--ignore-window"], runner=Fleet(set())), 2)


if __name__ == "__main__":
    unittest.main()
