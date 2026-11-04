from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.ci.policy import SCHEDULES

ROOT = Path(__file__).resolve().parents[1]


class SwitchScheduleTest(unittest.TestCase):
    def test_drift_runs_once_and_only_on_the_nightly_schedule(self) -> None:
        self.assertEqual(SCHEDULES["nightly"].count("switches-drift-nightly"), 1)
        for name, operations in SCHEDULES.items():
            with self.subTest(schedule=name):
                self.assertNotIn("switches-apply", operations)
                self.assertNotIn("switches-drift", operations)
                if name != "nightly":
                    self.assertNotIn("switches-drift-nightly", operations)

    def test_scheduled_operation_keeps_read_only_backend_and_no_apply_dependency(
        self,
    ) -> None:
        catalog = json.loads((ROOT / "config/ci/operations.json").read_text())
        operation = catalog["switches-drift-nightly"]
        self.assertFalse(operation["mutation"])
        self.assertEqual(operation["environment"]["TF_HTTP_USERNAME"], "plan")
        self.assertEqual(operation["needs"], [])
        self.assertEqual(operation["prerequisite"], "")
        self.assertEqual(operation["group"], catalog["switches-plan"]["group"])


if __name__ == "__main__":
    unittest.main()
