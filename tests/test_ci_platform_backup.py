import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from scripts.ci.platform_backup import (
    rotate_check_partition,
    run_backup,
    run_maintenance,
    snapshot_home_assistant_config,
    snapshot_sqlite_tree,
    stop_active_services,
    verify_restored_backup,
)


class BookshelfStateBackupTests(unittest.TestCase):
    def test_wal_commits_survive_without_stale_sidecars_or_pid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            connections = []
            try:
                for name in ("readarr.db", "cache.db", "logs.db"):
                    connection = sqlite3.connect(source / name)
                    connections.append(connection)
                    connection.execute("PRAGMA journal_mode=WAL")
                    connection.execute("CREATE TABLE evidence(value TEXT)")
                    connection.execute(
                        "INSERT INTO evidence VALUES ('committed-in-wal')"
                    )
                    connection.commit()
                (source / "config.xml").write_text("<Config/>")
                (source / "readarr.pid").write_text("123")
                target = root / "restore"
                snapshot_sqlite_tree(source, target)
                for name in ("readarr.db", "cache.db", "logs.db"):
                    self.assertFalse((target / (name + "-wal")).exists())
                    self.assertFalse((target / (name + "-shm")).exists())
                    with closing(sqlite3.connect(target / name)) as restored:
                        self.assertEqual(
                            restored.execute("PRAGMA integrity_check").fetchone(),
                            ("ok",),
                        )
                        self.assertEqual(
                            restored.execute("SELECT value FROM evidence").fetchone(),
                            ("committed-in-wal",),
                        )
                self.assertTrue((target / "config.xml").is_file())
                self.assertFalse((target / "readarr.pid").exists())
            finally:
                for connection in connections:
                    connection.close()

    def test_state_cannot_escape_through_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "readarr.db").symlink_to(root / "outside.db")
            with self.assertRaisesRegex(ValueError, "symlinks"):
                snapshot_sqlite_tree(source, root / "backup")

    def test_missing_authoritative_state_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "missing readarr.db"):
                snapshot_sqlite_tree(root, root / "backup")

    def test_nfs_backup_rejects_wrong_host_or_escaping_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "nas-01"):
            run_backup("homelab-04", bookshelf_config=Path("/tank/cluster/books"))
        for path in ("/tank/cluster/../persist", "/persist/forgejo", "/tank/cluster"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                run_backup("nas-01", bookshelf_config=Path(path))


class HomeAssistantConfigBackupTests(unittest.TestCase):
    def test_copies_state_and_drops_regenerable_bulk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            (source / ".storage").mkdir(parents=True)
            (source / ".storage/core.config_entries").write_text("{}")
            (source / ".storage/auth").write_text("{}")
            (source / "configuration.yaml").write_text("default_config:\n")
            (source / "secrets.yaml").write_text("x: y\n")
            for bulky in ("backups", "deps", "tts", ".cache"):
                (source / bulky).mkdir()
                (source / bulky / "blob").write_text("large")
            (source / "home-assistant.log").write_text("log")
            (source / "home-assistant.log.1").write_text("log")
            (source / "watchman_v2.db-journal").write_text("j")
            target = root / "stage"
            snapshot_home_assistant_config(source, target)
            self.assertTrue((target / ".storage/core.config_entries").is_file())
            self.assertTrue((target / ".storage/auth").is_file())
            self.assertTrue((target / "secrets.yaml").is_file())
            for gone in (
                "backups",
                "deps",
                "tts",
                ".cache",
                "home-assistant.log",
                "home-assistant.log.1",
                "watchman_v2.db-journal",
            ):
                self.assertFalse((target / gone).exists(), gone)

    def test_symlinks_are_kept_as_links_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            (source / ".storage").mkdir(parents=True)
            (source / ".storage/core.config_entries").write_text("{}")
            (source / "configuration.yaml").write_text("")
            (root / "outside").write_text("secret elsewhere")
            (source / "escape").symlink_to(root / "outside")
            target = root / "stage"
            snapshot_home_assistant_config(source, target)
            self.assertTrue((target / "escape").is_symlink())

    def test_missing_authoritative_state_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "core.config_entries"):
                snapshot_home_assistant_config(root, root / "stage")

    def test_rejects_wrong_host_or_escaping_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "nas-01"):
            run_backup(
                "homelab-04", home_assistant_config=Path("/tank/cluster/ha-config")
            )
        for path in ("/tank/cluster/../persist", "/persist/ha", "/tank/cluster"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                run_backup("nas-01", home_assistant_config=Path(path))


class PlatformBackupStagingTests(unittest.TestCase):
    @patch("scripts.ci.platform_backup.subprocess.run")
    def test_snapshot_uses_persistent_storage_and_cleans_up(self, run):
        with tempfile.TemporaryDirectory() as temporary:
            persist = Path(temporary)
            database = persist / "woodpecker/server-data/woodpecker.sqlite"
            database.parent.mkdir(parents=True)
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE evidence (value TEXT)")
                connection.execute("INSERT INTO evidence VALUES ('retained')")

            def verify_stage(command, *, cwd, check):
                self.assertIn("backup", command)
                self.assertTrue(check)
                self.assertEqual(cwd.parent, persist / "platform-backup")
                with sqlite3.connect(
                    cwd / "woodpecker/server-data/woodpecker.sqlite"
                ) as connection:
                    self.assertEqual(
                        connection.execute("SELECT value FROM evidence").fetchone(),
                        ("retained",),
                    )
                self.assertEqual(cwd.stat().st_mode & 0o777, 0o700)

            run.side_effect = verify_stage
            run_backup("homelab-04", persist_root=persist)
            run.assert_called_once()
            self.assertEqual(list((persist / "platform-backup").iterdir()), [])

    @patch("scripts.ci.platform_backup.subprocess.run")
    def test_failed_upload_cleans_persistent_stage(self, run):
        with tempfile.TemporaryDirectory() as temporary:
            persist = Path(temporary)
            database = persist / "woodpecker/server-data/woodpecker.sqlite"
            database.parent.mkdir(parents=True)
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE evidence (value TEXT)")
            run.side_effect = subprocess.CalledProcessError(1, ["restic", "backup"])
            with self.assertRaises(subprocess.CalledProcessError):
                run_backup("homelab-04", persist_root=persist)
            self.assertEqual(list((persist / "platform-backup").iterdir()), [])


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
