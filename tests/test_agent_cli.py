from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.agent_helpers import commit, make_repository

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COMMANDS = (
    "agent-run",
    "verify",
    "context",
    "doctor",
    "check-changed",
    "task-new",
    "task-resume",
    "task-checkpoint",
    "task-export",
    "status",
)
JSON_COMMANDS = ("context", "doctor", "check-changed", "task-resume", "status")


def run_agent(
    *arguments: str, cwd: Path = REPOSITORY_ROOT
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "scripts.agent", *arguments],
        cwd=cwd,
        env={**os.environ, "PYTHONPATH": str(REPOSITORY_ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )


class AgentCliTest(unittest.TestCase):
    def test_context_failure_has_a_json_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_agent("context", "--json", cwd=Path(directory))
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["command"], "context")
        self.assertEqual(payload["status"], "error")
        self.assertEqual(result.stderr, "")

    def test_verify_records_real_exit_status_and_can_checkpoint_evidence(self) -> None:
        from scripts.agent.tasks import create_task
        from tests.test_agent_tasks import creation_payload

        with tempfile.TemporaryDirectory() as directory:
            root = make_repository(Path(directory))
            (root / "source").write_text("initial")
            commit(root, "initial")
            create_task(root, "verify-demo", creation_payload())
            result = run_agent(
                "verify",
                "--json",
                "--task",
                "verify-demo",
                "--record",
                "--",
                sys.executable,
                "-c",
                "raise SystemExit(7)",
                cwd=root,
            )
            self.assertEqual(result.returncode, 7, result.stderr)
            payload = json.loads(result.stdout)
            verification = payload["results"][0]["verification"]
            record = json.loads(
                (root / ".agent-state/tasks/verify-demo/task.json").read_text()
            )
            self.assertEqual(record["verification_records"], [verification])
            self.assertEqual(verification["exit_code"], 7)
            self.assertFalse(verification["stale"])
            self.assertTrue(verification["time"])

    def test_help_lists_public_commands(self) -> None:
        result = run_agent("--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        for command in COMMANDS:
            self.assertIn(command, result.stdout)

    def test_structured_commands_accept_json_option(self) -> None:
        for command in JSON_COMMANDS:
            with self.subTest(command=command):
                result = run_agent(command, "--help")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--json", result.stdout)

    def test_context_json_reports_git_checkout_and_untracked_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent cli repo ") as directory:
            repository = make_repository(Path(directory))
            (repository / "tracked.txt").write_text("tracked\n", encoding="utf-8")
            commit(repository, "initial")
            (repository / "tracked.txt").write_text("modified\n", encoding="utf-8")
            (repository / "new file.txt").write_text("untracked\n", encoding="utf-8")

            result = run_agent("context", "--json", cwd=repository)

            subprocess.run(
                ["git", "checkout", "--detach", "-q"], cwd=repository, check=True
            )
            detached = run_agent("context", "--json", cwd=repository)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["command"], "context")
        self.assertEqual(payload["repository"]["root"], str(repository.resolve()))
        self.assertEqual(payload["repository"]["branch"], "main")
        self.assertFalse(payload["repository"]["detached"])
        self.assertRegex(payload["repository"]["head"], r"^[0-9a-f]{40}$")
        self.assertTrue(payload["repository"]["dirty"])
        self.assertEqual(payload["repository"]["dirty_summary"]["unstaged"], 1)
        self.assertEqual(payload["repository"]["dirty_summary"]["untracked"], 1)
        self.assertIn("new file.txt", payload["repository"]["dirty_summary"]["files"])
        self.assertEqual(detached.returncode, 0, detached.stderr)
        detached_payload = json.loads(detached.stdout)
        self.assertIsNone(detached_payload["repository"]["branch"])
        self.assertTrue(detached_payload["repository"]["detached"])

    def test_task_stdin_refuses_tty_instead_of_hanging(self) -> None:
        from scripts.agent.__main__ import _read_json_stdin
        from scripts.agent.tasks import TaskError

        with (
            mock.patch.object(sys.stdin, "isatty", return_value=True),
            self.assertRaisesRegex(TaskError, "no JSON on stdin"),
        ):
            _read_json_stdin()

    def test_task_new_template_prints_valid_creation_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_agent(
                "task-new", "template-demo", "--template", cwd=Path(directory)
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        template = json.loads(result.stdout)
        self.assertEqual(template["status"], "active")
        self.assertIn("acceptance_criteria", template)
        self.assertIn("owned_files", template)

    def test_unknown_command_exits_two_without_traceback(self) -> None:
        result = run_agent("does-not-exist")

        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)

    def test_validation_and_formatting_have_no_cli_stubs(self) -> None:
        for command in ("check", "fmt", "fmt-check"):
            with self.subTest(command=command):
                result = run_agent(command)

                self.assertEqual(result.returncode, 2)
                self.assertNotIn("Traceback", result.stderr)
                self.assertNotIn("not implemented", result.stderr)

    def test_doctor_returns_its_structured_contract(self) -> None:
        result = run_agent("doctor", "--json")

        self.assertIn(result.returncode, {0, 1})
        payload = json.loads(result.stdout)
        self.assertEqual(payload["command"], "doctor")
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
