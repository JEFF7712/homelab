from __future__ import annotations

import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.agent_workspaces.acceptance import (
    _direction_drop_counter,
    _probe_peer_denial,
    _validate_measured_load,
    _verify_guest_listeners,
    collect_host_health,
    run_real_access_acceptance,
    run_two_guest_acceptance,
)
from scripts.agent_workspaces.core import WorkspaceError, load_manifest
from tests.test_agent_workspaces import enabled_workspace, test_manifest


class HealthRunner:
    def __init__(self) -> None:
        self.slice_calls = 0
        self.nft_calls = 0
        self.postgres_primaries = "home-assistant-cluster-1"
        self.commands: list[list[str]] = []

    def __call__(self, command, **kwargs):
        del kwargs
        command = list(command)
        self.commands.append(command)
        if command[:2] == ["systemctl", "show"]:
            cpu_usage = self.slice_calls * 20_000_000_000
            self.slice_calls += 1
            return subprocess.CompletedProcess(
                command,
                0,
                f"CPUUsageNSec={cpu_usage}\nMemoryCurrent=1000\nMemoryPeak=2000\nCPUQuotaPerSecUSec=2s\nMemoryHigh=9663676416\nMemoryMax=10737418240\nManagedOOMMemoryPressure=kill\n",
                "",
            )
        if command[-4:] == ["get", "nodes", "-o", "json"]:
            output = {
                "items": [
                    {
                        "metadata": {"name": "homelab-01"},
                        "status": {
                            "conditions": [
                                {"type": "Ready", "status": "True"},
                                {"type": "EtcdIsVoter", "status": "True"},
                                {"type": "MemoryPressure", "status": "False"},
                                {"type": "DiskPressure", "status": "False"},
                                {"type": "PIDPressure", "status": "False"},
                            ]
                        },
                    }
                ]
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(output), "")
        if "--raw=/readyz" in command:
            return subprocess.CompletedProcess(command, 0, "ok\n", "")
        if any("cnpg.io/instanceRole=primary" in part for part in command):
            return subprocess.CompletedProcess(command, 0, self.postgres_primaries, "")
        if command[2:4] == ["exec", "-n"] and "psql" in command:
            if command[5] != "home-assistant-cluster-1":
                return subprocess.CompletedProcess(command, 1, "", "pod not found")
            return subprocess.CompletedProcess(command, 0, "1\n", "")
        if command[0] == "curl":
            return subprocess.CompletedProcess(command, 0, "200", "")
        if command[0] == "python3":
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[0] == "ssh-keygen":
            pathlib.Path(command[-1]).write_text("dummy_key")
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[0] == "nft":
            count = self.nft_calls
            self.nft_calls += 1
            return subprocess.CompletedProcess(
                command,
                0,
                f'iifname "aw-rupan-br" counter packets {count} bytes 0 drop\n'
                f'oifname "aw-accept-b-br" counter packets {count} bytes 0 drop\n'
                f'iifname "aw-accept-b-br" counter packets {count} bytes 0 drop\n'
                f'oifname "aw-rupan-br" counter packets {count} bytes 0 drop\n',
                "",
            )
        if command[0] == "ssh":
            if command[-1].startswith("ss -H"):
                return subprocess.CompletedProcess(command, 0, "LISTEN 0 128", "")
            if command[-1].startswith("python3 -c"):
                return subprocess.CompletedProcess(command, 0, "timeout", "")
            if command[-1].startswith("curl"):
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[-1] == "echo auth_ok":
                target = command[-2]
                is_primary_target = "192.0.2.2" in target or "rupan" in target
                has_primary_key = (
                    "-i" in command
                    and "id_dummy" not in command[command.index("-i") + 1]
                    and "id_ed25519" in command[command.index("-i") + 1]
                )
                has_password_auth = (
                    "-o" in command and "PreferredAuthentications=password" in command
                )
                if is_primary_target and has_primary_key and not has_password_auth:
                    return subprocess.CompletedProcess(command, 0, "auth_ok\n", "")
                return subprocess.CompletedProcess(
                    command, 255, "", "Permission denied (publickey).\n"
                )
            return subprocess.CompletedProcess(
                command,
                0,
                '{"disk_bytes":1073741824,"disk_seconds":1,"rx_bytes":70000000,"tx_bytes":3}',
                "",
            )
        if command[0] in {"ip", "virsh"}:
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)


