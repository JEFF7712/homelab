from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("deploy_fleet")

Runner = Callable[..., subprocess.CompletedProcess[str]]
Sleeper = Callable[[float], None]
SocketChecker = Callable[[str, int, float], bool]
HttpChecker = Callable[[str, float], bool]
Notifier = Callable[[str, str, str, list[str] | None], bool]


class FleetDeploymentError(Exception):
    """Raised when a fleet deployment step fails."""


class LockContentionError(FleetDeploymentError):
    """Raised when another fleet deployment currently holds the lock."""


@dataclass(frozen=True)
class HostConfig:
    name: str
    ip: str
    host_type: str  # "k3s-worker", "k3s-stateful", "appliance-dns", "appliance-nas"
    order: int


FLEET_HOSTS: dict[str, HostConfig] = {
    "homelab-02": HostConfig(
        name="homelab-02",
        ip="10.0.30.12",
        host_type="k3s-worker",
        order=1,
    ),
    "homelab-03": HostConfig(
        name="homelab-03",
        ip="10.0.30.13",
        host_type="k3s-worker",
        order=2,
    ),
    "homelab-04": HostConfig(
        name="homelab-04",
        ip="10.0.30.14",
        host_type="k3s-worker",
        order=3,
    ),
    "homelab-05": HostConfig(
        name="homelab-05",
        ip="10.0.30.15",
        host_type="k3s-worker",
        order=4,
    ),
    "homelab-01": HostConfig(
        name="homelab-01",
        ip="10.0.30.11",
        host_type="k3s-stateful",
        order=5,
    ),
    "adguard-netbird-01": HostConfig(
        name="adguard-netbird-01",
        ip="10.0.30.10",
        host_type="appliance-dns",
        order=6,
    ),
    "nas-01": HostConfig(
        name="nas-01",
        ip="10.0.30.20",
        host_type="appliance-nas",
        order=7,
    ),
}


class FleetLock:
    """Acquires a non-blocking exclusive flock on a lockfile."""

    def __init__(self, lock_path: Path, metadata: dict[str, Any] | None = None) -> None:
        self.lock_path = lock_path
        self.metadata = metadata or {}
        self._fd: int | None = None

    def __enter__(self) -> FleetLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        self.release()

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(
                str(self.lock_path), os.O_CREAT | os.O_RDWR | os.O_TRUNC, 0o600
            )
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            content = json.dumps(
                {
                    "pid": os.getpid(),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    **self.metadata,
                }
            )
            os.write(self._fd, content.encode("utf-8"))
        except (BlockingIOError, OSError) as err:
            owner_info = "unknown"
            if self.lock_path.is_file():
                try:
                    owner_info = self.lock_path.read_text(encoding="utf-8").strip()
                except OSError:
                    pass
            raise LockContentionError(
                f"Fleet deployment lock ({self.lock_path}) is currently held by another process: {owner_info}"
            ) from err

    def release(self) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
            try:
                if self.lock_path.is_file():
                    self.lock_path.unlink()
            except OSError:
                pass


