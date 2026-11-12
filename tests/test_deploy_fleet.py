import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.deploy_fleet import (
    APPLIANCE_SETTLE_ATTEMPTS,
    CORDON_MARK,
    FLEET_HOSTS,
    POSTGRES_CLUSTERS,
    ClusterLease,
    FleetDeployer,
    FleetDeploymentError,
    FleetLock,
    LockContentionError,
    build_parser,
    default_ntfy_notifier,
    main,
    parse_targets,
    verify_bgp_peer,
    verify_k3s_nodes_ready,
    verify_ssh,
)


class DeployFleetTest(unittest.TestCase):
    OLD = "/nix/store/" + "a" * 32 + "-nixos-system-old"
    NEW = "/nix/store/" + "b" * 32 + "-nixos-system-new"

    def deployer(self, **kwargs) -> FleetDeployer:
        """Model remote systems for existing deployment/probe tests."""
        runner = kwargs.get("runner", subprocess.run)
        running: dict[str, str] = {}

        def with_systems(argv, **options):
            result = runner(argv, **options)
            if argv[:3] == ["nix", "eval", "--raw"]:
                return subprocess.CompletedProcess(argv, 0, self.NEW, "")
            if argv[0] == "ssh" and argv[-1] == "readlink -e /run/current-system":
                return subprocess.CompletedProcess(
                    argv, 0, running.get(argv[-2], self.OLD), ""
                )
            if (
                argv[0] == "ssh"
                and result.returncode == 0
                and "/bin/switch-to-configuration switch" in argv[-1]
            ):
                running[argv[-2]] = self.OLD if self.OLD in argv[-1] else self.NEW
            return result

        return FleetDeployer(**dict(kwargs, runner=with_systems))

    def test_drain_outlasts_a_slow_pod_stop(self) -> None:
        # Regression (2026-11-10): Home Assistant stopped 128s after its
        # eviction and failed the 120s drain of homelab-03.
        self.assertGreaterEqual(build_parser().parse_args([]).drain_timeout, 300.0)
        self.assertGreaterEqual(FleetDeployer(targets=[]).drain_timeout, 300.0)

    def test_switch_survives_the_ci_job_that_drives_it(self) -> None:
        # Regression (2026-11-03): a switch of homelab-04 restarted Docker,
        # killed the deploying CI job and, inside its SSH session, stopped
        # mid-way with k3s down.
        commands: list[str] = []

        def runner(argv, **options):
            commands.append(argv[-1])
            return subprocess.CompletedProcess(argv, 0, "", "")

        deployer = FleetDeployer(targets=["homelab-04"], runner=runner)
        host = FLEET_HOSTS["homelab-04"]
        deployer.activate_system(host, self.NEW)
        deployer.activate_system(host, self.NEW, dry_run=True)
        switch, dry_run = commands
        self.assertIn(
            "sudo -n systemd-run --unit=homelab-fleet-switch --collect", switch
        )
        self.assertIn(
            "--wait " + self.NEW + "/bin/switch-to-configuration switch", switch
        )
        # Output tied to the SSH session made the switch panic on the broken
        # pipe when the CI job driving it died; it goes to the journal.
        self.assertNotIn("--pipe", switch)
        self.assertTrue(
            switch.startswith("sudo -n nix-env -p /nix/var/nix/profiles/system")
        )
        self.assertNotIn("systemd-run", dry_run)
        self.assertIn(self.NEW + "/bin/switch-to-configuration dry-activate", dry_run)

    def test_k3s_hosts_deploy_the_registry_variant(self) -> None:
        for name in (
            "homelab-01",
            "homelab-02",
            "homelab-03",
            "homelab-04",
            "homelab-05",
        ):
            self.assertEqual(FLEET_HOSTS[name].flake_attr, name + "-registry")
        for name in ("nas-01", "adguard-netbird-01"):
            self.assertEqual(FLEET_HOSTS[name].flake_attr, name)

    def test_nas_missing_dataset_blocks_rebuild_even_when_preflight_skipped(
        self,
    ) -> None:
        commands: list[list[str]] = []
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                commands.clear()

                def runner(
                    argv: list[str], **kwargs: object
                ) -> subprocess.CompletedProcess[str]:
                    del kwargs
                    commands.append(argv)
                    if argv[:2] == ["nix", "eval"] and "--raw" not in argv:
                        return subprocess.CompletedProcess(
                            argv, 0, '["tank/forgejo", "tank/s3"]', ""
                        )
                    return subprocess.CompletedProcess(
                        argv, 1, "", "cannot open 'tank/s3': dataset does not exist"
                    )

                deployer = self.deployer(
                    targets=[FLEET_HOSTS["nas-01"]],
                    dry_run=dry_run,
                    skip_preflight=True,
                    runner=runner,
                    enable_notifications=False,
                )
                with self.assertRaisesRegex(
                    FleetDeploymentError, "NAS storage preflight failed"
                ):
                    deployer.execute()
                self.assertFalse(any(cmd[0] == "nixos-rebuild" for cmd in commands))

    def test_nas_existing_datasets_are_checked_before_rebuild(self) -> None:
        commands: list[list[str]] = []

        def runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            commands.append(argv)
            output = (
                '["tank/forgejo", "tank/s3"]' if argv[:2] == ["nix", "eval"] else ""
            )
            return subprocess.CompletedProcess(argv, 0, output, "")

        deployer = self.deployer(targets=[FLEET_HOSTS["nas-01"]], runner=runner)
        deployer.rebuild_host(FLEET_HOSTS["nas-01"])
        storage = [cmd for cmd in commands if cmd[0] == "ssh" and "zfs list" in cmd[-1]]
        self.assertEqual(len(storage), 1)
        self.assertIn("tank/forgejo tank/s3", storage[0][-1])
        self.assertLess(
            commands.index(storage[0]),
            next(i for i, cmd in enumerate(commands) if cmd[0] == "nixos-rebuild"),
        )

    def test_nas_storage_evaluation_failure_blocks_rebuild(self) -> None:
        runner = MagicMock(
            return_value=subprocess.CompletedProcess(["nix"], 1, "", "eval failed")
        )
        deployer = self.deployer(targets=[FLEET_HOSTS["nas-01"]], runner=runner)
        with self.assertRaisesRegex(FleetDeploymentError, "eval failed"):
            deployer.verify_nas_datasets(FLEET_HOSTS["nas-01"])
        self.assertEqual(runner.call_count, 1)

    def test_nas_invalid_storage_evaluation_blocks_rebuild(self) -> None:
        for output in ("", "{}", "[null]", '["-bad"]'):
            with self.subTest(output=output):
                runner = MagicMock(
                    return_value=subprocess.CompletedProcess(["nix"], 0, output, "")
                )
                deployer = self.deployer(targets=[FLEET_HOSTS["nas-01"]], runner=runner)
                with self.assertRaisesRegex(
                    FleetDeploymentError, "NAS storage evaluation"
                ):
                    deployer.verify_nas_datasets(FLEET_HOSTS["nas-01"])
                self.assertEqual(runner.call_count, 1)

    def test_parse_targets_default_order(self) -> None:
        targets = parse_targets(None)
        names = [t.name for t in targets]
        self.assertEqual(
            names,
            [
                "homelab-02",
                "homelab-03",
                "homelab-01",
                "homelab-05",
                "adguard-netbird-01",
                "nas-01",
                # The CI host's switch kills the deploy job, so it goes last.
                "homelab-04",
            ],
        )

    def test_parse_targets_subset_preserves_canonical_order(self) -> None:
        targets = parse_targets(["nas-01", "homelab-01", "homelab-02"])
        names = [t.name for t in targets]
        self.assertEqual(names, ["homelab-02", "homelab-01", "nas-01"])

    def test_parse_targets_comma_separated(self) -> None:
        targets = parse_targets(["homelab-03,homelab-01"])
        names = [t.name for t in targets]
        self.assertEqual(names, ["homelab-03", "homelab-01"])

    def test_parse_targets_invalid_name(self) -> None:
        with self.assertRaises(FleetDeploymentError):
            parse_targets(["unknown-host"])

    def test_fleet_lock_acquire_and_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "deploy.lock"
            lock = FleetLock(lock_path, metadata={"test": True})
            with lock:
                self.assertTrue(lock_path.is_file())
                content = json.loads(lock_path.read_text())
                self.assertTrue(content["test"])
                self.assertIn("pid", content)
            self.assertFalse(lock_path.is_file())

    def test_fleet_lock_contention(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "deploy.lock"
            lock1 = FleetLock(lock_path)
            lock1.acquire()
            try:
                lock2 = FleetLock(lock_path)
                with self.assertRaises(LockContentionError):
                    lock2.acquire()
            finally:
                lock1.release()

    def test_verify_ssh_success(self) -> None:
        def runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            return subprocess.CompletedProcess(argv, 0, "", "")

        self.assertTrue(verify_ssh("10.0.30.12", "rupan", runner=runner))

    def test_verify_ssh_failure(self) -> None:
        def runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            return subprocess.CompletedProcess(argv, 255, "", "Connection refused")

        self.assertFalse(verify_ssh("10.0.30.12", "rupan", runner=runner))

    def test_verify_ssh_with_opts(self) -> None:
        recorded_argv: list[str] = []

        def runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            recorded_argv.extend(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")

        self.assertTrue(
            verify_ssh(
                "10.0.30.12",
                "rupan",
                runner=runner,
                ssh_opts=["-i", "/custom/key", "-o", "StrictHostKeyChecking=no"],
            )
        )
        self.assertIn("-i", recorded_argv)
        self.assertIn("/custom/key", recorded_argv)
        self.assertIn("StrictHostKeyChecking=no", recorded_argv)

    def test_verify_ssh_from_env(self) -> None:
        recorded_argv: list[str] = []

        def runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            recorded_argv.extend(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")

        with patch.dict(
            "os.environ", {"NIX_SSHOPTS": "-i /env/key -o IdentitiesOnly=yes"}
        ):
            self.assertTrue(verify_ssh("10.0.30.12", "rupan", runner=runner))
            self.assertIn("-i", recorded_argv)
            self.assertIn("/env/key", recorded_argv)
            self.assertIn("IdentitiesOnly=yes", recorded_argv)

    def test_verify_k3s_nodes_ready_all_ready(self) -> None:
        nodes_payload = {
            "items": [
                {
                    "metadata": {"name": "homelab-01"},
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                },
                {
                    "metadata": {"name": "homelab-02"},
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                },
                {
                    "metadata": {"name": "homelab-03"},
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                },
                {
                    "metadata": {"name": "homelab-04"},
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                },
                {
                    "metadata": {"name": "homelab-05"},
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                },
            ]
        }

        def runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            return subprocess.CompletedProcess(argv, 0, json.dumps(nodes_payload), "")

        ready, msg = verify_k3s_nodes_ready(runner=runner)
        self.assertTrue(ready)
        self.assertIn("All K3s nodes are Ready", msg)

    def test_verify_k3s_nodes_ready_one_not_ready(self) -> None:
        nodes_payload = {
            "items": [
                {
                    "metadata": {"name": "homelab-01"},
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                },
                {
                    "metadata": {"name": "homelab-02"},
                    "status": {"conditions": [{"type": "Ready", "status": "False"}]},
                },
                {
                    "metadata": {"name": "homelab-03"},
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                },
                {
                    "metadata": {"name": "homelab-04"},
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                },
                {
                    "metadata": {"name": "homelab-05"},
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                },
            ]
        }

        def runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            return subprocess.CompletedProcess(argv, 0, json.dumps(nodes_payload), "")

        ready, msg = verify_k3s_nodes_ready(runner=runner)
        self.assertFalse(ready)
        self.assertIn("Node homelab-02 is NotReady", msg)

    def test_verify_k3s_nodes_ready_missing_node(self) -> None:
        nodes_payload = {
            "items": [
                {
                    "metadata": {"name": "homelab-01"},
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                },
                {
                    "metadata": {"name": "homelab-02"},
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                },
            ]
        }

        def runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            return subprocess.CompletedProcess(argv, 0, json.dumps(nodes_payload), "")

        ready, msg = verify_k3s_nodes_ready(runner=runner)
        self.assertFalse(ready)
        self.assertIn(
            "Cluster missing expected nodes: homelab-03, homelab-04, homelab-05", msg
        )

    def test_verify_bgp_peer_success(self) -> None:
        def runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            if "get" in argv and "pods" in argv:
                return subprocess.CompletedProcess(argv, 0, "cilium-dgfw4\n", "")
            if "cilium-dbg" in argv:
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    "Session State: established\nPeer: 10.0.30.1\n",
                    "",
                )
            return subprocess.CompletedProcess(argv, 0, "", "")

        ok, msg = verify_bgp_peer(runner=runner)
        self.assertTrue(ok)
        self.assertIn("established", msg)

    def test_deployer_k3s_worker_sequence(self) -> None:
        executed_commands: list[list[str]] = []

        def mock_runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            executed_commands.append(argv)
            if "node" in argv and "get" in argv:
                return subprocess.CompletedProcess(argv, 0, "True", "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        sleeps: list[float] = []

        deployer = self.deployer(
            targets=[FLEET_HOSTS["homelab-02"]],
            skip_preflight=True,
            cooldown_seconds=10.0,
            runner=mock_runner,
            sleeper=sleeps.append,
        )

        result = deployer.execute()
        self.assertEqual(result["status"], "success")

        # Verify command sequence: cordon -> drain -> rebuild switch -> wait node ready -> uncordon
        cmd_verbs = [" ".join(c[:2]) for c in executed_commands]
        self.assertIn("kubectl cordon", cmd_verbs)
        self.assertIn("kubectl drain", cmd_verbs)
        self.assertIn("nixos-rebuild build", cmd_verbs)
        build = next(
            i
            for i, cmd in enumerate(executed_commands)
            if cmd[:2] == ["nixos-rebuild", "build"]
        )
        drain = next(
            i
            for i, cmd in enumerate(executed_commands)
            if cmd[:2] == ["kubectl", "drain"]
        )
        self.assertLess(build, drain)
        self.assertNotIn("--no-link", executed_commands[build])
        self.assertTrue(
            any(
                cmd[0] == "ssh"
                and self.NEW + "/bin/switch-to-configuration switch" in cmd[-1]
                for cmd in executed_commands
            )
        )
        self.assertIn("kubectl uncordon", cmd_verbs)
        self.assertEqual(sleeps, [10.0])

    def test_deployer_stateful_anchor_verifies_postgres(self) -> None:
        executed_commands: list[list[str]] = []

        def mock_runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            executed_commands.append(argv)
            if "node" in argv and "get" in argv:
                return subprocess.CompletedProcess(argv, 0, "True", "")
            if "get" in argv and "pods" in argv:
                return subprocess.CompletedProcess(argv, 0, "cilium-dgfw4\n", "")
            if "cilium-dbg" in argv:
                return subprocess.CompletedProcess(
                    argv, 0, "Session State: established\n", ""
                )
            return subprocess.CompletedProcess(argv, 0, "", "")

        deployer = self.deployer(
            targets=[FLEET_HOSTS["homelab-01"]],
            skip_preflight=True,
            skip_cooldown=True,
            runner=mock_runner,
        )

        result = deployer.execute()
        self.assertEqual(result["status"], "success")

        self.assertEqual(
            [cmd for cmd in executed_commands if cmd[:2] == ["kubectl", "wait"]],
            [
                [
                    "kubectl",
                    "wait",
                    "--for=condition=Ready",
                    f"clusters.postgresql.cnpg.io/{cluster}",
                    "-n",
                    namespace,
                    "--timeout=180s",
                ]
                for namespace, cluster in POSTGRES_CLUSTERS
            ],
        )

    def test_every_cloudnativepg_cluster_is_awaited(self) -> None:
        # umami-cluster was missing from the post-switch wait (2026-11-11).
        import yaml

        declared = set()
        for path in (Path(__file__).resolve().parents[1] / "gitops").rglob("*.yaml"):
            if "postgresql.cnpg.io" not in path.read_text():
                continue
            for document in yaml.safe_load_all(path.read_text()):
                if (
                    isinstance(document, dict)
                    and document.get("kind") == "Cluster"
                    and str(document.get("apiVersion", "")).startswith(
                        "postgresql.cnpg.io/"
                    )
                ):
                    metadata = document["metadata"]
                    declared.add((metadata["namespace"], metadata["name"]))
        self.assertEqual(declared, set(POSTGRES_CLUSTERS))

    def test_deployer_appliances(self) -> None:
        executed_commands: list[list[str]] = []

        def mock_runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            executed_commands.append(argv)
            if argv[:2] == ["nix", "eval"] and "--raw" not in argv:
                return subprocess.CompletedProcess(
                    argv, 0, '["tank/media", "tank/attic"]', ""
                )
            return subprocess.CompletedProcess(argv, 0, "", "")

        checked_sockets: list[tuple[str, int]] = []
        checked_urls: list[str] = []

        def mock_socket(host: str, port: int, timeout: float) -> bool:
            del timeout
            checked_sockets.append((host, port))
            return True

        def mock_http(url: str, timeout: float) -> bool:
            del timeout
            checked_urls.append(url)
            return True

        deployer = self.deployer(
            targets=[FLEET_HOSTS["adguard-netbird-01"], FLEET_HOSTS["nas-01"]],
            skip_preflight=True,
            skip_cooldown=True,
            runner=mock_runner,
            socket_checker=mock_socket,
            http_checker=mock_http,
        )

        result = deployer.execute()
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["hosts"]), 2)

        # The NAS is checked for a listening NFS server over SSH, never a
        # direct socket: CI containers are firewalled off 2049.
        self.assertNotIn(("10.0.30.20", 2049), checked_sockets)
        nfs = [
            argv
            for argv in executed_commands
            if argv[0] == "ssh" and "nfs-server.service" in argv[-1]
        ]
        self.assertEqual(len(nfs), 1)
        self.assertEqual(nfs[0][-2], "rupan@10.0.30.20")
        self.assertNotIn("http://10.0.30.20:8080/", checked_urls)
        self.assertIn("http://10.0.30.20:8090/nix-cache-info", checked_urls)

    def test_nas_probes_wait_for_activation_to_settle(self) -> None:
        for failures in (3, 100):
            with self.subTest(failures=failures):
                probes: list[list[str]] = []

                def runner(
                    argv: list[str], **kwargs: object
                ) -> subprocess.CompletedProcess[str]:
                    del kwargs
                    if argv[:2] == ["nix", "eval"] and "--raw" not in argv:
                        return subprocess.CompletedProcess(argv, 0, "[]", "")
                    if argv[0] == "ssh" and "nfs-server.service" in argv[-1]:
                        probes.append(argv)
                        code = 0 if len(probes) > failures else 3
                        return subprocess.CompletedProcess(argv, code, "", "")
                    return subprocess.CompletedProcess(argv, 0, "", "")

                deployer = self.deployer(
                    targets=[FLEET_HOSTS["nas-01"]],
                    skip_preflight=True,
                    skip_cooldown=True,
                    runner=runner,
                    socket_checker=lambda host, port, timeout: False,
                    http_checker=lambda url, timeout: True,
                    sleeper=lambda seconds: None,
                )
                if failures < APPLIANCE_SETTLE_ATTEMPTS:
                    self.assertEqual(deployer.execute()["status"], "success")
                else:
                    with self.assertRaisesRegex(FleetDeploymentError, "NFS server"):
                        deployer.execute()
                self.assertEqual(
                    len(probes),
                    min(failures + 1, APPLIANCE_SETTLE_ATTEMPTS)
                    * (2 if failures >= APPLIANCE_SETTLE_ATTEMPTS else 1),
                )

    def test_dns_verification_waits_for_adguard_to_listen(self) -> None:
        # Regression (2026-11-10): AdGuard Home was still restarting when the
        # single probe ran, and a healthy switch was rolled back.
        for failures in (0, 3, APPLIANCE_SETTLE_ATTEMPTS):
            with self.subTest(failures=failures):
                probes: list[list[str]] = []

                def runner(
                    argv: list[str], **kwargs: object
                ) -> subprocess.CompletedProcess[str]:
                    del kwargs
                    if argv[0] == "dig":
                        probes.append(argv)
                        answered = len(probes) > failures
                        return subprocess.CompletedProcess(
                            argv, 0, "10.0.30.10\n" if answered else "", ""
                        )
                    return subprocess.CompletedProcess(argv, 0, "", "")

                deployer = FleetDeployer(
                    targets=[],
                    runner=runner,
                    socket_checker=lambda host, port, timeout: False,
                    sleeper=lambda seconds: None,
                )
                if failures < APPLIANCE_SETTLE_ATTEMPTS:
                    deployer.verify_dns_appliance("10.0.30.10")
                else:
                    with self.assertRaisesRegex(FleetDeploymentError, "port 53"):
                        deployer.verify_dns_appliance("10.0.30.10")
                self.assertEqual(
                    len(probes), min(failures + 1, APPLIANCE_SETTLE_ATTEMPTS)
                )

    def test_nas_verification_fails_when_niks3_stays_down(self) -> None:
        def runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            if argv[:2] == ["nix", "eval"] and "--raw" not in argv:
                return subprocess.CompletedProcess(argv, 0, "[]", "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        deployer = self.deployer(
            targets=[FLEET_HOSTS["nas-01"]],
            skip_preflight=True,
            skip_cooldown=True,
            runner=runner,
            socket_checker=lambda host, port, timeout: False,
            http_checker=lambda url, timeout: ":8090/" not in url,
            sleeper=lambda seconds: None,
        )
        with self.assertRaisesRegex(FleetDeploymentError, "niks3 cache"):
            deployer.execute()

    def test_circuit_breaker_halts_on_failure_and_uncordons(self) -> None:
        executed_commands: list[list[str]] = []

        def mock_runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            executed_commands.append(argv)
            if "node" in argv and "get" in argv:
                return subprocess.CompletedProcess(argv, 0, "True", "")
            # Fail activation on homelab-02
            if (
                argv[0] == "ssh"
                and self.NEW + "/bin/switch-to-configuration switch" in argv[-1]
            ):
                return subprocess.CompletedProcess(argv, 1, "", "activation failed")
            return subprocess.CompletedProcess(argv, 0, "", "")

        deployer = self.deployer(
            targets=[FLEET_HOSTS["homelab-02"], FLEET_HOSTS["homelab-03"]],
            skip_preflight=True,
            skip_cooldown=True,
            runner=mock_runner,
        )

        with self.assertRaises(FleetDeploymentError):
            deployer.execute()

        # Check that homelab-03 was NEVER touched
        hosts_targeted = [
            c[c.index("--flake") + 1] for c in executed_commands if "--flake" in c
        ]
        self.assertEqual(
            hosts_targeted,
            [
                str(Path("./flake").resolve()) + "#homelab-02-registry",
                str(Path("./flake").resolve()) + "#homelab-03-registry",
            ],
        )
        self.assertFalse(
            any(
                cmd[0] == "ssh"
                and "@10.0.30.13" in cmd[-2]
                and "/bin/switch-to-configuration switch" in cmd[-1]
                for cmd in executed_commands
            )
        )
        self.assertTrue(
            any(
                cmd[0] == "ssh"
                and self.OLD + "/bin/switch-to-configuration switch" in cmd[-1]
                for cmd in executed_commands
            )
        )

        # Check that uncordon was attempted during recovery of homelab-02
        uncordon_cmds = [
            c
            for c in executed_commands
            if len(c) >= 3 and c[0] == "kubectl" and c[1] == "uncordon"
        ]
        self.assertTrue(len(uncordon_cmds) >= 1)
        self.assertEqual(uncordon_cmds[-1][2], "homelab-02")

    def test_dry_run_flags(self) -> None:
        executed_commands: list[list[str]] = []

        def mock_runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            executed_commands.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")

        deployer = self.deployer(
            targets=[FLEET_HOSTS["homelab-02"]],
            dry_run=True,
            skip_preflight=True,
            runner=mock_runner,
        )

        result = deployer.execute()
        self.assertTrue(result["dry_run"])

        # In dry run mode, cordon and drain should have --dry-run=client
        # and nixos-rebuild should be dry-activate
        for cmd in executed_commands:
            if cmd[0] == "kubectl" and cmd[1] in ("cordon", "drain"):
                self.assertIn("--dry-run=client", cmd)
            if cmd[0] == "nixos-rebuild":
                self.assertEqual(cmd[1], "build")

    def test_cli_parser(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "--target",
                "homelab-02",
                "--dry-run",
                "--skip-preflight",
                "--cooldown-seconds",
                "15",
            ]
        )
        self.assertEqual(args.targets, ["homelab-02"])
        self.assertTrue(args.dry_run)
        self.assertTrue(args.skip_preflight)
        self.assertEqual(args.cooldown_seconds, 15.0)

    def test_main_cli_execution_success(self) -> None:
        def mock_runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            output = (
                self.NEW
                if argv[:3] == ["nix", "eval", "--raw"]
                else self.OLD
                if argv[0] == "ssh" and argv[-1] == "readlink -e /run/current-system"
                else ""
            )
            return subprocess.CompletedProcess(argv, 0, output, "")

        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = str(Path(tmpdir) / "test.lock")
            ret = main(
                [
                    "--target",
                    "homelab-02",
                    "--dry-run",
                    "--skip-preflight",
                    "--lock-file",
                    lock_path,
                ],
                runner=mock_runner,
            )
            self.assertEqual(ret, 0)

    def test_notification_on_success(self) -> None:
        notifications: list[dict[str, object]] = []

        def mock_notifier(
            title: str, message: str, priority: str, tags: list[str] | None
        ) -> bool:
            notifications.append(
                {"title": title, "message": message, "priority": priority, "tags": tags}
            )
            return True

        def mock_runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            if "node" in argv and "get" in argv:
                return subprocess.CompletedProcess(argv, 0, "True", "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        deployer = self.deployer(
            targets=[FLEET_HOSTS["homelab-02"]],
            dry_run=False,
            skip_preflight=True,
            skip_cooldown=True,
            runner=mock_runner,
            notifier=mock_notifier,
        )
        deployer.execute()

        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["title"], "Fleet Deployment Succeeded")
        self.assertEqual(notifications[0]["priority"], "default")

    def test_notification_on_failure(self) -> None:
        notifications: list[dict[str, object]] = []

        def mock_notifier(
            title: str, message: str, priority: str, tags: list[str] | None
        ) -> bool:
            notifications.append(
                {"title": title, "message": message, "priority": priority, "tags": tags}
            )
            return True

        def mock_runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            if "nixos-rebuild" in argv:
                return subprocess.CompletedProcess(argv, 1, "", "rebuild switch error")
            return subprocess.CompletedProcess(argv, 0, "", "")

        deployer = self.deployer(
            targets=[FLEET_HOSTS["homelab-02"]],
            dry_run=False,
            skip_preflight=True,
            skip_cooldown=True,
            runner=mock_runner,
            notifier=mock_notifier,
        )

        with self.assertRaises(FleetDeploymentError):
            deployer.execute()

        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["title"], "[ALERT] Fleet Deployment Halted")
        self.assertEqual(notifications[0]["priority"], "urgent")

    def test_notification_disabled_with_no_notify(self) -> None:
        notifications: list[dict[str, object]] = []

        def mock_notifier(
            title: str, message: str, priority: str, tags: list[str] | None
        ) -> bool:
            notifications.append({"title": title, "message": message})
            return True

        def mock_runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            if "node" in argv and "get" in argv:
                return subprocess.CompletedProcess(argv, 0, "True", "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        deployer = self.deployer(
            targets=[FLEET_HOSTS["homelab-02"]],
            dry_run=False,
            skip_preflight=True,
            skip_cooldown=True,
            enable_notifications=False,
            runner=mock_runner,
            notifier=mock_notifier,
        )
        deployer.execute()
        self.assertEqual(len(notifications), 0)

    def test_default_ntfy_notifier_no_topic(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(default_ntfy_notifier("title", "msg"))

    def test_default_ntfy_notifier_success(self) -> None:
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__enter__.return_value = mock_response

        with patch(
            "urllib.request.urlopen", return_value=mock_response
        ) as mock_urlopen:
            result = default_ntfy_notifier(
                "Test Title",
                "Test Message",
                priority="high",
                tags=["rocket", "tada"],
                topic="my-topic",
                token="my-token",
            )
            self.assertTrue(result)
            mock_urlopen.assert_called_once()
            req = mock_urlopen.call_args[0][0]
            self.assertEqual(req.full_url, "https://ntfy.rupan.dev/my-topic")
            self.assertEqual(req.headers["Title"], "Test Title")
            self.assertEqual(req.headers["Priority"], "high")
            self.assertEqual(req.headers["Tags"], "rocket,tada")
            self.assertEqual(req.headers["Authorization"], "Bearer my-token")

    def test_default_ntfy_notifier_error(self) -> None:
        with patch("urllib.request.urlopen", side_effect=OSError("network down")):
            result = default_ntfy_notifier(
                "Test Title", "Test Message", topic="my-topic"
            )
            self.assertFalse(result)


class FleetClosureTest(unittest.TestCase):
    OLD = DeployFleetTest.OLD
    NEW = DeployFleetTest.NEW

    def run_deployment(
        self,
        *,
        unchanged=False,
        build_failure=False,
        failed_health=False,
        recovery_failure=False,
        node=None,
        failed_units="",
    ):
        self.commands = []
        self.build_directories = []
        self.active = self.OLD
        host = FLEET_HOSTS["homelab-02"]

        def runner(argv, **kwargs):
            self.commands.append(argv)
            if argv[:2] == ["nixos-rebuild", "build"]:
                self.build_directories.append(Path(kwargs["cwd"]))
                self.assertTrue(self.build_directories[-1].is_dir())
            output, code = "", 0
            if argv[:3] == ["nix", "eval", "--raw"]:
                output = self.OLD if unchanged else self.NEW
            elif argv[:2] == ["nixos-rebuild", "build"] and build_failure:
                code = 1
            elif argv[0] == "ssh":
                remote = argv[-1]
                if remote == "readlink -e /run/current-system":
                    output = self.active
                elif (
                    remote == "systemctl --failed --no-legend --plain"
                    and failed_health
                    and self.active == self.NEW
                ):
                    output = "application.service loaded failed failed Application\n"
                elif remote == "systemctl --failed --no-legend --plain":
                    output = failed_units
                elif "/bin/switch-to-configuration switch" in remote:
                    if self.OLD in remote and recovery_failure:
                        code = 1
                    else:
                        self.active = self.OLD if self.OLD in remote else self.NEW
            elif argv[:3] == ["kubectl", "get", "node"] and argv[-1] == "json":
                output = json.dumps(node or {})
            elif argv[:3] == ["kubectl", "get", "node"]:
                output = "True"
            return subprocess.CompletedProcess(
                argv, code, output, "fixture failure" if code else ""
            )

        deployer = FleetDeployer(
            targets=[host],
            runner=runner,
            skip_preflight=True,
            sleeper=lambda _: None,
            enable_notifications=False,
        )
        return deployer.execute()

    def test_unchanged_live_system_does_not_build_drain_or_activate(self):
        result = self.run_deployment(unchanged=True)
        self.assertEqual(result["hosts"][0]["status"], "unchanged")
        self.assertFalse(
            any(
                cmd[0] == "nixos-rebuild" or cmd[:2] != ["kubectl", "get"]
                for cmd in self.commands
                if cmd[0] in {"kubectl", "nixos-rebuild"}
            )
        )
        self.assertFalse(
            any(
                cmd[0] == "ssh" and "switch-to-configuration" in cmd[-1]
                for cmd in self.commands
            )
        )

    def test_build_result_link_is_isolated_and_its_directory_is_removed(self):
        self.run_deployment()
        self.assertEqual(len(self.build_directories), 1)
        self.assertFalse(self.build_directories[0].exists())
        self.assertNotEqual(self.build_directories[0], Path.cwd())

    def test_build_failure_leaves_workloads_and_live_system_untouched(self):
        with self.assertRaises(FleetDeploymentError):
            self.run_deployment(build_failure=True)
        self.assertEqual(self.active, self.OLD)
        self.assertFalse(
            any(
                cmd[:2] in (["kubectl", "cordon"], ["kubectl", "drain"])
                for cmd in self.commands
            )
        )
        self.assertTrue(
            any(
                cmd[0] == "ssh" and "rm -rf -- /tmp/homelab-fleet-" in cmd[-1]
                for cmd in self.commands
            )
        )

    def test_new_systemd_failure_restores_and_verifies_exact_previous_system(self):
        with self.assertRaisesRegex(FleetDeploymentError, "newly failed"):
            self.run_deployment(failed_health=True)
        self.assertEqual(self.active, self.OLD)
        rollback = next(
            i
            for i, cmd in enumerate(self.commands)
            if cmd[0] == "ssh"
            and self.OLD + "/bin/switch-to-configuration switch" in cmd[-1]
        )
        uncordon = next(
            i
            for i, cmd in enumerate(self.commands)
            if cmd[:2] == ["kubectl", "uncordon"]
        )
        self.assertLess(rollback, uncordon)

    def test_failed_recovery_keeps_unhealthy_node_cordoned_and_reports_path(self):
        with self.assertRaisesRegex(
            FleetDeploymentError, "recovery failed.*" + self.OLD
        ):
            self.run_deployment(failed_health=True, recovery_failure=True)
        self.assertFalse(
            any(cmd[:2] == ["kubectl", "uncordon"] for cmd in self.commands)
        )
        # The held cordon loses its mark, so later runs leave it alone.
        self.assertEqual(self.kubectl_mutations()[-1], ("annotate", f"{CORDON_MARK}-"))

    def kubectl_mutations(self):
        return [
            (cmd[1], cmd[-1] if cmd[1] == "annotate" else cmd[2])
            for cmd in self.commands
            if cmd[0] == "kubectl" and cmd[1] != "get"
        ]

    @staticmethod
    def node(*, cordoned=True, marked=True, ready=True):
        annotations = {CORDON_MARK: "true"} if marked else {}
        return {
            "metadata": {"name": "homelab-02", "annotations": annotations},
            "spec": {"unschedulable": True} if cordoned else {},
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "True" if ready else "False"}
                ]
            },
        }

    def test_deploy_marks_its_cordon_and_clears_the_mark_on_uncordon(self):
        self.run_deployment()
        self.assertEqual(
            self.kubectl_mutations(),
            [
                ("cordon", "homelab-02"),
                ("annotate", f"{CORDON_MARK}=true"),
                ("drain", "homelab-02"),
                ("uncordon", "homelab-02"),
                ("annotate", f"{CORDON_MARK}-"),
            ],
        )

    def test_unchanged_healthy_node_left_cordoned_by_a_killed_run_is_released(self):
        result = self.run_deployment(unchanged=True, node=self.node())
        self.assertEqual(result["hosts"][0]["status"], "unchanged")
        self.assertEqual(
            self.kubectl_mutations(),
            [("uncordon", "homelab-02"), ("annotate", f"{CORDON_MARK}-")],
        )

    def test_stale_cordon_is_kept_unless_marked_ready_and_without_failed_units(self):
        for case in (
            {"node": self.node(marked=False)},
            {"node": self.node(ready=False)},
            {
                "node": self.node(),
                "failed_units": "application.service loaded failed failed A\n",
            },
            {"node": self.node(cordoned=False)},
        ):
            with self.subTest(case):
                self.run_deployment(unchanged=True, **case)
                self.assertEqual(self.kubectl_mutations(), [])


