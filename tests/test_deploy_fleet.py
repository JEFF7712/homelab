import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.deploy_fleet import (
    FLEET_HOSTS,
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
                    if argv[:2] == ["nix", "eval"]:
                        return subprocess.CompletedProcess(
                            argv, 0, '["tank/forgejo", "tank/s3"]', ""
                        )
                    return subprocess.CompletedProcess(
                        argv, 1, "", "cannot open 'tank/s3': dataset does not exist"
                    )

                deployer = FleetDeployer(
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

        deployer = FleetDeployer(targets=[FLEET_HOSTS["nas-01"]], runner=runner)
        deployer.rebuild_host(FLEET_HOSTS["nas-01"])
        self.assertEqual([cmd[0] for cmd in commands], ["nix", "ssh", "nixos-rebuild"])
        self.assertIn("tank/forgejo tank/s3", commands[1][-1])

    def test_nas_storage_evaluation_failure_blocks_rebuild(self) -> None:
        runner = MagicMock(
            return_value=subprocess.CompletedProcess(["nix"], 1, "", "eval failed")
        )
        deployer = FleetDeployer(targets=[FLEET_HOSTS["nas-01"]], runner=runner)
        with self.assertRaisesRegex(FleetDeploymentError, "eval failed"):
            deployer.rebuild_host(FLEET_HOSTS["nas-01"])
        self.assertEqual(runner.call_count, 1)

    def test_nas_invalid_storage_evaluation_blocks_rebuild(self) -> None:
        for output in ("", "{}", "[null]", '["-bad"]'):
            with self.subTest(output=output):
                runner = MagicMock(
                    return_value=subprocess.CompletedProcess(["nix"], 0, output, "")
                )
                deployer = FleetDeployer(targets=[FLEET_HOSTS["nas-01"]], runner=runner)
                with self.assertRaisesRegex(
                    FleetDeploymentError, "NAS storage evaluation"
                ):
                    deployer.rebuild_host(FLEET_HOSTS["nas-01"])
                self.assertEqual(runner.call_count, 1)

    def test_parse_targets_default_order(self) -> None:
        targets = parse_targets(None)
        names = [t.name for t in targets]
        self.assertEqual(
            names,
            [
                "homelab-02",
                "homelab-03",
                "homelab-04",
                "homelab-05",
                "homelab-01",
                "adguard-netbird-01",
                "nas-01",
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

        deployer = FleetDeployer(
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
        self.assertIn("nixos-rebuild switch", cmd_verbs)
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

        deployer = FleetDeployer(
            targets=[FLEET_HOSTS["homelab-01"]],
            skip_preflight=True,
            skip_cooldown=True,
            runner=mock_runner,
        )

        result = deployer.execute()
        self.assertEqual(result["status"], "success")

        rollout_cmds = [
            " ".join(c) for c in executed_commands if "rollout" in c and "status" in c
        ]
        self.assertEqual(len(rollout_cmds), 2)
        self.assertTrue(any("home-assistant-postgres" in cmd for cmd in rollout_cmds))
        self.assertTrue(any("immich-postgres" in cmd for cmd in rollout_cmds))

    def test_deployer_appliances(self) -> None:
        executed_commands: list[list[str]] = []

        def mock_runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            executed_commands.append(argv)
            if argv[:2] == ["nix", "eval"]:
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

        deployer = FleetDeployer(
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

        # NAS appliance should check port 2049 and Attic URL
        self.assertIn(("10.0.30.20", 2049), checked_sockets)
        self.assertIn("http://10.0.30.20:8080/", checked_urls)

    def test_circuit_breaker_halts_on_failure_and_uncordons(self) -> None:
        executed_commands: list[list[str]] = []

        def mock_runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            executed_commands.append(argv)
            # Fail rebuild on homelab-02
            if "nixos-rebuild" in argv:
                return subprocess.CompletedProcess(
                    argv, 1, "", "nixos-rebuild switch failed"
                )
            return subprocess.CompletedProcess(argv, 0, "", "")

        deployer = FleetDeployer(
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
        self.assertEqual(hosts_targeted, ["./flake#homelab-02"])

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

        deployer = FleetDeployer(
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
                self.assertEqual(cmd[1], "dry-activate")

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
            return subprocess.CompletedProcess(argv, 0, "", "")

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

        deployer = FleetDeployer(
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

        deployer = FleetDeployer(
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

        deployer = FleetDeployer(
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


if __name__ == "__main__":
    unittest.main()
