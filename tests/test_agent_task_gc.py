from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.agent.task_gc import classify, main
from tests.agent_helpers import commit, make_repository

NOW = datetime(2026, 10, 22, tzinfo=UTC)
DAY = timedelta(days=1)


def write_task(
    tasks: Path, task_id: str, status: str, age: timedelta, now: datetime = NOW
) -> Path:
    directory = tasks / task_id
    directory.mkdir(parents=True)
    (directory / "task.json").write_text(
        json.dumps({"status": status, "timestamp": (now - age).isoformat()})
    )
    return directory


class TaskGcClassifyTest(unittest.TestCase):
    def test_only_old_unlocked_complete_records_are_collectable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tasks = Path(directory)
            old = write_task(tasks, "old", "complete", 20 * DAY)
            fresh = write_task(tasks, "fresh", "complete", 2 * DAY)
            locked = write_task(tasks, "locked", "complete", 20 * DAY)
            (locked / ".lock").write_text("{}")
            active = write_task(tasks, "active", "active", 20 * DAY)
            stale = write_task(tasks, "stale", "blocked", 40 * DAY)
            broken = tasks / "broken"
            broken.mkdir()
            (broken / "task.json").write_text("{")
            verdicts = {
                path.name: classify(path, NOW, 14 * DAY, 30 * DAY)
                for path in (old, fresh, locked, active, stale, broken)
            }
        self.assertEqual(
            verdicts,
            {
                "old": "collectable",
                "fresh": "recently completed",
                "locked": "locked",
                "active": "active",
                "stale": "stale blocked (40 days since checkpoint)",
                "broken": "unreadable",
            },
        )


class TaskGcMainTest(unittest.TestCase):
    def test_apply_archives_old_complete_records_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repository(Path(directory))
            (root / "README").write_text("fixture\n")
            commit(root, "initial")
            tasks = root / ".agent-state" / "tasks"
            now = datetime.now(UTC)
            for task_id, status, age in (
                ("done", "complete", 30 * DAY),
                ("recent", "complete", DAY),
                ("working", "active", 60 * DAY),
            ):
                write_task(tasks, task_id, status, age, now)
            archive = root / ".agent-state" / "archive" / "tasks"
            (archive / "done").mkdir(parents=True)
            previous = Path.cwd()
            os.chdir(root)
            try:
                with contextlib.redirect_stdout(io.StringIO()) as dry:
                    main([])
                self.assertTrue((tasks / "done").is_dir())
                with contextlib.redirect_stdout(io.StringIO()):
                    main(["--apply"])
            finally:
                os.chdir(previous)
            self.assertIn("archive done", dry.getvalue())
            self.assertIn("stale active", dry.getvalue())
            self.assertEqual(
                sorted(path.name for path in tasks.iterdir()), ["recent", "working"]
            )
            archived = sorted(path.name for path in archive.iterdir())
            self.assertEqual(len(archived), 2)
            self.assertTrue(archived[1].startswith("done-"))
            self.assertTrue((archive / archived[1] / "task.json").is_file())


if __name__ == "__main__":
    unittest.main()