class LeaseApi:
    """The Lease part of the Kubernetes API, with its create and replace rules."""

    def __init__(self) -> None:
        self.lease: dict | None = None
        self.version = 0
        self.calls: list[list[str]] = []

    def store(self, body: dict) -> None:
        self.version += 1
        body["metadata"]["resourceVersion"] = str(self.version)
        self.lease = body

    def hold(self, holder: str, renewed: datetime, duration: int = 300) -> None:
        stamp = renewed.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        self.store(
            {
                "metadata": {"name": "homelab-fleet", "namespace": "kube-system"},
                "spec": {
                    "holderIdentity": holder,
                    "leaseDurationSeconds": duration,
                    "renewTime": stamp,
                },
            }
        )

    def __call__(self, argv, **options):
        self.calls.append(argv)
        if argv[0] != "kubectl":
            return subprocess.CompletedProcess(argv, 0, "", "")
        verb = next(a for a in argv[1:] if not a.startswith("-") and a != "kube-system")
        body = json.loads(Path(argv[-1]).read_text()) if "-f" in argv else None
        if verb == "create":
            if self.lease is not None:
                return subprocess.CompletedProcess(argv, 1, "", "AlreadyExists")
            self.store(body)
        elif verb == "replace":
            if body["metadata"].get("resourceVersion") != str(self.version):
                return subprocess.CompletedProcess(argv, 1, "", "Conflict")
            self.store(body)
        elif verb == "get":
            if self.lease is None:
                return subprocess.CompletedProcess(argv, 1, "", "NotFound")
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.lease), "")
        elif verb == "patch":
            self.lease["spec"].update(json.loads(argv[argv.index("-p") + 1])["spec"])
        elif verb == "delete":
            self.lease = None
        return subprocess.CompletedProcess(argv, 0, "", "")


