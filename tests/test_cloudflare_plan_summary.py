"""Behaviour of the Cloudflare plan summary, including the first-import gate."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.cloudflare.plan_summary import changed_resources, main

NOOP = {
    "resource_changes": [
        {
            "address": "cloudflare_zero_trust_tunnel_cloudflared_config.homelab",
            "change": {"actions": ["no-op"]},
        }
    ]
}
DIRTY = {
    "resource_changes": [
        {
            "address": "cloudflare_zero_trust_tunnel_cloudflared_config.homelab",
            "change": {"actions": ["update"]},
        }
    ]
}


def write(plan: dict) -> Path:
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    )
    json.dump(plan, handle)
    handle.close()
    return Path(handle.name)


class TestPlanSummary(unittest.TestCase):
    def test_noop_plan_reports_nothing(self) -> None:
        self.assertEqual(changed_resources(NOOP), [])

    def test_dirty_plan_names_the_resource(self) -> None:
        self.assertEqual(
            changed_resources(DIRTY),
            [
                {
                    "address": "cloudflare_zero_trust_tunnel_cloudflared_config.homelab",
                    "actions": ["update"],
                }
            ],
        )

    def test_changes_are_reported_without_failing(self) -> None:
        plan = write(DIRTY)
        self.addCleanup(plan.unlink)
        self.assertEqual(main([str(plan)]), 0)

    def test_expect_noop_turns_a_change_into_a_failure(self) -> None:
        plan = write(DIRTY)
        self.addCleanup(plan.unlink)
        self.assertEqual(main([str(plan), "--expect-noop"]), 1)

    def test_expect_noop_passes_on_a_clean_plan(self) -> None:
        plan = write(NOOP)
        self.addCleanup(plan.unlink)
        self.assertEqual(main([str(plan), "--expect-noop"]), 0)


if __name__ == "__main__":
    unittest.main()
