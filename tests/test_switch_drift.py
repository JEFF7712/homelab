from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.ci.notify import summarise_switches
from scripts.switches import ci

ROOT = Path(__file__).resolve().parents[1]


class SwitchDriftTest(unittest.TestCase):
    def test_healthy_drift_checks_only_plan_both_switches(self) -> None:
        with patch.object(
            ci, "request", return_value={"review": {"zero_change": True}}
        ) as remote:
            report = ci.drift_report("a" * 40, {}, {})
        self.assertEqual(
            [call.args[:2] for call in remote.call_args_list],
            [("plan", "downstairs"), ("plan", "upstairs")],
        )
        self.assertEqual(
            {result["status"] for result in report["switches"].values()}, {"unchanged"}
        )

    def test_drift_and_unreachable_are_distinct_and_both_are_checked(self) -> None:
        with patch.object(
            ci,
            "request",
            side_effect=[
                OSError("backend-secret unavailable"),
                {
                    "review": {
                        "zero_change": False,
                        "changes": [{"address": "port.1"}],
                        "imports": 0,
                    }
                },
            ],
        ) as remote:
            report = ci.drift_report("a" * 40, {}, {"password": "backend-secret"})
        self.assertEqual(remote.call_count, 2)
        self.assertEqual(report["switches"]["downstairs"]["status"], "error")
        self.assertEqual(report["switches"]["upstairs"]["status"], "drift")
        self.assertNotIn("backend-secret", json.dumps(report))
        title, message = summarise_switches(report)
        self.assertIn("failure", title)
        self.assertIn("downstairs: check failed", message)
        self.assertIn("upstairs: drift", message)

    def test_incomplete_review_fails_closed(self) -> None:
        for review in ({}, None, "invalid"):
            with (
                self.subTest(review=review),
                patch.object(ci, "request", return_value={"review": review}),
            ):
                report = ci.drift_report("a" * 40, {}, {})
            self.assertEqual(
                {v["status"] for v in report["switches"].values()}, {"error"}
            )

    def test_missing_results_cannot_produce_a_healthy_notification(self) -> None:
        title, message = summarise_switches({})
        self.assertIn("failure", title)
        self.assertIn("Missing switch result", message)

    def test_cli_reports_initialization_errors_and_success_exit_status(self) -> None:
        for valid in (False, True):
            with self.subTest(valid=valid), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "tofu/switches").mkdir(parents=True)
                shutil.copy(
                    ROOT / "tofu/switches/inventory.json",
                    root / "tofu/switches/inventory.json",
                )
                ca = root / "ca.pem"
                ca.write_text("test-ca")
                env = (
                    {
                        "CI_COMMIT_SHA": "a" * 40,
                        "TF_HTTP_USERNAME": "plan",
                        "TF_HTTP_PASSWORD": "test-backend",
                        "STATE_CA_FILE": str(ca),
                    }
                    if valid
                    else {}
                )
                with (
                    patch.object(ci, "ROOT", root),
                    patch.dict(os.environ, env, clear=True),
                    patch.object(
                        ci, "request", return_value={"review": {"zero_change": True}}
                    ) as remote,
                    patch("sys.argv", ["switch-ci", "drift"]),
                ):
                    self.assertEqual(ci.main(), 0 if valid else 1)
                report = json.loads(
                    (root / "artifacts/switches/drift.json").read_text()
                )
                self.assertEqual(remote.call_count, 2 if valid else 0)
                self.assertEqual(
                    {v["status"] for v in report["switches"].values()},
                    {"unchanged" if valid else "error"},
                )

    def test_manual_check_needs_no_cron_only_notification_secret(self) -> None:
        catalog = json.loads((ROOT / "config/ci/operations.json").read_text())
        manual = catalog["switches-drift"]
        nightly = catalog["switches-drift-nightly"]
        self.assertNotIn("NTFY_TOKEN", manual["environment"])
        self.assertNotIn("NTFY_TOPIC", manual["environment"])
        self.assertEqual(
            nightly["environment"]["NTFY_TOKEN"], {"from_secret": "ntfy_token"}
        )
        for operation in (manual, nightly):
            self.assertFalse(operation["mutation"])
            self.assertEqual(operation["environment"]["TF_HTTP_USERNAME"], "plan")
            self.assertEqual(operation["needs"], [])

    def test_notification_success_or_failure_cannot_hide_drift_exit(self) -> None:
        spec = json.loads((ROOT / "config/ci/operations.json").read_text())[
            "switches-drift-nightly"
        ]
        self.assertFalse(spec["mutation"])
        for drift_status, notify_status in ((0, 0), (1, 0), (1, 5)):
            with (
                self.subTest(drift=drift_status, notify=notify_status),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                binary = root / "python"
                binary.write_text(
                    f'#!{shutil.which("bash")}\nprintf \'%s\\n\' "$*" >> "$CALLS"\ncase "$*" in\n *scripts.switches.ci*) exit "$DRIFT_STATUS" ;;\n *scripts.ci.notify*) exit "$NOTIFY_STATUS" ;;\n *) exit 99 ;;\nesac\n'
                )
                binary.chmod(0o755)
                calls = root / "calls"
                result = subprocess.run(
                    ["bash", "-c", spec["commands"][0]],
                    env={
                        **os.environ,
                        "PATH": str(root) + os.pathsep + os.environ["PATH"],
                        "CALLS": str(calls),
                        "DRIFT_STATUS": str(drift_status),
                        "NOTIFY_STATUS": str(notify_status),
                    },
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, drift_status, result.stderr)
                self.assertEqual(
                    "scripts.ci.notify" in calls.read_text(), bool(drift_status)
                )


if __name__ == "__main__":
    unittest.main()