class ClusterLeaseTest(unittest.TestCase):
    NOW = datetime(2026, 11, 11, 9, 30, tzinfo=UTC)

    def lease(
        self, api: LeaseApi, holder: str = "deploy-fleet pipeline 2"
    ) -> ClusterLease:
        return ClusterLease(
            holder, runner=api, clock=lambda: self.NOW, renew_seconds=3600
        )

    def test_free_lease_is_taken_and_released(self) -> None:
        api = LeaseApi()
        with self.lease(api):
            self.assertEqual(
                api.lease["spec"]["holderIdentity"], "deploy-fleet pipeline 2"
            )
        self.assertIsNone(api.lease)

    def test_a_live_holder_blocks_a_second_run(self) -> None:
        # Regression (2026-11-10): three fleet deployments of one commit ran
        # at once on the CI host's two deploy slots.
        api = LeaseApi()
        api.hold("deploy-fleet pipeline 1", self.NOW - timedelta(seconds=90))
        with self.assertRaisesRegex(LockContentionError, "pipeline 1"):
            with self.lease(api):
                self.fail("entered while another run held the lease")
        self.assertEqual(api.lease["spec"]["holderIdentity"], "deploy-fleet pipeline 1")

    def test_an_expired_holder_is_taken_over(self) -> None:
        # A job killed mid-run (the CI host restarting its own agent) stops
        # renewing; the next run must not wait on it forever.
        api = LeaseApi()
        api.hold("deploy-fleet pipeline 1", self.NOW - timedelta(seconds=301))
        with self.lease(api):
            self.assertEqual(
                api.lease["spec"]["holderIdentity"], "deploy-fleet pipeline 2"
            )
        self.assertIsNone(api.lease)

    def test_takeover_loses_to_a_concurrent_change(self) -> None:
        api = LeaseApi()
        api.hold("deploy-fleet pipeline 1", self.NOW - timedelta(seconds=600))
        original = api.__call__

        def renewed_meanwhile(argv, **options):
            if "replace" in argv:
                api.hold("fleet-reboot pipeline 3", self.NOW)
            return original(argv, **options)

        with self.assertRaisesRegex(LockContentionError, "changed while"):
            ClusterLease(
                "deploy-fleet pipeline 2",
                runner=renewed_meanwhile,
                clock=lambda: self.NOW,
            ).acquire()
        self.assertEqual(api.lease["spec"]["holderIdentity"], "fleet-reboot pipeline 3")

    def test_release_leaves_a_lease_someone_else_took(self) -> None:
        api = LeaseApi()
        lease = self.lease(api)
        lease.acquire()
        api.hold("fleet-reboot pipeline 3", self.NOW)
        lease.release()
        self.assertEqual(api.lease["spec"]["holderIdentity"], "fleet-reboot pipeline 3")

    def test_renew_moves_the_renew_time(self) -> None:
        api = LeaseApi()
        lease = self.lease(api)
        lease.acquire()
        later = self.NOW + timedelta(minutes=1)
        lease.clock = lambda: later
        lease.renew()
        self.assertEqual(
            api.lease["spec"]["renewTime"], later.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        )

    def test_deployment_stops_when_another_run_holds_the_lease(self) -> None:
        api = LeaseApi()
        api.hold("fleet-reboot pipeline 1", datetime.now(UTC))
        with tempfile.TemporaryDirectory() as directory:
            code = main(
                [
                    "--target",
                    "homelab-02",
                    "--no-notify",
                    "--lock-file",
                    str(Path(directory) / "lock"),
                ],
                runner=api,
            )
        self.assertEqual(code, 3)
        self.assertFalse(any(argv[0] == "ssh" for argv in api.calls))

    def test_dry_runs_do_not_take_the_lease(self) -> None:
        api = LeaseApi()
        api.hold("deploy-fleet pipeline 1", datetime.now(UTC))
        with tempfile.TemporaryDirectory() as directory:
            main(
                [
                    "--dry-run",
                    "--skip-preflight",
                    "--target",
                    "homelab-02",
                    "--no-notify",
                    "--lock-file",
                    str(Path(directory) / "lock"),
                ],
                runner=api,
            )
        self.assertFalse(any("lease" in " ".join(argv) for argv in api.calls))


if __name__ == "__main__":
    unittest.main()
