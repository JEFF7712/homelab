from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.agent.git_state import collect_git_state
from scripts.agent.session import CLIENTS, client_environment, run_client
from scripts.agent.tasks import TaskError, create_task, resume_task
from tests.agent_helpers import commit, make_repository
from tests.test_agent_tasks import creation_payload


class AgentSessionTest(unittest.TestCase):
    def test_new_client_launchers_select_cli_not_antigravity_desktop(self) -> None:
        self.assertEqual(CLIENTS["antigravity"], "agy")
        self.assertEqual(CLIENTS["muse"], "muse")

    def test_native_session_bridge_survives_filtered_environment_and_cleans_up(
        self,
    ) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = make_repository(Path(directory))
            (root / "justfile").write_text((source / "justfile").read_text())
            (root / "hooks").mkdir()
            for name in ["common.sh", "session-start"]:
                shutil.copy2(source / "hooks" / name, root / "hooks" / name)
            commit(root, "initial")
            create_task(root, "selected", creation_payload())
            with client_environment(
                root, "selected", "muse", trace=True
            ) as environment:
                bridge = (
                    Path(environment["PATH"].split(os.pathsep)[0])
                    / "homelab-agent-session"
                )
                self.assertTrue(bridge.is_file())
                filtered = {"PATH": environment["PATH"], "PYTHONPATH": str(source)}
                result = subprocess.run(
                    ["bash", str(root / "hooks/session-start")],
                    cwd=root,
                    env=filtered,
                    input=json.dumps(
                        {"hook_event_name": "SessionStart", "session_id": "fixture"}
                    ),
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                response = json.loads(result.stdout)
                self.assertIn(
                    "selected: selected",
                    response["hookSpecificOutput"]["additionalContext"],
                )
                receipt = json.loads(
                    (root / ".agent-state/evidence/hook-integration/fixture.jsonl")
                    .read_text()
                    .splitlines()[0]
                )
                self.assertEqual(receipt["selected_task"], "selected")
                self.assertEqual(receipt["client"], "muse")
                with client_environment(root, "other", "muse") as second:
                    self.assertNotEqual(second["PATH"], environment["PATH"])
                self.assertTrue(bridge.is_file())
            self.assertFalse(bridge.exists())

    def test_just_launcher_preserves_prompt_arguments_literally(self) -> None:
        root_source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = make_repository(Path(directory))
            (root / "justfile").write_text((root_source / "justfile").read_text())
            (root / "bin").mkdir()
            client = root / "bin/codex"
            client.write_text(
                f"#!{sys.executable}\nimport json, os, sys\nprint(json.dumps({{'argv':sys.argv[1:], 'task':os.environ['AGENT_TASK_ID']}}))\n"
            )
            client.chmod(0o755)
            commit(root, "initial")
            create_task(root, "literal", creation_payload())
            result = subprocess.run(
                [
                    "just",
                    "agent-run",
                    "literal",
                    "codex",
                    "--",
                    "a prompt with spaces; $HOME",
                    "$(false)",
                ],
                cwd=root,
                env={
                    **os.environ,
                    "PYTHONPATH": str(root_source),
                    "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            import json

            self.assertEqual(
                json.loads(result.stdout),
                {
                    "argv": ["a prompt with spaces; $HOME", "$(false)"],
                    "task": "literal",
                },
            )

    def test_task_selection_is_scoped_to_child_and_client_exit_is_preserved(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repository(Path(directory))
            (root / "source").write_text("initial")
            commit(root, "initial")
            create_task(root, "selected", creation_payload())
            previous = os.environ.get("AGENT_TASK_ID")
            inspection = resume_task(root, "selected")
            state = collect_git_state(root)
            with (
                mock.patch("scripts.agent.session.subprocess.run") as run,
                mock.patch(
                    "scripts.agent.session.resume_task", return_value=inspection
                ),
                mock.patch(
                    "scripts.agent.session.collect_git_state", return_value=state
                ),
            ):
                run.return_value.returncode = 7
                code = run_client(root, "selected", "cursor", ["-p", "literal $value"])
            self.assertEqual(code, 7)
            self.assertEqual(run.call_args.args[0], ["agent", "-p", "literal $value"])
            self.assertEqual(run.call_args.kwargs["env"]["AGENT_TASK_ID"], "selected")
            self.assertEqual(run.call_args.kwargs["env"]["AGENT_HOOK_CLIENT"], "cursor")
            self.assertEqual(os.environ.get("AGENT_TASK_ID"), previous)

    def test_invalid_task_does_not_launch_client(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repository(Path(directory))
            (root / "source").write_text("initial")
            commit(root, "initial")
            with mock.patch("scripts.agent.session.subprocess.run") as run:
                with self.assertRaises(TaskError):
                    run_client(root, "../invalid", "codex", [])
                run.assert_not_called()
