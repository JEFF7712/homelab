from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts.agent.client_smoke import inspect_receipts, run_smoke


class ClientSmokeEvidenceTest(unittest.TestCase):
    def test_opencode_failure_only_evidence_no_longer_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                mock.patch(
                    "scripts.agent.client_smoke.resume_task",
                    return_value={"task": {"status": "active"}},
                ),
                mock.patch(
                    "scripts.agent.client_smoke.collect_git_state",
                    return_value=SimpleNamespace(root=root, fingerprint="fixture"),
                ),
                mock.patch("scripts.agent.client_smoke.subprocess.run") as version,
                mock.patch("scripts.agent.client_smoke.subprocess.Popen") as client,
                mock.patch(
                    "scripts.agent.client_smoke.inspect_receipts",
                    return_value={"context": False, "failure": True, "stop": False},
                ),
            ):
                version.return_value.stdout = "1.18.32"
                client.return_value.returncode = 0
                client.return_value.communicate.return_value = ("HOOK_SMOKE_DONE", "")
                report = run_smoke(root, "selected", "opencode")
            self.assertEqual(report["status"], "fail")

    def test_old_and_other_task_receipts_cannot_prove_current_integration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipts = root / ".agent-state/evidence/hook-integration"
            failures = root / ".agent-state/evidence/hooks"
            receipts.mkdir(parents=True)
            failures.mkdir()
            entries = [
                {
                    "client": "codex",
                    "selected_task": "target",
                    "timestamp": "2026-09-29T00:00:00Z",
                    "phase": "context",
                    "event": "SessionStart",
                    "session_id": "old",
                },
                {
                    "client": "codex",
                    "selected_task": "other",
                    "timestamp": "2026-09-30T01:00:00Z",
                    "phase": "context",
                    "event": "SessionStart",
                    "session_id": "other",
                },
                {
                    "client": "codex",
                    "selected_task": "target",
                    "timestamp": "2026-09-30T01:00:00Z",
                    "phase": "received",
                    "event": "PostToolUse",
                    "session_id": "current",
                },
            ]
            (receipts / "fixture.jsonl").write_text(
                "\n".join(json.dumps(item) for item in entries)
            )
            (failures / "current.jsonl").write_text(
                json.dumps(
                    {
                        "exit_code": 7,
                        "check": "python -m scripts.agent verify --json",
                        "timestamp": "2026-09-30T01:00:00Z",
                    }
                )
            )
            stages = inspect_receipts(root, "target", "codex", "2026-09-30T00:00:00Z")
        self.assertEqual(stages, {"context": False, "failure": True, "stop": False})
