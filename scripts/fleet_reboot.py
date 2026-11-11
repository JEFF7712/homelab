"""Nightly fleet reboots onto the installed kernel.

A NixOS switch installs a new kernel but keeps running the old one, so kernel
fixes only take effect after a reboot, and nothing rebooted the fleet (four
hosts ran a kernel two updates old on 2026-11-10). This runs inside the
03:00-05:00 America/Chicago window from a nightly cron and reboots, one host
at a time, each host whose booted kernel, initrd or modules differ from the
running system.

It never deploys. A cron run skips the authorization check that agent
deployments get (scripts/ci/policy.py), so a host is rebooted only onto the
system a gated deployment activated and verified: the boot profile must be
the running system, or the host is skipped.

k3s nodes are cordoned and drained first; after the reboot the host must run
the installed system with no newly failed unit and pass its host-type checks
(node Ready and Postgres primaries for k3s, DNS for the appliance, NFS,
datasets and caches for the NAS) before the node is uncordoned.

The NAS goes after the k3s nodes and the DNS appliance, and only when a
platform backup finished in the last two hours, no backup, drill or
maintenance unit is running and every ZFS pool is healthy; otherwise it waits
for the next night. The CI host runs this job, so it cannot reboot itself and
check the result: it goes last, drained, with a watcher on the first
control-plane node that uncordons it once it reports Ready on the installed
kernel, and its reboot scheduled shortly after the job exits.

No host starts after 04:30, so the window ends by 05:00. The first failure
stops the run, leaves the host cordoned and sends an urgent ntfy alert.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from datetime import time as clock
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scripts.deploy_fleet import (
    FLEET_HOSTS,
    ClusterLease,
    FleetDeployer,
    FleetDeploymentError,
    FleetLock,
    HostConfig,
    LockContentionError,
    Notifier,
    Runner,
    Sleeper,
    lease_holder,
    parse_targets,
    run_command,
    verify_k3s_nodes_ready,
)
from scripts.deploy_fleet import build_parser as deploy_parser

logger = logging.getLogger("fleet_reboot")

ZONE = ZoneInfo("America/Chicago")
WINDOW_START = clock(3, 0)
WINDOW_END = clock(5, 0)
LAST_START = clock(4, 30)
# The CI job runs on this host, and the watcher that finishes its reboot runs
# on the first control-plane node.
SELF_HOST = "homelab-04"
WATCHER_HOST = "homelab-01"
# Units whose activity makes a NAS reboot unsafe (platform-backup.nix).
NAS_BUSY_UNITS = (
    "platform-backup.service",
    "platform-restore-drill.service",
    "platform-backup-maintenance.service",
)
NAS_BACKUP_MAX_AGE = 2 * 3600
# What makes a booted system differ from the installed one in a way only a
# reboot applies.
BOOT_PARTS = ("kernel", "initrd", "kernel-modules")
SYSTEM_BIN = "/run/current-system/sw/bin"
REBOOT_DOWN_TIMEOUT = 180.0
REBOOT_UP_TIMEOUT = 900.0
POLL_SECONDS = 10.0


@dataclass(frozen=True)
class Window:
    start: clock = WINDOW_START
    end: clock = WINDOW_END
    last_start: clock = LAST_START

    def open(self, now: datetime) -> bool:
        local = now.astimezone(ZONE).time()
        return self.start <= local < self.end

    def may_start(self, now: datetime) -> bool:
        local = now.astimezone(ZONE).time()
        return self.start <= local < self.last_start


NIGHTLY = Window()


def reboot_order(targets: Sequence[HostConfig]) -> list[HostConfig]:
    """k3s nodes and appliances in inventory order, then the NAS, then this host."""

    def key(host: HostConfig) -> tuple[int, int]:
        if host.name == SELF_HOST:
            return (2, host.order)
        if host.host_type == "appliance-nas":
            return (1, host.order)
        return (0, host.order)

    return sorted(targets, key=key)


class FleetRebooter(FleetDeployer):
    def __init__(
        self,
        targets: list[HostConfig],
        *,
        window: Window = NIGHTLY,
        clock_now: Callable[[], datetime] = lambda: datetime.now(ZONE),
        **kwargs: Any,
    ) -> None:
        super().__init__(targets, **kwargs)
        self.window = window
        self.clock_now = clock_now

    # -- what a host needs ------------------------------------------------

    def boot_parts(self, host: HostConfig) -> dict[str, tuple[str, str]]:
        """(booted, installed) store path of each boot part."""
        script = "; ".join(
            f"readlink -f /run/booted-system/{part}; readlink -f /run/current-system/{part}"
            for part in BOOT_PARTS
        )
        result = run_command(
            self.ssh_command(host.ip, script), runner=self.runner, timeout=30.0
        )
        lines = result.stdout.split()
        if len(lines) != 2 * len(BOOT_PARTS):
            raise FleetDeploymentError(
                f"{host.name}: could not read booted and installed boot parts"
            )
        return {
            part: (lines[2 * index], lines[2 * index + 1])
            for index, part in enumerate(BOOT_PARTS)
        }

    def boots_running_system(self, host: HostConfig) -> bool:
        """Whether a reboot would boot the system that is running now."""
        result = run_command(
            self.ssh_command(
                host.ip,
                "readlink -f /nix/var/nix/profiles/system; readlink -f /run/current-system",
            ),
            runner=self.runner,
            timeout=30.0,
        )
        paths = result.stdout.split()
        return len(paths) == 2 and paths[0] == paths[1]

    def reboot_pending(self, host: HostConfig) -> bool:
        return any(
            booted != installed for booted, installed in self.boot_parts(host).values()
        )

    def installed_kernel_version(self, host: HostConfig) -> str:
        """uname -r of the installed kernel, from its modules directory."""
        result = run_command(
            self.ssh_command(
                host.ip, "ls /run/current-system/kernel-modules/lib/modules"
            ),
            runner=self.runner,
            timeout=30.0,
        )
        versions = result.stdout.split()
        if len(versions) != 1:
            raise FleetDeploymentError(
                f"{host.name}: expected one installed kernel, found {versions}"
            )
        return versions[0]

    def busy_unit(self, host: HostConfig, units: Sequence[str]) -> str | None:
        """The first of units that is running, as "unit is state", or None."""
        states = run_command(
            self.ssh_command(host.ip, shlex.join(["systemctl", "is-active", *units])),
            runner=self.runner,
            timeout=30.0,
            check=False,
        ).stdout.split()
        for unit, state in zip(units, states, strict=False):
            if state in ("active", "activating", "deactivating", "reloading"):
                return f"{unit} is {state}"
        return None

    def nas_blocker(self, host: HostConfig) -> str | None:
        """Why the NAS must not reboot now, or None."""
        busy = self.busy_unit(host, NAS_BUSY_UNITS)
        if busy:
            return busy
        finished = run_command(
            self.ssh_command(
                host.ip,
                "systemctl show platform-backup.service "
                "-p Result -p ExecMainExitTimestampMonotonic; cat /proc/uptime",
            ),
            runner=self.runner,
            timeout=30.0,
        ).stdout
        fields = dict(
            line.split("=", 1) for line in finished.splitlines() if "=" in line
        )
        uptime_line = [line for line in finished.splitlines() if "=" not in line]
        if fields.get("Result") != "success":
            return f"the last platform backup did not succeed ({fields.get('Result')})"
        try:
            exited = int(fields["ExecMainExitTimestampMonotonic"]) / 1_000_000
            uptime = float(uptime_line[0].split()[0])
        except (KeyError, ValueError, IndexError):
            return "cannot tell when the last platform backup finished"
        if exited <= 0 or uptime - exited > NAS_BACKUP_MAX_AGE:
            return "no platform backup finished in the last two hours"
        pools = run_command(
            self.ssh_command(host.ip, "sudo -n zpool status -x"),
            runner=self.runner,
            timeout=30.0,
        ).stdout.strip()
        if pools != "all pools are healthy":
            return f"ZFS reports: {pools.splitlines()[0] if pools else 'nothing'}"
        ready, message = verify_k3s_nodes_ready(runner=self.runner, timeout=10.0)
        if not ready:
            return f"cluster not ready: {message}"
        return None

    # -- rebooting --------------------------------------------------------

    def schedule_reboot(self, host: HostConfig, delay: str) -> None:
        run_command(
            self.ssh_command(
                host.ip,
                shlex.join(
                    [
                        "sudo",
                        "-n",
                        "systemd-run",
                        f"--on-active={delay}",
                        "--unit=homelab-fleet-reboot",
                        "--no-ask-password",
                        f"{SYSTEM_BIN}/systemctl",
                        "reboot",
                    ]
                ),
            ),
            runner=self.runner,
            timeout=30.0,
        )

    def reachable(self, host: HostConfig) -> bool:
        result = run_command(
            self.ssh_command(host.ip, "true"),
            runner=self.runner,
            timeout=15.0,
            check=False,
        )
        return result.returncode == 0

    def wait_for(self, check: Callable[[], bool], timeout: float, what: str) -> None:
        for _ in range(max(1, int(timeout / POLL_SECONDS))):
            if check():
                return
            self.sleeper(POLL_SECONDS)
        raise FleetDeploymentError(f"timed out after {timeout:.0f}s waiting for {what}")

    def verify_rebooted(self, host: HostConfig, failed_before: set[str]) -> None:
        if self.reboot_pending(host):
            raise FleetDeploymentError(f"{host.name} did not boot the installed system")
        if not self.settled(lambda: not (self.failed_units(host) - failed_before)):
            raise FleetDeploymentError(f"{host.name} has newly failed systemd units")
        if host.host_type in ("k3s-worker", "k3s-stateful"):
            self.wait_node_ready(host.name)
        elif host.host_type == "appliance-dns":
            self.verify_dns_appliance(host.ip)
        elif host.host_type == "appliance-nas":
            self.verify_nas_datasets(host)
            self.verify_nas_appliance(host.ip)

    def reboot_host(self, host: HostConfig) -> None:
        is_k3s = host.host_type in ("k3s-worker", "k3s-stateful")
        failed_before = self.failed_units(host)
        cordoned = False
        try:
            if is_k3s:
                self.cordon_node(host.name)
                cordoned = True
                self.drain_node(host.name)
            self.schedule_reboot(host, "5s")
            self.wait_for(
                lambda: not self.reachable(host),
                REBOOT_DOWN_TIMEOUT,
                f"{host.name} to go down",
            )
            self.wait_for(
                lambda: self.reachable(host), REBOOT_UP_TIMEOUT, f"{host.name} to boot"
            )
            self.verify_rebooted(host, failed_before)
            if is_k3s:
                self.uncordon_node(host.name)
                cordoned = False
                if host.host_type == "k3s-stateful":
                    self.wait_postgres_ready()
            self.cooldown(host.name)
        except Exception as err:
            raise FleetDeploymentError(
                f"reboot of {host.name} failed: {err}"
                + ("; the node stays cordoned" if cordoned else "")
            ) from err

    def hand_off_self(self, host: HostConfig) -> None:
        """Drain this job's own host and let the watcher finish its reboot."""
        version = self.installed_kernel_version(host)
        watcher = FLEET_HOSTS[WATCHER_HOST]
        self.cordon_node(host.name)
        self.drain_node(host.name)
        # Waits up to 20 minutes for the node to report Ready on the installed
        # kernel, then uncordons it; otherwise it stays cordoned for a person
        # or the next night. k3s's own kubectl is the cluster admin there.
        node = shlex.quote(host.name)
        script = (
            "deadline=$(( $(date +%s) + 1200 )); "
            "while [ $(date +%s) -lt $deadline ]; do "
            f"ready=$(k3s kubectl get node {node} -o "
            "jsonpath='{.status.conditions[?(@.type==\"Ready\")].status}' 2>/dev/null); "
            f"kernel=$(k3s kubectl get node {node} -o "
            "jsonpath='{.status.nodeInfo.kernelVersion}' 2>/dev/null); "
            f'if [ "$ready" = True ] && [ "$kernel" = {shlex.quote(version)} ]; then '
            f"k3s kubectl uncordon {node}; exit 0; fi; sleep 15; done; exit 1"
        )
        run_command(
            self.ssh_command(
                watcher.ip,
                shlex.join(
                    [
                        "sudo",
                        "-n",
                        "systemd-run",
                        f"--unit=homelab-reboot-watch-{host.name}",
                        "--collect",
                        "--no-ask-password",
                        # Starts before the reboot is scheduled; a minute
                        # covers the drain and this job's own exit.
                        "--on-active=30s",
                        # Transient units get a PATH without the system profile.
                        f"--setenv=PATH={SYSTEM_BIN}",
                        f"{SYSTEM_BIN}/sh",
                        "-c",
                        script,
                    ]
                ),
            ),
            runner=self.runner,
            timeout=30.0,
        )
        self.schedule_reboot(host, "90s")

    # -- the run ----------------------------------------------------------

    def maintain(self) -> dict[str, Any]:
        now = self.clock_now()
        if not self.window.open(now):
            logger.info("Outside the maintenance window (%s); nothing to do", now)
            return {"status": "outside-window", "hosts": []}
        results: list[dict[str, Any]] = []
        rebooted: list[str] = []
        try:
            for host in reboot_order(self.targets):
                if not self.window.may_start(self.clock_now()):
                    results.append({"host": host.name, "status": "window-closing"})
                    continue
                if not self.reboot_pending(host):
                    results.append({"host": host.name, "status": "current"})
                    continue
                blocker = None
                if not self.boots_running_system(host):
                    blocker = "its boot profile is not the running system"
                elif host.host_type == "appliance-nas":
                    blocker = self.nas_blocker(host)
                elif host.name == SELF_HOST:
                    # homelab-04 also writes platform snapshots.
                    blocker = self.busy_unit(host, ("platform-backup.service",))
                if blocker:
                    logger.warning("[%s] Not rebooting: %s", host.name, blocker)
                    self.notify(
                        title="Reboot deferred",
                        message=f"{host.name} keeps its pending kernel until the next night: {blocker}",
                        priority="default",
                        tags=["hourglass"],
                    )
                    results.append(
                        {"host": host.name, "status": "deferred", "reason": blocker}
                    )
                    continue
                if self.dry_run:
                    results.append({"host": host.name, "status": "would-reboot"})
                    continue
                started = time.monotonic()
                if host.name == SELF_HOST:
                    self.hand_off_self(host)
                    results.append({"host": host.name, "status": "handed-off"})
                    rebooted.append(host.name + " (handed off)")
                    continue
                self.reboot_host(host)
                rebooted.append(host.name)
                results.append(
                    {
                        "host": host.name,
                        "status": "rebooted",
                        "duration_seconds": round(time.monotonic() - started, 1),
                    }
                )
        except Exception as err:
            self.notify(
                title="[ALERT] Fleet reboot halted",
                message=str(err),
                priority="urgent",
                tags=["rotating_light", "warning"],
            )
            raise
        if rebooted:
            self.notify(
                title="Fleet reboots completed",
                message="Rebooted onto the installed kernel: " + ", ".join(rebooted),
                priority="default",
                tags=["white_check_mark", "arrows_counterclockwise"],
            )
        return {"status": "success", "dry_run": self.dry_run, "hosts": results}


