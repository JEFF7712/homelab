from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Environment variables that carry a paid inference credential.
PAID_KEY = re.compile(r"\bname:\s*(OPENAI_API_KEY|ANTHROPIC_API_KEY)\b")


class SpendGateTest(unittest.TestCase):
    """Every paid consumer is declared where the operator caps spending."""

    def setUp(self) -> None:
        self.spend = json.loads((ROOT / "config/spend.json").read_text())

    def test_caps_are_whole_non_negative_amounts(self) -> None:
        self.assertEqual(self.spend["currency"], "USD")
        caps = [self.spend["monthly_cap"]] + [
            consumer["monthly_cap"] for consumer in self.spend["consumers"].values()
        ]
        for cap in caps:
            self.assertIsInstance(cap, int)
            self.assertGreaterEqual(cap, 0)
        self.assertLessEqual(sum(caps[1:]), caps[0], "consumers exceed the total")

    def test_declared_manifests_exist(self) -> None:
        for name, consumer in self.spend["consumers"].items():
            with self.subTest(consumer=name):
                self.assertTrue((ROOT / consumer["manifest"]).is_file())

    def test_every_paid_key_consumer_is_declared(self) -> None:
        declared = {c["manifest"] for c in self.spend["consumers"].values()}
        consumers = {
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "gitops").rglob("*.yaml")
            if PAID_KEY.search(path.read_text(errors="replace"))
        }
        self.assertEqual(consumers - declared, set())


if __name__ == "__main__":
    unittest.main()
