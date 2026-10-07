import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.ci.platform_backup import (
    rotate_check_partition,
    run_maintenance,
    stop_active_services,
    verify_restored_backup,
)


class PlatformBackupServiceTests(unittest.TestCase):
    @patch("scripts.ci.platform_backup.subprocess.run")
    def test_outage_service_is_not_scheduled_for_restart(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 3, "inactive\n"),
            subprocess.CompletedProcess([], 0, "active\n"),
            subprocess.CompletedProcess([], 0),
        ]
        stopped = []
        stop_active_services(["forgejo", "garage"], stopped)
        self.assertEqual(stopped, ["garage"])
        self.assertEqual(
            run.call_args_list[-1].args[0], ["systemctl", "stop", "garage"]
        )

    @patch("scripts.ci.platform_backup.subprocess.run")
    def test_transition_fails_before_backup_or_restart(self, run):
        run.return_value = subprocess.CompletedProcess([], 3, "deactivating\n")
        stopped = []
        with self.assertRaisesRegex(RuntimeError, "deactivating"):
            stop_active_services(["forgejo"], stopped)
        self.assertEqual(stopped, [])
        self.assertEqual(run.call_count, 1)

    @patch("scripts.ci.platform_backup.subprocess.run")
    def test_stop_failure_retains_cleanup_obligation(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, "active\n"),
            subprocess.CalledProcessError(1, ["systemctl", "stop", "forgejo"]),
        ]
        stopped = []
        with self.assertRaises(subprocess.CalledProcessError):
            stop_active_services(["forgejo"], stopped)
        self.assertEqual(stopped, ["forgejo"])


class PlatformBackupMaintenanceTests(unittest.TestCase):
    def test_partition_rotation_cycles_from_1_to_20_and_wraps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "check-partition"
            # 1. Uninitialized state
            p1 = rotate_check_partition(state_file)
            self.assertEqual(p1, 1)
            self.assertEqual(state_file.read_text().strip(), "2")

            # 2. Next run
            p2 = rotate_check_partition(state_file)
            self.assertEqual(p2, 2)
            self.assertEqual(state_file.read_text().strip(), "3")

            # 3. Wrapping at 20
            state_file.write_text("20\n")
            p20 = rotate_check_partition(state_file)
            self.assertEqual(p20, 20)
            self.assertEqual(state_file.read_text().strip(), "1")

            # 4. Invalid content recovers gracefully
            state_file.write_text("corrupt\n")
            p_rec = rotate_check_partition(state_file)
            self.assertEqual(p_rec, 1)
            self.assertEqual(state_file.read_text().strip(), "2")

    @patch("scripts.ci.platform_backup.subprocess.run")
    def test_maintenance_enforces_nas_01_ownership(self, mock_run) -> None:
        with self.assertRaisesRegex(ValueError, "designated exclusively to nas-01"):
            run_maintenance("homelab-04")
        self.assertEqual(mock_run.call_count, 0)

    @patch("scripts.ci.platform_backup.subprocess.run")
    def test_maintenance_runs_prune_and_rotated_check(self, mock_run) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "check-partition"
            state_file.write_text("5\n")
            run_maintenance("nas-01", partition_state_file=state_file)

            self.assertEqual(mock_run.call_count, 2)
            forget_cmd = mock_run.call_args_list[0].args[0]
            check_cmd = mock_run.call_args_list[1].args[0]

            self.assertEqual(forget_cmd[0], "restic")
            self.assertIn("forget", forget_cmd)
            self.assertIn("--prune", forget_cmd)

            self.assertEqual(check_cmd[0], "restic")
            self.assertIn("check", check_cmd)
            self.assertIn("--read-data-subset=5/20", check_cmd)
            self.assertEqual(state_file.read_text().strip(), "6")


class PlatformBackupVerifyRestoreTests(unittest.TestCase):
    def test_verify_restored_backup_detects_clean_and_corrupt_databases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            valid_db = root / "valid.sqlite"
            with sqlite3.connect(valid_db) as conn:
                conn.execute("CREATE TABLE t (id INT);")
                conn.execute("INSERT INTO t VALUES (42);")

            # Clean verification should pass
            verify_restored_backup(root)

            # Corrupt database header should fail
            corrupt_db = root / "corrupt.db"
            corrupt_db.write_bytes(b"NOT A SQLITE DATABASE FILE HEADER")
            with self.assertRaises(ValueError):
                verify_restored_backup(root)
