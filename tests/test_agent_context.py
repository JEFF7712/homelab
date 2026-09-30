from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.agent_helpers import commit, make_repository
from tests.test_agent_tasks import creation_payload

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def run_agent(repository: Path, *arguments: str, task_id: str | None = None):
    environment = {**os.environ, "PYTHONPATH": str(REPOSITORY_ROOT)}
    if task_id is None:
        environment.pop("AGENT_TASK_ID", None)
    else:
        environment["AGENT_TASK_ID"] = task_id
    return subprocess.run(
        [sys.executable, "-m", "scripts.agent", *arguments],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


class AgentContextTest(unittest.TestCase):
    def make_repo(self, directory: str) -> Path:
        repository = make_repository(Path(directory))
        (repository / "tracked.txt").write_text("initial\n", encoding="utf-8")
        commit(repository, "initial")
        return repository

    def create_task(self, repository: Path, task_id: str) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "scripts.agent", "task-new", task_id, "--json"],
            cwd=repository,
            env={**os.environ, "PYTHONPATH": str(REPOSITORY_ROOT)},
            input=json.dumps(creation_payload()),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_task_lists_active_tasks_without_selecting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = self.make_repo(directory)
            self.create_task(repository, "one")
            self.create_task(repository, "two")
            result = run_agent(repository, "context", "--json")
        payload = json.loads(result.stdout)
        self.assertIsNone(payload["task"]["selected"])
        self.assertEqual(payload["task"]["available"], ["one", "two"])

    def test_explicit_and_session_task_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = self.make_repo(directory)
            self.create_task(repository, "one")
            explicit = run_agent(repository, "context", "--json", "--task", "one")
            session = run_agent(repository, "context", "--json", task_id="one")
        self.assertEqual(json.loads(explicit.stdout)["task"]["selected"], "one")
        self.assertEqual(json.loads(session.stdout)["task"]["selected"], "one")

    def test_text_output_is_bounded_and_reports_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = self.make_repo(directory)
            for index in range(800):
                (repository / f"long changed filename {index:04d}.txt").write_text("x")
            result = run_agent(repository, "context")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLessEqual(len(result.stdout.encode()), 6144)
        self.assertIn("truncated", result.stdout.lower())
        self.assertIn("untracked: 800", result.stdout)
        self.assertIn("Tasks", result.stdout)
        self.assertIn("just check-changed", result.stdout)
        self.assertIn("AGENT_MAP.md", result.stdout)

    def test_nonobject_task_record_does_not_break_startup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = self.make_repo(directory)
            path = repository / ".agent-state/tasks/corrupt"
            path.mkdir(parents=True)
            (path / "task.json").write_text("[]")
            result = run_agent(repository, "context", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["task"]["available"], [])

    def test_export_redacts_and_explains_uncommitted_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = self.make_repo(directory)
            payload = creation_payload()
            payload["next_action"] = "Use token=secret-value from ~/.ssh/id_ed25519"
            create = subprocess.run(
                [sys.executable, "-m", "scripts.agent", "task-new", "one"],
                cwd=repository,
                env={**os.environ, "PYTHONPATH": str(REPOSITORY_ROOT)},
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(create.returncode, 0, create.stderr)
            result = run_agent(repository, "task-export", "one")
            handoff = (repository / "docs/agent-tasks/one.md").read_text()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("secret-value", handoff)
        self.assertNotIn("id_ed25519", handoff)
        self.assertIn("[REDACTED]", handoff)
        self.assertIn("uncommitted", handoff.lower())
        self.assertIn("patch", handoff.lower())
        self.assertIn("Exported at:", handoff)
        self.assertIn("Current HEAD at export:", handoff)

    def test_export_marks_old_evidence_stale_without_rewriting_record(self) -> None:
        from scripts.agent.git_state import collect_git_state
        from scripts.agent.tasks import create_task, export_task, resume_task

        with tempfile.TemporaryDirectory() as directory:
            root = self.make_repo(directory)
            payload = creation_payload()
            payload["verification_records"] = [
                {
                    "command": "just check",
                    "exit_code": 0,
                    "time": "2026-09-30T00:00:00Z",
                    "source_fingerprint": collect_git_state(root).fingerprint,
                    "evidence_path": ".agent-state/evidence/check.log",
                    "stale": False,
                }
            ]
            create_task(root, "export-stale", payload)
            record = root / ".agent-state/tasks/export-stale/task.json"
            original = record.read_bytes()
            (root / "tracked.txt").write_text("changed")
            exported = export_task(root, "export-stale").read_text()
            self.assertIn("freshness `stale`", exported)
            self.assertIn("verified at `2026-09-30T00:00:00Z`", exported)
            self.assertIn("Current source fingerprint at export:", exported)
            self.assertEqual(record.read_bytes(), original)
            self.assertTrue(
                resume_task(root, "export-stale")["verifications"][0]["stale"]
            )


if __name__ == "__main__":
    unittest.main()
