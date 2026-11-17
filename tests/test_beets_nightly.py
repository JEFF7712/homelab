"""Exercise the shipped nightly runner against real disposable SQLite libraries."""

import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from tests.gitops_render import config_map

ROOT = Path(__file__).resolve().parent.parent
CONFIG_MAP = config_map("media", "beets-config")
CONFIG = yaml.safe_load(CONFIG_MAP["data"]["config.yaml"])
MOD = {"__name__": "beets_under_test"}
exec(compile(CONFIG_MAP["data"]["nightly.py"], "nightly.py", "exec"), MOD)


def create_library(path, tracks=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            "CREATE TABLE items (id INTEGER); CREATE TABLE albums (id INTEGER);"
        )
        conn.executemany("INSERT INTO items VALUES (?)", [(i,) for i in range(tracks)])
        if tracks:
            conn.execute("INSERT INTO albums VALUES (1)")


class FakeProcess:
    """Popen stand-in: ``finish`` runs once the importer is allowed to complete."""

    def __init__(self, finish, polls_before_exit=0):
        self.finish = finish
        self.polls_before_exit = polls_before_exit
        self.signals = []
        self.returncode = None

    def wait(self, timeout=None):
        if self.returncode is None:
            if self.polls_before_exit and not self.signals:
                self.polls_before_exit -= 1
                raise subprocess.TimeoutExpired("beet", timeout)
            self.returncode = 130 if self.signals else self.finish()
        return self.returncode

    def send_signal(self, number):
        self.signals.append(number)

    def kill(self):
        self.returncode = -9