def build_parser() -> argparse.ArgumentParser:
    parser = deploy_parser()
    parser.description = "Reboot hosts onto their installed kernel, one at a time."
    parser.add_argument(
        "--ignore-window",
        action="store_true",
        help="Run outside 03:00-05:00 America/Chicago (only with --dry-run).",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    runner: Runner = subprocess.run,
    sleeper: Sleeper = time.sleep,
    notifier: Notifier | None = None,
    clock_now: Callable[[], datetime] = lambda: datetime.now(ZONE),
) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    args = build_parser().parse_args(argv)
    if args.ignore_window and not args.dry_run:
        logger.error("--ignore-window is only allowed with --dry-run")
        return 2
    try:
        targets = parse_targets(args.targets)
    except FleetDeploymentError as err:
        logger.error("Configuration error: %s", err)
        return 2
    ssh_raw = args.ssh_opts or os.getenv("NIX_SSHOPTS") or os.getenv("SSH_OPTS")
    window = (
        Window(clock(0, 0), clock(23, 59, 59), clock(23, 59, 59))
        if args.ignore_window
        else NIGHTLY
    )
    try:
        with (
            FleetLock(Path(args.lock_file), metadata={"operation": "fleet-reboot"}),
            contextlib.ExitStack() as held,
        ):
            if not args.dry_run:
                held.enter_context(
                    ClusterLease(lease_holder("fleet-reboot"), runner=runner)
                )
            rebooter = FleetRebooter(
                targets,
                window=window,
                clock_now=clock_now,
                dry_run=args.dry_run,
                drain_timeout=args.drain_timeout,
                ready_timeout=args.ready_timeout,
                ssh_user=args.ssh_user,
                flake_path=args.flake_path,
                runner=runner,
                sleeper=sleeper,
                notifier=notifier,
                enable_notifications=not args.no_notify,
                ntfy_topic=args.ntfy_topic,
                ntfy_url=args.ntfy_url,
                ntfy_token=args.ntfy_token,
                ssh_opts=shlex.split(ssh_raw) if ssh_raw else None,
            )
            result = rebooter.maintain()
            print(json.dumps(result, indent=2))
            return 0
    except LockContentionError as err:
        logger.error(str(err))
        return 3
    except FleetDeploymentError as err:
        logger.error("Fleet reboot halted: %s", err)
        return 1


if __name__ == "__main__":
    sys.exit(main())