def acceptance_workspaces(directory: str):
    first = enabled_workspace("acceptance-a")
    second = enabled_workspace("acceptance-b")
    second["owner"] = "acceptance-b@example.test"
    second["browser_identity"] = "acceptance-b@example.test"
    second["network"] = {
        **second["network"],
        "segment": "198.51.100.0/30",
        "address": "198.51.100.2/30",
        "gateway": "198.51.100.1",
        "mac": "02:00:00:00:00:02",
        "bridge": "aw-accept-b-br",
        "tap": "aw-accept-b-tp",
    }
    for item in (first, second):
        workspace_id = item["id"]
        item["storage"] = {
            "system_disk": f"/persist/agent-workspaces/{workspace_id}/disks/system.qcow2",
            "system_disk_gib": 16,
            "data_disk": f"/persist/agent-workspaces/{workspace_id}/disks/data.qcow2",
            "data_disk_gib": 16,
        }
    manifest = pathlib.Path(directory) / "manifest.json"
    manifest.write_text(json.dumps(test_manifest([first, second])))
    return load_manifest(manifest)


class AcceptanceTests(unittest.TestCase):
    def test_collect_health_requires_ready_etcd_postgres_and_ha(self) -> None:
        health = collect_host_health(HealthRunner())
        self.assertEqual(health["nodes"]["ready"], 1)
        self.assertEqual(health["etcd"]["voters"], 1)
        self.assertEqual(health["postgres"]["query"], "SELECT 1")
        self.assertEqual(health["home_assistant"]["http_status"], 200)
        self.assertEqual(health["workspace_slice"]["cpu_quota_per_second"], "2s")
        self.assertEqual(health["workspace_slice"]["memory_peak_bytes"], 2000)

    def test_collect_health_queries_the_current_cnpg_primary(self) -> None:
        runner = HealthRunner()
        collect_host_health(runner)
        query = next(command for command in runner.commands if "psql" in command)
        self.assertEqual(
            query[2:9],
            [
                "exec",
                "-n",
                "home-assistant",
                "home-assistant-cluster-1",
                "-c",
                "postgres",
                "--",
            ],
        )

    def test_collect_health_requires_exactly_one_cnpg_primary(self) -> None:
        for primaries in ("", "home-assistant-cluster-1 home-assistant-cluster-2"):
            with self.subTest(primaries=primaries):
                runner = HealthRunner()
                runner.postgres_primaries = primaries
                with self.assertRaisesRegex(WorkspaceError, "primary pod"):
                    collect_host_health(runner)
                self.assertFalse(any("psql" in command for command in runner.commands))

    def test_two_guest_acceptance_records_health_and_deprovisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspaces = acceptance_workspaces(directory)
            key = pathlib.Path(directory) / "id_ed25519"
            key.write_text("private")
            evidence = pathlib.Path(directory) / "evidence.json"
            status = {"consistent": True, "domain_state": "running"}
            with (
                patch("scripts.agent_workspaces.acceptance.os.geteuid", return_value=0),
                patch(
                    "scripts.agent_workspaces.acceptance.provision_status",
                    return_value=status,
                ),
                patch(
                    "scripts.agent_workspaces.acceptance.deprovision_workspace"
                ) as deprovision,
                patch("scripts.agent_workspaces.acceptance.time.sleep"),
                patch("scripts.agent_workspaces.acceptance._validate_measured_load"),
            ):
                result = run_two_guest_acceptance(
                    workspaces,
                    ssh_key=key,
                    network_url="https://example.test/pressure",
                    duration_seconds=10,
                    evidence_path=evidence,
                    runner=HealthRunner(),
                )
            self.assertEqual(result["result"], "accepted")
            self.assertEqual(set(result["health"]), {"before", "during", "after"})
            self.assertIsInstance(result["health"]["during"], list)
            self.assertEqual(deprovision.call_count, 2)
            self.assertEqual(json.loads(evidence.read_text())["result"], "accepted")

    def test_acceptance_rejects_non_disposable_ids_and_unsafe_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspaces = acceptance_workspaces(directory)
            key = pathlib.Path(directory) / "id_ed25519"
            key.write_text("private")
            workspaces[0].raw["id"] = "production-user"
            with self.assertRaisesRegex(WorkspaceError, "acceptance-\\*"):
                run_two_guest_acceptance(
                    workspaces,
                    ssh_key=key,
                    network_url="https://example.test/pressure",
                    duration_seconds=10,
                    evidence_path=pathlib.Path(directory) / "evidence.json",
                    runner=HealthRunner(),
                )
            workspaces[0].raw["id"] = "acceptance-a"
            with (
                patch("scripts.agent_workspaces.acceptance.os.geteuid", return_value=0),
                self.assertRaisesRegex(WorkspaceError, "absolute HTTPS"),
            ):
                run_two_guest_acceptance(
                    workspaces,
                    ssh_key=key,
                    network_url="http://example.test;false",
                    duration_seconds=10,
                    evidence_path=pathlib.Path(directory) / "evidence.json",
                    runner=HealthRunner(),
                )

    def test_cleanup_failure_prevents_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspaces = acceptance_workspaces(directory)
            key = pathlib.Path(directory) / "id_ed25519"
            key.write_text("private")
            evidence = pathlib.Path(directory) / "evidence.json"
            status = {"consistent": True, "domain_state": "running"}
            with (
                patch("scripts.agent_workspaces.acceptance.os.geteuid", return_value=0),
                patch(
                    "scripts.agent_workspaces.acceptance.provision_status",
                    return_value=status,
                ),
                patch(
                    "scripts.agent_workspaces.acceptance.deprovision_workspace",
                    side_effect=WorkspaceError("destroy failed"),
                ),
                patch("scripts.agent_workspaces.acceptance.time.sleep"),
                patch("scripts.agent_workspaces.acceptance._validate_measured_load"),
                self.assertRaisesRegex(WorkspaceError, "cleanup failed"),
            ):
                run_two_guest_acceptance(
                    workspaces,
                    ssh_key=key,
                    network_url="https://example.test/pressure",
                    duration_seconds=10,
                    evidence_path=evidence,
                    runner=HealthRunner(),
                )
            recorded = json.loads(evidence.read_text())
            self.assertEqual(recorded["result"], "failed")
            self.assertEqual(len(recorded["cleanup_errors"]), 2)

    def test_zero_or_short_load_is_rejected(self) -> None:
        evidence = {
            "duration_seconds": 60,
            "pressure": [
                {
                    "workspace_id": workspace_id,
                    "seconds": 0.1,
                    "disk_bytes": 0,
                    "rx_bytes": 0,
                }
                for workspace_id in ("acceptance-a", "acceptance-b")
            ],
            "health": {
                "before": {"workspace_slice": {"cpu_usage_nsec": 100}},
                "after": {"workspace_slice": {"cpu_usage_nsec": 100}},
            },
            "isolation": {
                "firewall_drop_delta": {"acceptance-a": 0, "acceptance-b": 0}
            },
        }
        with self.assertRaisesRegex(WorkspaceError, "ended too early"):
            _validate_measured_load(evidence)

    def test_isolation_requires_listeners_and_drop_counters_both_directions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspaces = acceptance_workspaces(directory)
            key = pathlib.Path(directory) / "id_ed25519"
            key.write_text("private")
            runner = HealthRunner()
            _verify_guest_listeners(workspaces, key, runner)
            before = {
                workspaces[0].id: _direction_drop_counter(
                    workspaces[0], workspaces[1], runner
                ),
                workspaces[1].id: _direction_drop_counter(
                    workspaces[1], workspaces[0], runner
                ),
            }
            after = {
                workspaces[0].id: _direction_drop_counter(
                    workspaces[0], workspaces[1], runner
                ),
                workspaces[1].id: _direction_drop_counter(
                    workspaces[1], workspaces[0], runner
                ),
            }
            self.assertTrue(
                all(after[item.id] > before[item.id] for item in workspaces)
            )

            class RefusedRunner(HealthRunner):
                def __call__(self, command, **kwargs):
                    command = list(command)
                    if command[0] == "ssh":
                        return subprocess.CompletedProcess(command, 0, "", "")
                    return super().__call__(command, **kwargs)

            with self.assertRaisesRegex(WorkspaceError, "no listening SSH target"):
                _verify_guest_listeners(workspaces, key, RefusedRunner())

            class ConnectionRefusedRunner(HealthRunner):
                def __call__(self, command, **kwargs):
                    command = list(command)
                    if command[0] == "ssh" and command[-1].startswith("python3 -c"):
                        return subprocess.CompletedProcess(
                            command, 2, "", "unexpected socket error: 111"
                        )
                    return super().__call__(command, **kwargs)

            with self.assertRaisesRegex(WorkspaceError, "socket error: 111"):
                _probe_peer_denial(
                    workspaces[0], workspaces[1], key, ConnectionRefusedRunner()
                )

    def test_health_timeout_and_evidence_failure_still_contain_and_deprovision(
        self,
    ) -> None:
        class TimeoutRunner(HealthRunner):
            def __call__(self, command, **kwargs):
                if list(command)[:2] == ["systemctl", "show"] and self.slice_calls:
                    raise subprocess.TimeoutExpired(list(command), 10)
                return super().__call__(command, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            workspaces = acceptance_workspaces(directory)
            key = pathlib.Path(directory) / "id_ed25519"
            key.write_text("private")
            runner = TimeoutRunner()
            status = {"consistent": True, "domain_state": "running"}
            with (
                patch("scripts.agent_workspaces.acceptance.os.geteuid", return_value=0),
                patch(
                    "scripts.agent_workspaces.acceptance.provision_status",
                    return_value=status,
                ),
                patch(
                    "scripts.agent_workspaces.acceptance.deprovision_workspace"
                ) as deprovision,
                patch("scripts.agent_workspaces.acceptance.time.sleep"),
                patch.object(pathlib.Path, "write_text", side_effect=OSError("full")),
                self.assertRaises(subprocess.TimeoutExpired),
            ):
                run_two_guest_acceptance(
                    workspaces,
                    ssh_key=key,
                    network_url="https://example.test/pressure",
                    duration_seconds=10,
                    evidence_path=pathlib.Path(directory) / "evidence.json",
                    runner=runner,
                )
            self.assertEqual(deprovision.call_count, 2)
            self.assertEqual(
                len([command for command in runner.commands if command[0] == "ip"]),
                2,
            )
            self.assertEqual(
                len(
                    [
                        command
                        for command in runner.commands
                        if command[:2] == ["virsh", "destroy"]
                    ]
                ),
                2,
            )

    def test_initial_health_timeout_still_contain_and_deprovision(self) -> None:
        class TimeoutRunner(HealthRunner):
            def __call__(self, command, **kwargs):
                if list(command)[:2] == ["systemctl", "show"]:
                    raise subprocess.TimeoutExpired(list(command), 10)
                return super().__call__(command, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            workspaces = acceptance_workspaces(directory)
            key = pathlib.Path(directory) / "id_ed25519"
            key.write_text("private")
            runner = TimeoutRunner()
            status = {"consistent": True, "domain_state": "running"}
            with (
                patch("scripts.agent_workspaces.acceptance.os.geteuid", return_value=0),
                patch(
                    "scripts.agent_workspaces.acceptance.provision_status",
                    return_value=status,
                ),
                patch(
                    "scripts.agent_workspaces.acceptance.deprovision_workspace"
                ) as deprovision,
                self.assertRaises(subprocess.TimeoutExpired),
            ):
                run_two_guest_acceptance(
                    workspaces,
                    ssh_key=key,
                    network_url="https://example.test/pressure",
                    duration_seconds=10,
                    evidence_path=pathlib.Path(directory) / "evidence.json",
                    runner=runner,
                )
            self.assertEqual(deprovision.call_count, 2)
            recorded = json.loads(
                (pathlib.Path(directory) / "evidence.json").read_text()
            )
            self.assertEqual(recorded["failure"]["type"], "TimeoutExpired")
            self.assertEqual(
                len([command for command in runner.commands if command[0] == "ip"]),
                2,
            )


class RealAccessAcceptanceTests(unittest.TestCase):
    def test_real_access_acceptance_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspaces = acceptance_workspaces(directory)
            key = pathlib.Path(directory) / "id_ed25519"
            key.write_text("private")
            pub = pathlib.Path(directory) / "id_ed25519.pub"
            pub.write_text(
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest enrolled@example.test"
            )
            evidence = pathlib.Path(directory) / "evidence.json"
            status = {"consistent": True, "domain_state": "running"}
            with (
                patch("scripts.agent_workspaces.acceptance.os.geteuid", return_value=0),
                patch(
                    "scripts.agent_workspaces.acceptance.provision_status",
                    return_value=status,
                ),
            ):
                result = run_real_access_acceptance(
                    workspaces,
                    primary_ssh_key=key,
                    evidence_path=evidence,
                    network_url="https://example.test/health",
                    cleanup=False,
                    runner=HealthRunner(),
                )
            self.assertEqual(result["result"], "accepted")
            self.assertEqual(result["auth"]["primary_to_own"], "accepted")
            self.assertEqual(result["auth"]["primary_to_peer"], "denied_publickey")
            self.assertEqual(result["auth"]["unauthorized_to_own"], "denied_publickey")
            self.assertEqual(result["auth"]["unauthorized_to_peer"], "denied_publickey")
            self.assertEqual(result["auth"]["password_auth_to_own"], "denied_password")
            self.assertEqual(result["auth"]["password_auth_to_peer"], "denied_password")
            self.assertEqual(result["isolation"]["peer_denial"], "timeout")
            self.assertEqual(result["egress"]["status"], "verified")
            self.assertTrue(evidence.is_file())
            recorded = json.loads(evidence.read_text())
            self.assertEqual(recorded["result"], "accepted")

    def test_real_access_acceptance_requires_two_enabled_workspaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspaces = acceptance_workspaces(directory)
            key = pathlib.Path(directory) / "id_ed25519"
            key.write_text("private")
            evidence = pathlib.Path(directory) / "evidence.json"
            workspaces[0].raw["enabled"] = False
            with (
                patch("scripts.agent_workspaces.acceptance.os.geteuid", return_value=0),
                self.assertRaisesRegex(
                    WorkspaceError, "exactly two enabled workspaces"
                ),
            ):
                run_real_access_acceptance(
                    workspaces,
                    primary_ssh_key=key,
                    evidence_path=evidence,
                    runner=HealthRunner(),
                )

    def test_real_access_acceptance_requires_root_and_safe_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspaces = acceptance_workspaces(directory)
            key = pathlib.Path(directory) / "id_ed25519"
            evidence = pathlib.Path(directory) / "evidence.json"
            with (
                patch(
                    "scripts.agent_workspaces.acceptance.os.geteuid", return_value=1000
                ),
                self.assertRaisesRegex(WorkspaceError, "must run as root"),
            ):
                run_real_access_acceptance(
                    workspaces,
                    primary_ssh_key=key,
                    evidence_path=evidence,
                    runner=HealthRunner(),
                )
            with (
                patch("scripts.agent_workspaces.acceptance.os.geteuid", return_value=0),
                self.assertRaisesRegex(WorkspaceError, "missing or unsafe"),
            ):
                run_real_access_acceptance(
                    workspaces,
                    primary_ssh_key=key,
                    evidence_path=evidence,
                    runner=HealthRunner(),
                )

    def test_real_access_acceptance_fails_if_peer_allows_primary_key(self) -> None:
        class InsecurePeerRunner(HealthRunner):
            def __call__(self, command, **kwargs):
                cmd = list(command)
                if cmd and cmd[0] == "ssh" and cmd[-1] == "echo auth_ok":
                    return subprocess.CompletedProcess(cmd, 0, "auth_ok\n", "")
                return super().__call__(command, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            workspaces = acceptance_workspaces(directory)
            key = pathlib.Path(directory) / "id_ed25519"
            key.write_text("private")
            evidence = pathlib.Path(directory) / "evidence.json"
            status = {"consistent": True, "domain_state": "running"}
            with (
                patch("scripts.agent_workspaces.acceptance.os.geteuid", return_value=0),
                patch(
                    "scripts.agent_workspaces.acceptance.provision_status",
                    return_value=status,
                ),
                self.assertRaisesRegex(
                    WorkspaceError, "unexpectedly authenticated on peer"
                ),
            ):
                run_real_access_acceptance(
                    workspaces,
                    primary_ssh_key=key,
                    evidence_path=evidence,
                    runner=InsecurePeerRunner(),
                )


if __name__ == "__main__":
    unittest.main()
