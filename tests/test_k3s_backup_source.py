from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import k3s_backup_source as backup


class K3sBackupSourceTests(unittest.TestCase):
    def setup_tree(self, root: Path) -> tuple[Path, Path, Path, Path]:
        server, snapshots, source, key = (
            root / name for name in ("server", "snapshots", "source", "host-key")
        )
        (server / "cred").mkdir(parents=True)
        snapshots.mkdir()
        (server / "token").write_bytes(b"fake-token-for-unit-test")
        (server / "cred/encryption-config.json").write_text('{"test-key": true}')
        key.write_bytes(b"fake-host-key-for-unit-test")
        return server, snapshots, source, key

    def runner(self, snapshots: Path):
        def execute(command, **kwargs):
            if command[:3] == ["k3s", "etcd-snapshot", "save"]:
                (snapshots / "external-backup-test").write_bytes(b"fake-etcd-database")
            return subprocess.CompletedProcess(command, 0, b"")

        return execute

    def test_publish_freezes_inputs_and_preserves_original_snapshot_permissions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            server, snapshots, root, key = self.setup_tree(Path(directory))
            with patch.object(
                backup.subprocess, "run", side_effect=self.runner(snapshots)
            ):
                generation = backup.prepare(root, server, snapshots, key, os.getgid())
                result = backup.verify(generation)
            self.assertEqual(backup.selected_generation(root), generation)
            self.assertTrue(result["encryption_config"])
            self.assertEqual(
                (generation / "server-token").read_bytes(),
                (server / "token").read_bytes(),
            )
            self.assertEqual(
                (generation / "homelab-01-ssh_host_ed25519_key").read_bytes(),
                key.read_bytes(),
            )
            original = snapshots / "external-backup-test"
            self.assertEqual(original.stat().st_mode & 0o777, 0o644)
            self.assertNotEqual(
                original.stat().st_ino, (generation / "etcd-snapshot").stat().st_ino
            )
            self.assertEqual(
                (generation / "server-token").stat().st_mode & 0o777, 0o640
            )

    def test_rotation_during_snapshot_refuses_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            server, snapshots, root, key = self.setup_tree(Path(directory))
            execute = self.runner(snapshots)

            def rotating(command, **kwargs):
                result = execute(command, **kwargs)
                if command[:3] == ["k3s", "etcd-snapshot", "save"]:
                    (server / "token").write_bytes(b"rotated-token")
                return result

            with patch.object(backup.subprocess, "run", side_effect=rotating):
                with self.assertRaisesRegex(RuntimeError, "changed during snapshot"):
                    backup.prepare(root, server, snapshots, key, os.getgid())
            self.assertFalse((root / "LATEST").exists())

    def test_prior_good_generation_remains_selected_after_failed_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            server, snapshots, root, key = self.setup_tree(Path(directory))
            with patch.object(
                backup.subprocess, "run", side_effect=self.runner(snapshots)
            ):
                previous = backup.prepare(root, server, snapshots, key, os.getgid())
            (snapshots / "external-backup-test").unlink()
            with patch.object(
                backup.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0),
            ):
                with self.assertRaisesRegex(RuntimeError, "fresh"):
                    backup.prepare(root, server, snapshots, key, os.getgid())
            self.assertEqual(backup.selected_generation(root), previous)

    def test_verifier_rejects_changed_or_linked_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            server, snapshots, root, key = self.setup_tree(Path(directory))
            with patch.object(
                backup.subprocess, "run", side_effect=self.runner(snapshots)
            ):
                generation = backup.prepare(root, server, snapshots, key, os.getgid())
            token = generation / "server-token"
            token.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "checksum"):
                backup.verify(generation)
            token.unlink()
            token.symlink_to(server / "token")
            with self.assertRaisesRegex(ValueError, "file-type"):
                backup.verify(generation)

    def test_stale_generation_is_not_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            server, snapshots, root, key = self.setup_tree(Path(directory))
            with patch.object(
                backup.subprocess, "run", side_effect=self.runner(snapshots)
            ):
                generation = backup.prepare(root, server, snapshots, key, os.getgid())
            path = generation / "backup-manifest.json"
            manifest = json.loads(path.read_text())
            manifest["created"] = (
                dt.datetime.now(dt.UTC) - dt.timedelta(days=2)
            ).isoformat()
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "stale"):
                backup.verify(generation)

    def test_wrong_cycle_is_refused_even_when_generation_is_recent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            server, snapshots, root, key = self.setup_tree(Path(directory))
            with patch.object(
                backup.subprocess, "run", side_effect=self.runner(snapshots)
            ):
                generation = backup.prepare(root, server, snapshots, key, os.getgid())
                with self.assertRaisesRegex(ValueError, "manifest"):
                    backup.verify(generation, cycle="19990101")

    @unittest.skipUnless(
        shutil.which("restic"), "restic required for independent read-back"
    )
    def test_independent_repository_contains_every_critical_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            server, snapshots, root, key = self.setup_tree(base)
            with patch.object(
                backup.subprocess, "run", side_effect=self.runner(snapshots)
            ):
                generation = backup.prepare(root, server, snapshots, key, os.getgid())
            with patch.dict(
                os.environ,
                {
                    "RESTIC_REPOSITORY": str(base / "repo"),
                    "RESTIC_PASSWORD": "fixture-password",
                },
            ):
                subprocess.run(["restic", "init"], check=True, capture_output=True)
                subprocess.run(
                    ["restic", "backup", "--host", "fixture", str(generation)],
                    check=True,
                    capture_output=True,
                )
                self.assertTrue(
                    backup.verify_repository(generation, "fixture")[
                        "independent_copy_verified"
                    ]
                )
                # Restic may tolerate a skipped file while backing up unrelated
                # paths. A receipt cannot accept an incomplete recovery set.
                subprocess.run(
                    [
                        "restic",
                        "backup",
                        "--host",
                        "fixture",
                        "--exclude",
                        "server-token",
                        str(generation),
                    ],
                    check=True,
                    capture_output=True,
                )
                with self.assertRaisesRegex(ValueError, "server-token"):
                    backup.verify_repository(generation, "fixture")

    def test_pointer_cannot_escape_generation_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "LATEST").write_text("../../token")
            with self.assertRaisesRegex(ValueError, "pointer"):
                backup.selected_generation(root)

    def test_offline_restore_refuses_persistent_scratch_before_etcd_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(
                backup.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, "ext2/ext3\n"),
            ) as execute:
                with self.assertRaisesRegex(ValueError, "tmpfs"):
                    backup.restore_check(
                        root / "unused-snapshot", root / "private-runtime"
                    )
            self.assertEqual(execute.call_count, 1)

    def test_offline_restore_uses_private_temporary_member_and_cleans_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "snapshot"
            snapshot.write_bytes(b"fake-etcd-database")
            runtime = root / "private-runtime"
            with patch.object(
                backup.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, "tmpfs\n"),
            ) as execute:
                backup.restore_check(snapshot, runtime)
            restore = execute.call_args_list[-1].args[0]
            self.assertEqual(
                restore[:4], ["etcdutl", "snapshot", "restore", str(snapshot)]
            )
            self.assertEqual(list(runtime.iterdir()), [])
