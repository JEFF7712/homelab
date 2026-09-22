"""Regression tests for production-scoped release gating."""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.jev_decision_gate import PRODUCTION_MODEL, main


def run_gate(argv: list[str]) -> tuple[int, dict[str, dict]]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = main(argv)
    entries = {}
    for line in buffer.getvalue().splitlines():
        line = line.strip()
        if line.startswith("{"):
            entry = json.loads(line)
            entries[entry["model"]] = entry
    return code, entries


class DecisionGateScopeTest(unittest.TestCase):
    def test_production_model_passes_gate(self) -> None:
        code, _ = run_gate(["--model", PRODUCTION_MODEL])
        self.assertEqual(code, 0)

    def test_advisory_model_cannot_fail_run(self) -> None:
        code, entries = run_gate(["--model", "local-shadow"])
        self.assertEqual(code, 0)
        self.assertFalse(entries["local-shadow"]["gated"])
        self.assertTrue(entries[PRODUCTION_MODEL]["gated"])

    def test_gate_model_override_still_fails(self) -> None:
        code, _ = run_gate(
            ["--model", PRODUCTION_MODEL, "--gate-model", "local-shadow"]
        )
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
