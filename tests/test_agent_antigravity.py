from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.agent.antigravity_hook import adapt_hook, normalized_payload
from scripts.agent.session import client_environment
from tests.agent_helpers import bash_shebang


class AntigravityAdapterTest(unittest.TestCase):
    def test_registered_command_recovers_empty_workspace_from_scoped_launch(
        self,
    ) -> None:
        source = Path(__file__).resolve().parents[1]
        bundle = json.loads(
            (source / ".agents/plugins/homelab-workflow/hooks.json").read_text()
        )
        command = bundle["homelab-workflow"]["PreInvocation"][0]["command"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "hooks").mkdir()
            adapter = root / "hooks/antigravity"
            adapter.write_text(bash_shebang() + "\ncat\n")
            adapter.chmod(0o755)
            with client_environment(root, "selected", "antigravity") as environment:
                result = subprocess.run(
                    ["sh", "-c", command],
                    cwd="/tmp",
                    env={"PATH": environment["PATH"], "HOME": os.environ["HOME"]},
                    input='{"conversationId":"fixture","workspacePaths":[]}',
                    text=True,
                    capture_output=True,
                    check=False,
                )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["conversationId"], "fixture")

    def test_camel_case_failure_preserves_actual_exit_and_unknown_failure(self) -> None:
        payload = {
            "conversationId": "actual-conversation",
            "toolCall": {"name": "run_command", "args": {"CommandLine": "just check"}},
            "error": "exit status 7",
        }
        result = normalized_payload(payload, "PostToolUse")
        self.assertEqual(result["session_id"], "actual-conversation")
        self.assertEqual(result["tool_input"], {"command": "just check"})
        self.assertEqual(result["tool_response"], {"exit_code": 7})
        payload["error"] = "permission denied"
        result = normalized_payload(payload, "PostToolUse")
        self.assertEqual(result["hook_event_name"], "PostToolUseFailure")
        self.assertNotIn("tool_response", result)

    def test_context_injects_ephemeral_message_once_and_stop_is_advisory(self) -> None:
        with mock.patch("scripts.agent.antigravity_hook.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = json.dumps(
                {"hookSpecificOutput": {"additionalContext": "selected context"}}
            )
            self.assertEqual(
                adapt_hook(Path.cwd(), "context", {"invocationNum": 0}),
                {"injectSteps": [{"ephemeralMessage": "selected context"}]},
            )
            self.assertEqual(
                adapt_hook(Path.cwd(), "context", {"invocationNum": 1}), {}
            )
            self.assertEqual(run.call_count, 1)
            run.return_value.stdout = json.dumps(
                {"systemMessage": "checkpoint required"}
            )
            output = io.StringIO()
            with contextlib.redirect_stderr(output):
                self.assertEqual(
                    adapt_hook(Path.cwd(), "stop", {"fullyIdle": True}),
                    {"decision": "stop"},
                )
            self.assertIn("checkpoint required", output.getvalue())
            self.assertEqual(adapt_hook(Path.cwd(), "stop", {"fullyIdle": False}), {})
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args.kwargs["timeout"], 8)

    def test_process_missing_hook_and_bad_input_fail_open(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for payload, mode in [
            ("not json", "context"),
            ("[]", "context"),
            ("{}", "unknown"),
        ]:
            with self.subTest(payload=payload, mode=mode):
                result = subprocess.run(
                    ["bash", str(root / "hooks/antigravity"), mode],
                    input=payload,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {})
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(adapt_hook(Path(directory), "context", {}), {})
