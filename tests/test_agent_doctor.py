import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.agent.doctor import run_doctor

ROOT = Path(__file__).resolve().parents[1]


class AgentDoctorTest(unittest.TestCase):
    def test_invalid_adapter_syntax_fails_without_echoing_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            shutil.copy(ROOT / ".gitlab-ci.yml", repository / ".gitlab-ci.yml")
            (repository / ".codex").mkdir()
            (repository / ".codex/hooks.json").write_text(
                '{"token": "synthetic-private-value"'
            )
            payload = run_doctor(repository)
        item = next(
            item
            for item in payload["checks"]
            if item["name"] == "adapter:.codex/hooks.json"
        )
        self.assertEqual(item["status"], "fail")
        self.assertNotIn("synthetic-private-value", str(payload))

    def test_doctor_reports_structured_results_without_secret_values(self) -> None:
        payload = run_doctor(ROOT)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["command"], "doctor")
        self.assertTrue(payload["checks"])
        self.assertTrue(
            all(
                item["status"] in {"pass", "fail", "unavailable"}
                for item in payload["checks"]
            )
        )
        self.assertTrue(
            all(
                item["remedy"] for item in payload["checks"] if item["status"] != "pass"
            )
        )
        rendered = str(payload)
        self.assertNotIn("OPNSENSE_API_SECRET=", rendered)

    def test_doctor_covers_hooks_adapters_and_hook_dependencies(self) -> None:
        payload = run_doctor(ROOT)
        names = [item["name"] for item in payload["checks"]]
        for expected in (
            "command:jq",
            "command:node",
            "command:timeout",
            "adapter:.codex/hooks.json",
            "adapter:opencode.json",
            "adapter:.mcp.json",
            "adapter:.opencode/plugins/agent-harness.js",
            "hook:hooks/session-start",
            "hook:hooks/stop",
            "hook:hooks/validation-result",
            "credential-reference:SSH_DEPLOY_KEY",
            "credential-reference:HASS_TOKEN",
        ):
            self.assertIn(expected, names)


if __name__ == "__main__":
    unittest.main()
