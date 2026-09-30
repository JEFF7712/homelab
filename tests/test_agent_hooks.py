import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.agent.hook_runtime import (
    failure_status,
    record_failure,
    tasks_needing_attention,
)
from tests.agent_helpers import commit, make_repository

ROOT = Path(__file__).resolve().parents[1]


class AgentHookTest(unittest.TestCase):
    def test_real_client_failure_shapes_and_unknown_codes(self) -> None:
        cases = [
            (
                {
                    "hook_event_name": "PostToolUseFailure",
                    "error": "Exit code 7\nfailed",
                },
                (True, 7),
            ),
            (
                {
                    "hook_event_name": "postToolUseFailure",
                    "error_message": "Command timed out",
                    "failure_type": "timeout",
                },
                (True, None),
            ),
            ({"hook_event_name": "PostToolUse", "tool_response": ""}, (False, None)),
            (
                {
                    "tool_response": json.dumps(
                        {"command": "verify", "results": [{"exit_code": 7}]}
                    )
                },
                (True, 7),
            ),
            (
                {
                    "tool_response": json.dumps(
                        {
                            "command": "check-changed",
                            "results": [{"exit_code": 0}, {"exit_code": 3}],
                        }
                    )
                },
                (True, 3),
            ),
            ({"tool_response": {"exit_code": 0}}, (False, 0)),
        ]
        for payload, expected in cases:
            with self.subTest(payload=payload):
                self.assertEqual(failure_status(payload), expected)

    def test_failure_event_without_exit_code_does_not_invent_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record_failure(
                root,
                {
                    "hook_event_name": "postToolUseFailure",
                    "tool_input": {"command": "just check"},
                    "error_message": "timeout TOKEN=synthetic-secret",
                    "failure_type": "timeout",
                },
            )
            record = json.loads(
                (root / ".agent-state/evidence/hooks/unknown.jsonl").read_text()
            )
        self.assertIsNone(record["exit_code"])
        self.assertEqual(record["failure_type"], "timeout")
        self.assertNotIn("synthetic-secret", record["error"])

    def test_client_hook_configuration_is_valid_json(self) -> None:
        for path in (
            ".claude/settings.json",
            ".codex/hooks.json",
            ".cursor/hooks.json",
            ".mcp.json",
            "opencode.json",
        ):
            self.assertIsInstance(json.loads((ROOT / path).read_text()), dict)

    def test_session_start_returns_client_valid_json_and_recursion_fails_open(
        self,
    ) -> None:
        result = subprocess.run(
            ["bash", str(ROOT / "hooks/session-start")],
            cwd=ROOT,
            input='{"session_id":"fixture","hook_event_name":"SessionStart"}',
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            set(json.loads(result.stdout)),
            {"hookSpecificOutput"},
        )
        self.assertEqual(
            json.loads(result.stdout)["hookSpecificOutput"]["hookEventName"],
            "SessionStart",
        )
        cursor = subprocess.run(
            ["bash", str(ROOT / "hooks/session-start")],
            cwd=ROOT,
            input='{"conversation_id":"fixture","hook_event_name":"sessionStart"}',
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(cursor.returncode, 0, cursor.stderr)
        self.assertEqual(set(json.loads(cursor.stdout)), {"additional_context"})
        recursive = subprocess.run(
            ["bash", str(ROOT / "hooks/session-start")],
            cwd=ROOT,
            env={**os.environ, "AGENT_HOOK_ACTIVE": "1"},
            input="{}",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(json.loads(recursive.stdout), {})
        malformed = subprocess.run(
            ["bash", str(ROOT / "hooks/session-start")],
            cwd=ROOT,
            input="not-json",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(malformed.returncode, 0, malformed.stderr)
        self.assertEqual(json.loads(malformed.stdout), {})

    def test_validation_result_records_matching_failures_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-hooks-") as directory:
            hooks = Path(directory) / "hooks"
            shutil.copytree(ROOT / "hooks", hooks)
            script = str(hooks / "validation-result")
            identity = f"hook-fixture-{os.getpid()}"
            evidence = (
                Path(directory) / ".agent-state/evidence/hooks" / f"{identity}.jsonl"
            )
            self.assertFalse(evidence.exists())
            matching = subprocess.run(
                ["bash", script],
                cwd=directory,
                env={**os.environ, "PYTHONPATH": str(ROOT)},
                input=json.dumps(
                    {
                        "session_id": identity,
                        "tool_input": {
                            "command": "python -m unittest discover -s tests"
                        },
                        "tool_response": {"exit_code": 1},
                    }
                ),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(matching.returncode, 0, matching.stderr)
            self.assertEqual(json.loads(matching.stdout), {})
            self.assertTrue(evidence.is_file())
            record = json.loads(evidence.read_text())
            self.assertEqual(record["exit_code"], 1)
            self.assertIn("python", record["check"])

            other_identity = f"{identity}-unmatched"
            other_evidence = (
                Path(directory)
                / ".agent-state/evidence/hooks"
                / f"{other_identity}.jsonl"
            )
            unmatched = subprocess.run(
                ["bash", script],
                cwd=directory,
                env={**os.environ, "PYTHONPATH": str(ROOT)},
                input=json.dumps(
                    {
                        "session_id": other_identity,
                        "tool_input": {"command": "echo hello"},
                        "tool_response": {"exit_code": 1},
                    }
                ),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(unmatched.returncode, 0, unmatched.stderr)
            self.assertFalse(other_evidence.exists())

    def test_validation_result_accepts_codex_post_tool_use_payload(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-hooks-") as directory:
            hooks = Path(directory) / "hooks"
            shutil.copytree(ROOT / "hooks", hooks)
            identity = f"codex-fixture-{os.getpid()}"
            evidence = (
                Path(directory) / ".agent-state/evidence/hooks" / f"{identity}.jsonl"
            )
            result = subprocess.run(
                ["bash", str(hooks / "validation-result")],
                cwd=directory,
                env={**os.environ, "PYTHONPATH": str(ROOT)},
                input=json.dumps(
                    {
                        "session_id": identity,
                        "hook_event_name": "PostToolUse",
                        "tool_name": "Bash",
                        "tool_input": {"command": "just check"},
                        "tool_response": {"exit_code": 1},
                    }
                ),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            record = json.loads(evidence.read_text())
            self.assertEqual(record["exit_code"], 1)
            self.assertIn("just check", record["check"])

    def test_failure_evidence_redacts_before_truncation_and_is_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            hooks = Path(directory) / "hooks"
            shutil.copytree(ROOT / "hooks", hooks)
            for command in (
                'python --token "synthetic secret" PASSWORD=other-secret',
                "just check " + "x" * 155 + ' TOKEN="' + "private" * 50 + '"',
                "/usr/bin/python3 -m unittest",
            ):
                result = subprocess.run(
                    ["bash", str(hooks / "validation-result")],
                    env={**os.environ, "PYTHONPATH": str(ROOT)},
                    input=json.dumps(
                        {"session_id": "fixture", "command": command, "exit_code": 1}
                    ),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            raw = (
                Path(directory) / ".agent-state/evidence/hooks/fixture.jsonl"
            ).read_text()
            records = [json.loads(line) for line in raw.splitlines()]
        self.assertEqual(len(records), 3)
        for secret in ("synthetic secret", "other-secret", "private"):
            self.assertNotIn(secret, raw)
        self.assertTrue(
            all(item["timestamp"] and len(item["check"]) <= 200 for item in records)
        )

    def test_malformed_failure_payloads_fail_open(self) -> None:
        for payload in (
            "not-json",
            "[]",
            '{"command":"just check","exit_code":"invalid"}',
            '{"tool_input":[],"tool_response":[]}',
        ):
            result = subprocess.run(
                ["bash", str(ROOT / "hooks/validation-result")],
                input=payload,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {})


class AgentStopHookTest(unittest.TestCase):
    def make_repo(self, directory: str) -> Path:
        repository = make_repository(Path(directory))
        (repository / "tracked.txt").write_text("initial\n", encoding="utf-8")
        commit(repository, "initial")
        shutil.copytree(ROOT / "hooks", repository / "hooks")
        commit(repository, "hooks")
        return repository

    def create_task(self, repository: Path, task_id: str) -> None:
        from tests.test_agent_tasks import creation_payload

        result = subprocess.run(
            [sys.executable, "-m", "scripts.agent", "task-new", task_id],
            cwd=repository,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            input=json.dumps(creation_payload()),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def run_stop(
        self, repository: Path, task_id: str | None
    ) -> subprocess.CompletedProcess[str]:
        environment = {**os.environ, "PYTHONPATH": str(ROOT)}
        environment.pop("AGENT_TASK_ID", None)
        environment.pop("AGENT_HOOK_ACTIVE", None)
        if task_id is not None:
            environment["AGENT_TASK_ID"] = task_id
        hooks = repository / "hooks"
        if not hooks.is_dir():
            shutil.copytree(ROOT / "hooks", hooks)
        return subprocess.run(
            ["bash", str(hooks / "stop")],
            cwd=repository,
            env=environment,
            input="{}",
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

    def test_scoped_task_with_drift_reminds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = self.make_repo(directory)
            self.create_task(repository, "stop-demo")
            (repository / "untracked.txt").write_text("drift\n", encoding="utf-8")
            result = self.run_stop(repository, "stop-demo")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("stop-demo", payload["followup_message"])

    def test_unscoped_scan_finds_drifted_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = self.make_repo(directory)
            self.create_task(repository, "stop-scan")
            (repository / "untracked.txt").write_text("drift\n", encoding="utf-8")
            result = self.run_stop(repository, None)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("stop-scan", payload["followup_message"])

    def test_unscoped_scan_stays_within_hook_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = self.make_repo(directory)
            for index in range(6):
                self.create_task(repository, f"stop-budget-{index}")
            (repository / "untracked.txt").write_text("drift\n", encoding="utf-8")
            result = self.run_stop(repository, None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("stop-budget-", result.stdout)

    def test_codex_stop_reminds_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = self.make_repo(directory)
            self.create_task(repository, "stop-codex")
            (repository / "untracked.txt").write_text("drift\n", encoding="utf-8")
            environment = {**os.environ, "PYTHONPATH": str(ROOT)}
            environment.pop("AGENT_HOOK_ACTIVE", None)
            environment["AGENT_TASK_ID"] = "stop-codex"
            result = subprocess.run(
                ["bash", str(repository / "hooks" / "stop")],
                cwd=repository,
                env=environment,
                input=json.dumps(
                    {
                        "session_id": "fixture",
                        "hook_event_name": "Stop",
                        "stop_hook_active": False,
                    }
                ),
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertNotIn("decision", payload)
        self.assertIn("stop-codex", payload["systemMessage"])

    def test_clean_checkout_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = self.make_repo(directory)
            self.create_task(repository, "stop-clean")
            scoped = self.run_stop(repository, "stop-clean")
            unscoped = self.run_stop(repository, None)
        self.assertEqual(scoped.returncode, 0, scoped.stderr)
        self.assertEqual(json.loads(scoped.stdout), {})
        self.assertEqual(unscoped.returncode, 0, unscoped.stderr)
        self.assertEqual(json.loads(unscoped.stdout), {})

    def test_batch_scan_collects_git_state_once_and_skips_corrupt_records(self) -> None:
        from scripts.agent.git_state import collect_git_state

        with tempfile.TemporaryDirectory() as directory:
            repository = self.make_repo(directory)
            for index in range(12):
                self.create_task(repository, f"batch-{index:02d}")
            corrupt = repository / ".agent-state/tasks/corrupt"
            corrupt.mkdir()
            (corrupt / "task.json").write_text("not-json")
            (repository / "tracked.txt").write_text("changed\n")
            with mock.patch(
                "scripts.agent.hook_runtime.collect_git_state", wraps=collect_git_state
            ) as collect:
                attention = tasks_needing_attention(repository)
            self.assertEqual(collect.call_count, 1)
            self.assertEqual(attention, [f"batch-{index:02d}" for index in range(5)])

    def test_unresolved_failure_reminds_even_when_checkpoint_is_current(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = self.make_repo(directory)
            self.create_task(repository, "unresolved")
            path = repository / ".agent-state/tasks/unresolved/task.json"
            record = json.loads(path.read_text())
            record["unresolved_failures"] = ["schema validation failed"]
            path.write_text(json.dumps(record))
            result = self.run_stop(repository, "unresolved")
        self.assertIn("unresolved", json.loads(result.stdout)["followup_message"])


class AgentOpencodePluginTest(unittest.TestCase):
    PLUGIN = ROOT / ".opencode/plugins/agent-harness.js"

    def require_node(self) -> str:
        node = shutil.which("node")
        if node is None:
            self.fail("node is required for plugin tests; run nix develop ./flake")
        return node

    def test_lifecycle_injects_context_and_reminds_once_per_turn(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-opencode-") as directory:
            root = Path(directory)
            hooks = root / "hooks"
            hooks.mkdir()
            for name, response in (
                (
                    "session-start",
                    '{"hookSpecificOutput":{"additionalContext":"scoped context"}}',
                ),
                ("stop", '{"systemMessage":"checkpoint selected task"}'),
            ):
                (hooks / name).write_text(
                    '#!/bin/sh\ncat >> "$(dirname "$0")/received.jsonl"\n'
                    + f"printf '%s\\n' '{response}'\n"
                )
            harness = f"""
                import assert from 'node:assert/strict';
                const plugin = await import({json.dumps(f"file://{self.PLUGIN}")});
                const toasts = [];
                const hooks = await plugin.AgentHarness({{
                    worktree: {json.dumps(directory)},
                    client: {{tui: {{showToast: async (options) => toasts.push(options.body)}}}},
                }});
                const transform = hooks['experimental.chat.system.transform'];
                const output = {{system: ['original']}};
                await transform({{sessionID: 'selected'}}, output);
                await transform({{sessionID: 'selected'}}, output);
                assert.deepEqual(output.system, ['original', 'scoped context']);
                await hooks.event({{event: {{type: 'session.idle', properties: {{sessionID: 'other'}}}}}});
                const idle = {{event: {{type: 'session.idle', properties: {{sessionID: 'selected'}}}}}};
                await hooks.event(idle);
                await hooks.event(idle);
                assert.equal(toasts.length, 1);
                assert.equal(toasts[0].message, 'checkpoint selected task');
                await transform({{sessionID: 'selected'}}, {{system: []}});
                await hooks.event({{event: {{type: 'session.compacted', properties: {{sessionID: 'selected'}}}}}});
                await transform({{sessionID: 'selected'}}, {{system: []}});
                await hooks.event(idle);
                assert.equal(toasts.length, 2);
                await transform({{sessionID: 'deleted'}}, {{system: []}});
                await hooks.event({{event: {{type: 'session.deleted', properties: {{info: {{id: 'deleted'}}}}}}}});
                await hooks.event({{event: {{type: 'session.idle', properties: {{sessionID: 'deleted'}}}}}});
                assert.equal(toasts.length, 2);
                await hooks.dispose();
            """
            result = subprocess.run(
                [self.require_node(), "--input-type=module", "-e", harness],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            events = [
                json.loads(line)
                for line in (hooks / "received.jsonl").read_text().splitlines()
            ]
            self.assertEqual(
                [event["hook_event_name"] for event in events],
                [
                    "SessionStart",
                    "Stop",
                    "SessionStart",
                    "SessionStart",
                    "Stop",
                    "SessionStart",
                ],
            )
            self.assertEqual(result.stdout, "")
            self.assertEqual(
                result.stderr.count("[agent-harness] checkpoint selected task"), 2
            )

    def test_lifecycle_fails_open_when_shared_hooks_are_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-opencode-") as directory:
            harness = f"""
                import assert from 'node:assert/strict';
                const plugin = await import({json.dumps(f"file://{self.PLUGIN}")});
                const hooks = await plugin.AgentHarness({{worktree: {json.dumps(directory)}}});
                const output = {{system: ['original']}};
                await hooks['experimental.chat.system.transform']({{sessionID: 'selected'}}, output);
                assert.deepEqual(output.system, ['original']);
                await hooks.event({{event: {{type: 'session.idle', properties: {{sessionID: 'selected'}}}}}});
                await hooks['experimental.chat.system.transform']({{}}, output);
                assert.deepEqual(output.system, ['original']);
            """
            result = subprocess.run(
                [
                    self.require_node(),
                    "--disable-warning=MODULE_TYPELESS_PACKAGE_JSON",
                    "--input-type=module",
                    "-e",
                    harness,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")

    def test_plugin_exports_exactly_one_entrypoint(self) -> None:
        # OpenCode loads every exported function as a plugin entrypoint, so
        # helper exports break startup. This pins the single-export contract.
        node = self.require_node()
        harness = (
            "const plugin = await import("
            + json.dumps(f"file://{self.PLUGIN}")
            + ");"
            + "const names = Object.keys(plugin).filter("
            + " (key) => typeof plugin[key] === 'function');"
            + "if (names.length !== 1 || typeof plugin.AgentHarness !== 'function') {"
            + " console.error('unexpected plugin exports: ' + names.join(','));"
            + " process.exit(1); }"
        )
        result = subprocess.run(
            [node, "--input-type=module", "-e", harness],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_plugin_records_only_failed_validation_commands(self) -> None:
        node = self.require_node()
        with tempfile.TemporaryDirectory(prefix="agent-opencode-") as directory:
            hooks = Path(directory) / "hooks"
            hooks.mkdir()
            (hooks / "validation-result").write_text(
                '#!/usr/bin/env bash\ndir=$(dirname "$0")\ncat >> "$dir/received.jsonl"\n',
                encoding="utf-8",
            )
            (hooks / "validation-result").chmod(0o755)
            harness = (
                "const plugin = await import("
                + json.dumps(f"file://{self.PLUGIN}")
                + ");"
                + "const hooks = await plugin.AgentHarness({ worktree: "
                + json.dumps(directory)
                + ", directory: "
                + json.dumps(directory)
                + " });"
                + 'const handler = hooks["tool.execute.after"];'
                + "const calls = ["
                + '{"tool": "bash", "sessionID": "sess-1", "callID": "c1",'
                + ' "args": {"command": "just check"},'
                + ' "output": {"title": "just check", "output": "boom",'
                + ' "metadata": {"exit": 1}}},'
                + '{"tool": "bash", "sessionID": "a/b c!", "callID": "c2",'
                + ' "args": {"command": "python -m unittest"},'
                + ' "output": {"title": "t", "output": "boom",'
                + ' "metadata": {"exit": 2}}},'
                + '{"tool": "bash", "sessionID": "sess-1", "callID": "c3",'
                + ' "args": {"command": "echo hi"},'
                + ' "output": {"title": "t", "output": "",'
                + ' "metadata": {"exit": 1}}},'
                + '{"tool": "bash", "sessionID": "sess-1", "callID": "c4",'
                + ' "args": {"command": "just check"},'
                + ' "output": {"title": "t", "output": "",'
                + ' "metadata": {"exit": 0}}},'
                + '{"tool": "read", "sessionID": "sess-1", "callID": "c5",'
                + ' "args": {"filePath": "x"},'
                + ' "output": {"title": "t", "output": "", "metadata": {}}},'
                + '{"tool": "bash", "sessionID": "sess-1", "callID": "c6",'
                + ' "args": {"command": "just check"},'
                + ' "output": {"title": "t", "output": "", "metadata": {}}},'
                + "];"
                + "for (const call of calls) {"
                + " await handler("
                + " {tool: call.tool, sessionID: call.sessionID,"
                + " callID: call.callID, args: call.args}, call.output); }"
            )
            result = subprocess.run(
                [node, "--input-type=module", "-e", harness],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            received = (hooks / "received.jsonl").read_text(encoding="utf-8")
        records = [json.loads(line) for line in received.strip().splitlines()]
        self.assertEqual(
            records,
            [
                {
                    "session_id": "sess-1",
                    "hook_event_name": "PostToolUse",
                    "tool_input": {"command": "just check"},
                    "tool_response": {"exit_code": 1},
                },
                {
                    "session_id": "abc",
                    "hook_event_name": "PostToolUse",
                    "tool_input": {"command": "python -m unittest"},
                    "tool_response": {"exit_code": 2},
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