def run_command(
    argv: list[str],
    runner: Runner = subprocess.run,
    timeout: float = 300.0,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    logger.info("Executing: %s (timeout=%.1fs)", " ".join(argv), timeout)
    try:
        result = runner(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=max(0.1, timeout),
        )
    except subprocess.TimeoutExpired as err:
        msg = f"Command timed out after {timeout:.1f}s: {' '.join(argv)}"
        logger.error(msg)
        raise FleetDeploymentError(msg) from err
    except OSError as err:
        msg = f"Failed to execute command {' '.join(argv)}: {err}"
        logger.error(msg)
        raise FleetDeploymentError(msg) from err

    if check and result.returncode != 0:
        detail = (result.stdout + "\n" + result.stderr).strip()
        msg = f"Command failed with exit code {result.returncode}: {' '.join(argv)}\n{detail}"
        logger.error(msg)
        raise FleetDeploymentError(msg)
    return result


def default_socket_checker(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def default_http_checker(url: str, timeout: float = 5.0) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "deploy-fleet/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return bool(200 <= resp.status < 400)
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def default_ntfy_notifier(
    title: str,
    message: str,
    priority: str = "default",
    tags: list[str] | None = None,
    *,
    server_url: str | None = None,
    topic: str | None = None,
    token: str | None = None,
) -> bool:
    url_base = (server_url or os.getenv("NTFY_URL") or "https://ntfy.rupan.dev").rstrip(
        "/"
    )
    ntfy_topic = topic or os.getenv("NTFY_TOPIC")
    ntfy_token = token or os.getenv("NTFY_TOKEN")

    if not ntfy_topic:
        return False

    full_url = f"{url_base}/{ntfy_topic.lstrip('/')}"
    headers: dict[str, str] = {
        "Title": title,
        "Priority": priority,
    }
    if tags:
        headers["Tags"] = ",".join(tags)
    if ntfy_token:
        headers["Authorization"] = f"Bearer {ntfy_token}"

    try:
        req = urllib.request.Request(
            full_url,
            data=message.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return bool(200 <= resp.status < 300)
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        logger.warning("Failed to send ntfy notification to %s: %s", full_url, err)
        return False


def verify_ssh(
    ip: str,
    user: str,
    runner: Runner = subprocess.run,
    timeout: float = 5.0,
    ssh_opts: Sequence[str] | None = None,
) -> bool:
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={int(max(1, timeout))}",
    ]
    if ssh_opts is not None:
        cmd.extend(ssh_opts)
    else:
        env_opts = os.getenv("NIX_SSHOPTS") or os.getenv("SSH_OPTS")
        if env_opts:
            cmd.extend(shlex.split(env_opts))
    cmd.extend([f"{user}@{ip}", "true"])
    res = run_command(cmd, runner=runner, timeout=timeout + 2.0, check=False)
    if res.returncode != 0:
        logger.warning(
            "SSH check to %s@%s failed (exit %d): %s",
            user,
            ip,
            res.returncode,
            (res.stdout + "\n" + res.stderr).strip(),
        )
    return res.returncode == 0


def verify_k3s_nodes_ready(
    runner: Runner = subprocess.run,
    timeout: float = 10.0,
) -> tuple[bool, str]:
    cmd = ["kubectl", "get", "nodes", "-o", "json"]
    res = run_command(cmd, runner=runner, timeout=timeout, check=False)
    if res.returncode != 0:
        return False, f"kubectl get nodes failed: {res.stderr.strip()}"

    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError as err:
        return False, f"Failed to parse kubectl output: {err}"

    items = data.get("items", [])
    if not items:
        return False, "No nodes returned by kubectl"

    expected_k3s = {
        "homelab-01",
        "homelab-02",
        "homelab-03",
        "homelab-04",
        "homelab-05",
    }
    found_nodes = set()

    for item in items:
        name = item.get("metadata", {}).get("name")
        found_nodes.add(name)
        conditions = item.get("status", {}).get("conditions", [])
        is_ready = any(
            c.get("type") == "Ready" and c.get("status") == "True" for c in conditions
        )
        if not is_ready:
            return False, f"Node {name} is NotReady"

    missing = expected_k3s - found_nodes
    if missing:
        return False, f"Cluster missing expected nodes: {', '.join(sorted(missing))}"

    return True, "All K3s nodes are Ready"


def verify_bgp_peer(
    runner: Runner = subprocess.run,
    timeout: float = 15.0,
) -> tuple[bool, str]:
    cmd_find_pod = [
        "kubectl",
        "-n",
        "kube-system",
        "get",
        "pods",
        "-l",
        "app.kubernetes.io/name=cilium-agent",
        "--field-selector=spec.nodeName=homelab-01",
        "-o",
        "jsonpath={.items[0].metadata.name}",
    ]
    res_find = run_command(cmd_find_pod, runner=runner, timeout=timeout, check=False)
    pod_name = res_find.stdout.strip()
    if not pod_name or res_find.returncode != 0:
        return False, "Could not find cilium agent pod on homelab-01"

    cmd_check_bgp = [
        "kubectl",
        "-n",
        "kube-system",
        "exec",
        pod_name,
        "-c",
        "cilium-agent",
        "--",
        "cilium-dbg",
        "bgp",
        "peers",
    ]
    res_bgp = run_command(cmd_check_bgp, runner=runner, timeout=timeout, check=False)
    if res_bgp.returncode != 0:
        return False, f"cilium-dbg bgp peers check failed: {res_bgp.stderr.strip()}"

    output = res_bgp.stdout.lower()
    if "established" in output:
        return True, "BGP session established"
    return False, f"BGP session not established in output: {res_bgp.stdout.strip()}"


def parse_targets(target_args: Sequence[str] | None) -> list[HostConfig]:
    if not target_args:
        # Default full safe order
        return sorted(FLEET_HOSTS.values(), key=lambda h: h.order)

    chosen_names: set[str] = set()
    for arg in target_args:
        for item in arg.split(","):
            clean = item.strip()
            if clean:
                chosen_names.add(clean)

    for name in chosen_names:
        if name not in FLEET_HOSTS:
            valid = ", ".join(sorted(FLEET_HOSTS.keys()))
            raise FleetDeploymentError(
                f"Unknown target host '{name}'. Valid targets: {valid}"
            )

    selected = [FLEET_HOSTS[name] for name in chosen_names]
    # Always sort by canonical rollout order
    return sorted(selected, key=lambda h: h.order)


class FleetDeployer:
    """Executes the safe rolling deployment across the homelab fleet."""

    def __init__(
        self,
        targets: list[HostConfig],
        *,
        dry_run: bool = False,
        skip_preflight: bool = False,
        skip_drain: bool = False,
        skip_cooldown: bool = False,
        cooldown_seconds: float = 30.0,
        drain_timeout: float = 120.0,
        ready_timeout: float = 180.0,
        ssh_user: str = "rupan",
        flake_path: str = "./flake",
        runner: Runner = subprocess.run,
        sleeper: Sleeper = time.sleep,
        socket_checker: SocketChecker = default_socket_checker,
        http_checker: HttpChecker = default_http_checker,
        enable_notifications: bool = True,
        ntfy_topic: str | None = None,
        ntfy_url: str | None = None,
        ntfy_token: str | None = None,
        notifier: Notifier | None = None,
        ssh_opts: Sequence[str] | None = None,
    ) -> None:
        self.targets = targets
        self.dry_run = dry_run
        self.skip_preflight = skip_preflight
        self.skip_drain = skip_drain
        self.skip_cooldown = skip_cooldown
        self.cooldown_seconds = cooldown_seconds
        self.drain_timeout = drain_timeout
        self.ready_timeout = ready_timeout
        self.ssh_user = ssh_user
        self.flake_path = flake_path
        self.runner = runner
        self.sleeper = sleeper
        self.socket_checker = socket_checker
        self.http_checker = http_checker
        self.enable_notifications = enable_notifications
        self.ntfy_topic = ntfy_topic
        self.ntfy_url = ntfy_url
        self.ntfy_token = ntfy_token
        self.notifier = notifier
        self.ssh_opts = list(ssh_opts) if ssh_opts is not None else None

    def notify(
        self,
        title: str,
        message: str,
        priority: str = "default",
        tags: list[str] | None = None,
    ) -> bool:
        if not self.enable_notifications:
            return False
        if self.notifier is not None:
            return self.notifier(title, message, priority, tags)
        return default_ntfy_notifier(
            title,
            message,
            priority,
            tags,
            server_url=self.ntfy_url,
            topic=self.ntfy_topic,
            token=self.ntfy_token,
        )

    def run_preflight(self) -> None:
        logger.info("=== Running Pre-flight Gate ===")
        # 1. SSH check on all targeted hosts
        for host in self.targets:
            logger.info("Checking SSH reachability to %s (%s)...", host.name, host.ip)
            if not verify_ssh(
                host.ip,
                self.ssh_user,
                runner=self.runner,
                timeout=5.0,
                ssh_opts=self.ssh_opts,
            ):
                raise FleetDeploymentError(
                    f"Preflight failed: SSH unreachable to {host.name} ({host.ip})"
                )
            logger.info("SSH reachability confirmed for %s", host.name)

        # 2. If any K3s host is targeted, verify cluster node health
        k3s_targeted = any(
            h.host_type in ("k3s-worker", "k3s-stateful") for h in self.targets
        )
        if k3s_targeted:
            logger.info("Checking K3s cluster node readiness...")
            ready, msg = verify_k3s_nodes_ready(runner=self.runner, timeout=10.0)
            if not ready:
                raise FleetDeploymentError(f"Preflight failed: {msg}")
            logger.info("K3s node check passed: %s", msg)

            logger.info("Checking Cilium BGP peering...")
            bgp_ok, bgp_msg = verify_bgp_peer(runner=self.runner, timeout=10.0)
            if not bgp_ok:
                logger.warning(
                    "Preflight BGP peer check warning: %s (proceeding)", bgp_msg
                )
            else:
                logger.info("Cilium BGP check passed: %s", bgp_msg)

        logger.info("=== Pre-flight Gate Passed ===")

    def cordon_node(self, node_name: str) -> None:
        logger.info("[%s] Cordoning node...", node_name)
        cmd = ["kubectl", "cordon", node_name]
        if self.dry_run:
            cmd.append("--dry-run=client")
        run_command(cmd, runner=self.runner, timeout=30.0)

    def uncordon_node(self, node_name: str) -> None:
        logger.info("[%s] Uncordoning node...", node_name)
        cmd = ["kubectl", "uncordon", node_name]
        if self.dry_run:
            cmd.append("--dry-run=client")
        run_command(cmd, runner=self.runner, timeout=30.0)

    def drain_node(self, node_name: str) -> None:
        logger.info(
            "[%s] Draining node (timeout=%.0fs)...", node_name, self.drain_timeout
        )
        cmd = [
            "kubectl",
            "drain",
            node_name,
            "--ignore-daemonsets",
            "--delete-emptydir-data",
            "--force",
            f"--timeout={int(self.drain_timeout)}s",
        ]
        if self.dry_run:
            cmd.append("--dry-run=client")
        run_command(cmd, runner=self.runner, timeout=self.drain_timeout + 10.0)

    def rebuild_host(self, host: HostConfig) -> None:
        if host.host_type == "appliance-nas":
            self.verify_nas_datasets(host)
        action = "dry-activate" if self.dry_run else "switch"
        logger.info("[%s] Executing nixos-rebuild %s...", host.name, action)
        cmd = [
            "nixos-rebuild",
            action,
            "--flake",
            f"{self.flake_path}#{host.name}",
            "--target-host",
            f"{self.ssh_user}@{host.ip}",
            "--build-host",
            f"{self.ssh_user}@{host.ip}",
            "--elevate=sudo",
        ]
        run_command(cmd, runner=self.runner, timeout=600.0)

    def verify_nas_datasets(self, host: HostConfig) -> None:
        result = run_command(
            [
                "nix",
                "eval",
                "--json",
                f"{self.flake_path}#nixosConfigurations.{host.name}.config.fileSystems",
                "--apply",
                (
                    "filesystems: builtins.map (fs: fs.device) (builtins.filter "
                    '(fs: fs.fsType == "zfs") (builtins.attrValues filesystems))'
                ),
            ],
            runner=self.runner,
            timeout=120.0,
        )
        try:
            datasets = json.loads(result.stdout)
        except json.JSONDecodeError as err:
            raise FleetDeploymentError(
                "NAS storage evaluation returned invalid JSON"
            ) from err
        if not isinstance(datasets, list) or not all(
            isinstance(dataset, str) and dataset and not dataset.startswith("-")
            for dataset in datasets
        ):
            raise FleetDeploymentError(
                "NAS storage evaluation returned invalid datasets"
            )
        if not datasets:
            return
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
        if self.ssh_opts is not None:
            cmd.extend(self.ssh_opts)
        else:
            cmd.extend(
                shlex.split(os.getenv("NIX_SSHOPTS") or os.getenv("SSH_OPTS") or "")
            )
        cmd.extend(
            [
                f"{self.ssh_user}@{host.ip}",
                shlex.join(
                    ["sudo", "-n", "zfs", "list", "-H", "-o", "name", *datasets]
                ),
            ]
        )
        result = run_command(cmd, runner=self.runner, timeout=30.0, check=False)
        if result.returncode != 0:
            detail = (result.stdout + "\n" + result.stderr).strip()
            raise FleetDeploymentError(
                f"NAS storage preflight failed on {host.name}: provision or restore the "
                f"configured ZFS datasets before rebuilding. NixOS rebuild does not "
                f"create Disko datasets.\n{detail}"
            )

    def wait_node_ready(self, node_name: str) -> None:
        if self.dry_run:
            logger.info("[%s] [DRY RUN] Would wait for node to report Ready", node_name)
            return

        logger.info(
            "[%s] Waiting for node to report Ready (timeout=%.0fs)...",
            node_name,
            self.ready_timeout,
        )
        cmd = [
            "kubectl",
            "get",
            "node",
            node_name,
            "-o",
            'jsonpath={.status.conditions[?(@.type=="Ready")].status}',
        ]
        started = time.monotonic()
        while time.monotonic() - started < self.ready_timeout:
            res = run_command(cmd, runner=self.runner, timeout=10.0, check=False)
            if res.returncode == 0 and res.stdout.strip() == "True":
                logger.info("[%s] Node is Ready", node_name)
                return
            self.sleeper(3.0)

        raise FleetDeploymentError(
            f"Node {node_name} failed to report Ready within {self.ready_timeout}s"
        )

    def wait_postgres_ready(self) -> None:
        if self.dry_run:
            logger.info(
                "[homelab-01] [DRY RUN] Would wait for PostgreSQL deployments to be Ready"
            )
            return

        logger.info(
            "[homelab-01] Waiting for PostgreSQL workloads to report Ready (timeout=%.0fs)...",
            self.ready_timeout,
        )
        pg_deployments = [
            ("home-assistant-postgres", "home-assistant"),
            ("immich-postgres", "immich"),
        ]
        for dep, ns in pg_deployments:
            logger.info(
                "[homelab-01] Waiting for rollout status of %s in %s...", dep, ns
            )
            cmd = [
                "kubectl",
                "rollout",
                "status",
                f"deployment/{dep}",
                "-n",
                ns,
                f"--timeout={int(self.ready_timeout)}s",
            ]
            run_command(cmd, runner=self.runner, timeout=self.ready_timeout + 5.0)

    def verify_dns_appliance(self, ip: str) -> None:
        if self.dry_run:
            logger.info("[%s] [DRY RUN] Would verify DNS resolution on port 53", ip)
            return

        logger.info("Verifying DNS responsiveness on %s:53...", ip)
        cmd = ["dig", f"@{ip}", "homelab.internal", "+short", "+timeout=2"]
        try:
            res = run_command(cmd, runner=self.runner, timeout=5.0, check=False)
            if res.returncode == 0:
                logger.info("DNS verification passed via dig for %s", ip)
                return
        except FleetDeploymentError as err:
            logger.warning(
                "DNS verification via dig failed, falling back to socket check: %s", err
            )

        if not self.socket_checker(ip, 53, 3.0):
            raise FleetDeploymentError(
                f"DNS verification failed on {ip}: port 53 not reachable"
            )
        logger.info("DNS verification passed via port 53 socket check for %s", ip)

    def verify_nas_appliance(self, ip: str) -> None:
        if self.dry_run:
            logger.info(
                "[%s] [DRY RUN] Would verify NFS (port 2049) and Attic (port 8080)",
                ip,
            )
            return

        logger.info("Verifying NFS port 2049 on %s...", ip)
        if not self.socket_checker(ip, 2049, 3.0):
            raise FleetDeploymentError(
                f"NAS verification failed: NFS port 2049 not reachable on {ip}"
            )
        logger.info("NFS port 2049 check passed for %s", ip)

        logger.info("Verifying Attic binary cache on http://%s:8080/...", ip)
        if not self.http_checker(f"http://{ip}:8080/", 5.0):
            raise FleetDeploymentError(
                f"NAS verification failed: Attic HTTP endpoint on http://{ip}:8080/ not responding"
            )
        logger.info("Attic binary cache check passed for %s", ip)

    def cooldown(self, host_name: str) -> None:
        if self.dry_run or self.skip_cooldown or self.cooldown_seconds <= 0:
            return
        logger.info(
            "[%s] Cooling down for %.1fs before next operation...",
            host_name,
            self.cooldown_seconds,
        )
        self.sleeper(self.cooldown_seconds)

    def deploy_host(self, host: HostConfig) -> None:
        logger.info(
            "----------------------------------------------------------------------"
        )
        logger.info(
            "Starting deployment of %s (IP: %s, Type: %s, Order: %d)",
            host.name,
            host.ip,
            host.host_type,
            host.order,
        )
        logger.info(
            "----------------------------------------------------------------------"
        )

        is_k3s = host.host_type in ("k3s-worker", "k3s-stateful")
        is_stateful_k3s = host.host_type == "k3s-stateful"
        cordoned = False

        try:
            # 1. Cordon & Drain if K3s node and not skipped
            if is_k3s and not self.skip_drain:
                self.cordon_node(host.name)
                cordoned = True
                self.drain_node(host.name)

            # 2. NixOS Rebuild Switch / Dry-activate
            self.rebuild_host(host)

            # 3. Post-switch actions
            if is_k3s:
                self.wait_node_ready(host.name)
                if cordoned:
                    self.uncordon_node(host.name)
                    cordoned = False

                if is_stateful_k3s:
                    self.wait_postgres_ready()
                    bgp_ok, bgp_msg = verify_bgp_peer(runner=self.runner, timeout=15.0)
                    if not bgp_ok:
                        logger.warning(
                            "Post-switch BGP peer check warning on %s: %s",
                            host.name,
                            bgp_msg,
                        )
                    else:
                        logger.info("Post-switch Cilium BGP verified on %s", host.name)

            elif host.host_type == "appliance-dns":
                self.verify_dns_appliance(host.ip)

            elif host.host_type == "appliance-nas":
                self.verify_nas_appliance(host.ip)

            # 4. Cooldown
            self.cooldown(host.name)
            logger.info("Successfully completed deployment for %s", host.name)

        except Exception as err:
            logger.error(
                "[CIRCUIT BREAKER] Host %s failed during deployment: %s",
                host.name,
                err,
            )
            # Safe recovery attempt: if node is still cordoned, uncordon it
            if cordoned:
                logger.info(
                    "[CIRCUIT BREAKER] Attempting to uncordon %s during recovery...",
                    host.name,
                )
                try:
                    self.uncordon_node(host.name)
                except Exception as uncordon_err:
                    logger.error(
                        "Failed to uncordon %s during recovery: %s",
                        host.name,
                        uncordon_err,
                    )
            raise

    def execute(self) -> dict[str, Any]:
        started = time.monotonic()
        results: list[dict[str, Any]] = []

        try:
            if not self.skip_preflight:
                self.run_preflight()

            for host in self.targets:
                host_started = time.monotonic()
                self.deploy_host(host)
                results.append(
                    {
                        "host": host.name,
                        "ip": host.ip,
                        "type": host.host_type,
                        "status": "success",
                        "duration_seconds": round(time.monotonic() - host_started, 2),
                    }
                )

            total_duration = round(time.monotonic() - started, 2)
            logger.info(
                "=== Fleet deployment completed successfully in %.2fs ===",
                total_duration,
            )
            if not self.dry_run:
                hosts_str = ", ".join(h.name for h in self.targets)
                self.notify(
                    title="Fleet Deployment Succeeded",
                    message=f"Successfully deployed {len(self.targets)} host(s) ({hosts_str}) in {total_duration:.1f}s",
                    priority="default",
                    tags=["white_check_mark", "rocket"],
                )
            return {
                "status": "success",
                "dry_run": self.dry_run,
                "total_duration_seconds": total_duration,
                "hosts": results,
            }
        except Exception as err:
            self.notify(
                title="[ALERT] Fleet Deployment Halted",
                message=f"Circuit breaker halted deployment: {err}",
                priority="urgent",
                tags=["rotating_light", "warning"],
            )
            raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Automated rolling fleet deployment orchestrator for homelab hosts."
    )
    parser.add_argument(
        "--target",
        dest="targets",
        action="append",
        help="Deploy only specified host(s). Can be specified multiple times or comma-separated.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate rolling deployment using dry-activate and client dry-runs.",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip pre-flight connectivity and node readiness checks.",
    )
    parser.add_argument(
        "--skip-drain",
        action="store_true",
        help="Skip cordon and drain steps on K3s nodes.",
    )
    parser.add_argument(
        "--skip-cooldown",
        action="store_true",
        help="Skip cooldown waiting periods between hosts.",
    )
    parser.add_argument(
        "--cooldown-seconds",
        type=float,
        default=30.0,
        help="Duration in seconds to wait after each host update (default: 30.0).",
    )
    parser.add_argument(
        "--drain-timeout",
        type=float,
        default=120.0,
        help="Timeout in seconds for kubectl drain (default: 120.0).",
    )
    parser.add_argument(
        "--ready-timeout",
        type=float,
        default=180.0,
        help="Timeout in seconds for node and workload readiness (default: 180.0).",
    )
    parser.add_argument(
        "--ssh-user",
        default="rupan",
        help="SSH username for remote targets (default: rupan).",
    )
    parser.add_argument(
        "--flake-path",
        default="./flake",
        help="Path to the NixOS flake (default: ./flake).",
    )
    parser.add_argument(
        "--lock-file",
        default="/tmp/deploy-fleet.lock",
        help="Path to the deployment lockfile (default: /tmp/deploy-fleet.lock).",
    )
    parser.add_argument(
        "--ntfy-topic",
        default=os.getenv("NTFY_TOPIC"),
        help="ntfy topic to send notifications to (default: $NTFY_TOPIC or homelab-alerts).",
    )
    parser.add_argument(
        "--ntfy-url",
        default=os.getenv("NTFY_URL"),
        help="ntfy server URL (default: $NTFY_URL or https://ntfy.rupan.dev).",
    )
    parser.add_argument(
        "--ntfy-token",
        default=os.getenv("NTFY_TOKEN"),
        help="Bearer token for ntfy authentication (default: $NTFY_TOKEN).",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Disable ntfy push notifications.",
    )
    parser.add_argument(
        "--ssh-opts",
        default=None,
        help="Extra SSH options or flags (default: $NIX_SSHOPTS or $SSH_OPTS).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured JSON results upon completion.",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    runner: Runner = subprocess.run,
    sleeper: Sleeper = time.sleep,
    socket_checker: SocketChecker = default_socket_checker,
    http_checker: HttpChecker = default_http_checker,
    notifier: Notifier | None = None,
) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        targets = parse_targets(args.targets)
    except FleetDeploymentError as err:
        logger.error("Configuration error: %s", err)
        return 2

    ssh_opts_raw = args.ssh_opts or os.getenv("NIX_SSHOPTS") or os.getenv("SSH_OPTS")
    ssh_opts = shlex.split(ssh_opts_raw) if ssh_opts_raw else None

    lock = FleetLock(
        Path(args.lock_file),
        metadata={
            "targets": [asdict(t) for t in targets],
            "dry_run": args.dry_run,
        },
    )

    try:
        with lock:
            deployer = FleetDeployer(
                targets,
                dry_run=args.dry_run,
                skip_preflight=args.skip_preflight,
                skip_drain=args.skip_drain,
                skip_cooldown=args.skip_cooldown,
                cooldown_seconds=args.cooldown_seconds,
                drain_timeout=args.drain_timeout,
                ready_timeout=args.ready_timeout,
                ssh_user=args.ssh_user,
                flake_path=args.flake_path,
                runner=runner,
                sleeper=sleeper,
                socket_checker=socket_checker,
                http_checker=http_checker,
                enable_notifications=not args.no_notify,
                ntfy_topic=args.ntfy_topic,
                ntfy_url=args.ntfy_url,
                ntfy_token=args.ntfy_token,
                notifier=notifier,
                ssh_opts=ssh_opts,
            )
            result = deployer.execute()
            if args.json:
                print(json.dumps(result, indent=2))
            return 0
    except LockContentionError as err:
        logger.error(str(err))
        return 3
    except FleetDeploymentError as err:
        logger.error("Fleet deployment halted by circuit breaker: %s", err)
        return 1
    except Exception as err:
        logger.exception("Unexpected error during fleet deployment: %s", err)
        return 1


if __name__ == "__main__":
    sys.exit(main())
