from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "gitops/observability/network-monitor/monitor.py"
)
SPEC = importlib.util.spec_from_file_location("network_monitor", SOURCE)
assert SPEC is not None and SPEC.loader is not None
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)


def probes(*failed: str) -> dict[str, bool]:
    names = [
        "gateway",
        "dns",
        "tcp:1.1.1.1",
        "tcp:8.8.8.8",
        "https:1.1.1.1",
        "https:8.8.8.8",
    ]
    return {name: name not in failed for name in names}


class NetworkMonitorTests(unittest.TestCase):
    def test_failed_delivery_retries_in_order(self) -> None:
        events = [{"title": "outage"}, {"title": "recovery"}]
        state: dict[str, Any] = {"pending": events.copy()}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            monitor.save(path, state)
            with patch.object(monitor, "publish", side_effect=OSError("offline")):
                monitor.deliver(path, state)
            self.assertEqual(json.loads(path.read_text())["pending"], events)
            with patch.object(monitor, "publish") as publish:
                monitor.deliver(path, state)
            self.assertEqual([call.args[0] for call in publish.call_args_list], events)
            self.assertEqual(json.loads(path.read_text())["pending"], [])

    def test_single_provider_failure_is_healthy(self) -> None:
        self.assertEqual(monitor.failures(probes("tcp:1.1.1.1", "https:1.1.1.1")), [])

    def test_failure_threshold_and_recovery_survive_restart(self) -> None:
        state: dict[str, Any] = {}
        failed = probes("tcp:1.1.1.1", "tcp:8.8.8.8")
        for now in (100, 130):
            monitor.update(state, failed, now)
            self.assertNotIn("pending", state)
        monitor.update(state, failed, 160)
        self.assertEqual(state["outage"], 100)
        self.assertEqual(len(state["pending"]), 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            monitor.save(path, state)
            restored = json.loads(path.read_text())
        monitor.update(restored, probes(), 190)
        self.assertEqual(len(restored["pending"]), 1)
        monitor.update(restored, probes(), 220)
        self.assertNotIn("outage", restored)
        self.assertEqual(len(restored["pending"]), 2)
        self.assertIn("120 seconds", restored["pending"][1]["message"])

    def test_flapping_does_not_duplicate_outage(self) -> None:
        state: dict[str, Any] = {}
        for now in (0, 30, 60, 90):
            monitor.update(state, probes("dns"), now)
        monitor.update(state, probes(), 120)
        monitor.update(state, probes("gateway"), 150)
        self.assertEqual(len(state["pending"]), 1)
        self.assertEqual(state["failed_layers"], ["dns", "gateway"])

    def test_brief_failure_clears_after_recovery(self) -> None:
        state: dict[str, Any] = {}
        monitor.update(state, probes("dns"), 0)
        monitor.update(state, probes(), 30)
        monitor.update(state, probes(), 60)
        self.assertNotIn("first_failure", state)
        self.assertNotIn("pending", state)


if __name__ == "__main__":
    unittest.main()
