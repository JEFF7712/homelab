import json
import unittest
from pathlib import Path

from scripts.ci.prepare_platform import secret_events

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT / "config/ci/operations.json").read_text())


class SecretEventsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.events = secret_events(CATALOG)

    def test_every_catalog_secret_allows_deployment(self) -> None:
        self.assertTrue(self.events)
        for name, allowed in self.events.items():
            self.assertIn("deployment", allowed, name)

    def test_weekly_grades_refresh_secrets_allow_cron(self) -> None:
        # Live since 2026-11-17 (operator-approved after the fact): the weekly
        # uwagent-grades-refresh downloads its npm cache with the read-only
        # supply token. Changing these lists changes secret access and needs
        # the operator's explicit approval.
        self.assertEqual(self.events["supply_read_password"], ["cron", "deployment"])
        self.assertEqual(self.events["madgrades_token"], ["cron", "deployment"])
        self.assertEqual(self.events["forgejo_publish_token"], ["cron", "deployment"])

    def test_unscheduled_write_credentials_stay_deployment_only(self) -> None:
        self.assertEqual(self.events["supply_write_token"], ["deployment"])

    def test_events_follow_the_schedule(self) -> None:
        without = secret_events(CATALOG, schedules={})
        self.assertEqual(without["supply_read_password"], ["deployment"])
        self.assertEqual(without["madgrades_token"], ["deployment"])


if __name__ == "__main__":
    unittest.main()