class TestNightlyRunner(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.database = self.root / "persistent" / "library.db"
        self.state = self.database.parent / "state.pickle"
        self.config = self.root / "config.yaml"
        self.config.write_text(
            yaml.safe_dump(
                dict(CONFIG, library=str(self.database), statefile=str(self.state))
            )
        )

    def run_import(self, callback, paths=None, free=None):
        """Run with a fake importer; ``free`` yields successive free-space reads."""
        reads = iter(free or [])
        process = FakeProcess(callback)

        def popen(command, **kwargs):
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            process.command = command
            return process

        with (
            patch.object(MOD["subprocess"], "Popen", side_effect=popen) as command,
            patch.dict(
                MOD["run"].__globals__,
                free_bytes=lambda _: next(reads, MOD["MIN_FREE_BYTES"]),
            ),
        ):
            result = MOD["run"](self.config, paths or [])
        summary = json.loads((self.database.parent / "last-run.json").read_text())
        return result, summary, command.call_args.args[0] if command.called else None

    def test_first_import_reports_persisted_growth(self):
        def import_album():
            self.assertTrue(self.state.parent.is_dir())
            create_library(self.database, tracks=8)
            self.state.write_bytes(b"saved progress")
            return 0

        code, summary, command = self.run_import(
            import_album, ["/data/music/Artist/Album"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(summary["before"], {"tracks": 0, "albums": 0})
        self.assertEqual(summary["added"], {"tracks": 8, "albums": 1})
        self.assertEqual(command[-1], "/data/music/Artist/Album")
        self.assertIn("--resume", command)

    def test_success_without_imports_fails_for_empty_library(self):
        def skip_all():
            create_library(self.database)
            return 0

        code, summary, command = self.run_import(skip_all)
        self.assertEqual(code, 1)
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(command[-1], CONFIG["directory"])

    def test_incremental_no_change_is_success_with_existing_tracks(self):
        create_library(self.database, tracks=8)
        code, summary, _ = self.run_import(lambda: 0)
        self.assertEqual(code, 0)
        self.assertEqual(summary["added"], {"tracks": 0, "albums": 0})

    def test_importer_failure_preserves_exit_status_and_partial_progress(self):
        def interrupted():
            create_library(self.database, tracks=3)
            return 7

        code, summary, _ = self.run_import(interrupted)
        self.assertEqual(code, 7)
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["after"]["tracks"], 3)

    def test_low_space_skips_import_without_failing(self):
        create_library(self.database, tracks=8)
        code, summary, command = self.run_import(lambda: 0, free=[10**9])
        self.assertEqual(code, 0)
        self.assertIsNone(command)
        self.assertEqual(summary["status"], "paused_low_space")
        self.assertIsNone(summary["import_exit_code"])

    def test_space_dropping_mid_run_interrupts_and_keeps_progress(self):
        floor = MOD["MIN_FREE_BYTES"]
        process = FakeProcess(lambda: 0, polls_before_exit=3)

        def popen(command, **kwargs):
            create_library(self.database, tracks=5)
            return process

        reads = iter([floor * 2, floor * 2, floor - 1, floor - 1])
        with (
            patch.object(MOD["subprocess"], "Popen", side_effect=popen),
            patch.dict(MOD["run"].__globals__, free_bytes=lambda _: next(reads)),
        ):
            code = MOD["run"](self.config, [])
        summary = json.loads((self.database.parent / "last-run.json").read_text())
        self.assertEqual(process.signals, [MOD["signal"].SIGINT])
        self.assertEqual(code, 0)
        self.assertEqual(summary["status"], "paused_low_space")
        self.assertEqual(summary["added"], {"tracks": 5, "albums": 1})
        self.assertEqual(summary["import_exit_code"], 130)

    def test_corrupt_library_stops_before_import(self):
        self.database.parent.mkdir()
        self.database.write_bytes(b"not a database")
        with patch.object(MOD["subprocess"], "Popen") as command:
            with self.assertRaises(sqlite3.DatabaseError):
                MOD["run"](self.config, [])
        command.assert_not_called()


class TestNightlyConfiguration(unittest.TestCase):
    def test_matching_and_durable_state(self):
        self.assertIn("musicbrainz", CONFIG["plugins"].split())
        self.assertEqual(
            Path(CONFIG["statefile"]).parent, Path(CONFIG["library"]).parent
        )
        self.assertTrue(CONFIG["import"]["incremental_skip_later"])
        self.assertEqual(CONFIG["import"]["quiet_fallback"], "skip")
        self.assertFalse(CONFIG["import"]["move"])
        self.assertFalse(CONFIG["import"]["copy"])
        self.assertTrue(CONFIG["import"]["write"])

    def test_no_whole_file_rewrites(self):
        # tank/media snapshots pin rewritten files; embedart and scrub rewrite
        # every audio file and nearly filled the pool on 2026-11-15.
        plugins = CONFIG["plugins"].split()
        self.assertNotIn("embedart", plugins)
        self.assertNotIn("scrub", plugins)

    def test_genres_fill_gaps_without_overwriting(self):
        # Music lighting reads genres through Navidrome; lastgenre only fills
        # albums that have none.
        self.assertIn("lastgenre", CONFIG["plugins"].split())
        self.assertFalse(CONFIG["lastgenre"]["force"])

    def test_serial_execution_and_runner_mount(self):
        cron = next(
            d
            for d in yaml.safe_load_all((ROOT / "gitops/media/music.yaml").read_text())
            if d["kind"] == "CronJob" and d["metadata"]["name"] == "beets-nightly"
        )
        self.assertEqual(cron["spec"]["concurrencyPolicy"], "Forbid")
        job = cron["spec"]["jobTemplate"]["spec"]
        self.assertEqual(job["backoffLimit"], 0)
        spec = job["template"]["spec"]
        self.assertEqual(spec["restartPolicy"], "Never")
        container = spec["containers"][0]
        self.assertEqual(container["command"], ["python3", "/config/nightly.py"])
        self.assertTrue(
            next(m for m in container["volumeMounts"] if m["name"] == "beets-config")[
                "readOnly"
            ]
        )
        self.assertFalse(
            next(m for m in container["volumeMounts"] if m["name"] == "media").get(
                "readOnly", False
            )
        )


if __name__ == "__main__":
    unittest.main()
