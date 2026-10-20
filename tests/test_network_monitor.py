from __future__ import annotations

import importlib.util
import json
import struct
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
    def test_heartbeat_posts_only_when_layers_are_healthy(self) -> None:
        with (
            patch.object(
                monitor.Path, "read_text", return_value="https://hc-ping.com/test\n"
            ),
            patch.object(monitor.urllib.request, "urlopen") as urlopen,
        ):
            monitor.heartbeat(probes("dns"))
            urlopen.assert_not_called()
            monitor.heartbeat(probes("tcp:1.1.1.1", "https:1.1.1.1"))
            request = urlopen.call_args.args[0]
            self.assertEqual(request.full_url, "https://hc-ping.com/test")
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(urlopen.call_args.kwargs["timeout"], monitor.TIMEOUT)

    def test_heartbeat_failure_does_not_leak_url_or_raise(self) -> None:
        with (
            patch.object(
                monitor.Path, "read_text", return_value="https://hc-ping.com/test"
            ),
            patch.object(
                monitor.urllib.request, "urlopen", side_effect=OSError("secret URL")
            ),
            patch("builtins.print") as output,
        ):
            monitor.heartbeat(probes())
            output.assert_called_once_with("external heartbeat failed", flush=True)

    def test_truncated_dns_response_is_a_failed_probe(self) -> None:
        with patch.object(monitor, "dns", side_effect=struct.error("short reply")):
            self.assertFalse(monitor.safe_probe("dns"))

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

    def test_elapsed_threshold_and_recovery_survive_restart(self) -> None:
        state: dict[str, Any] = {}
        failed = probes("tcp:1.1.1.1", "tcp:8.8.8.8")
        for now in range(100, 160, 5):
            monitor.update(state, failed, now)
            self.assertNotIn("pending", state)
        monitor.update(state, failed, 160)
        self.assertEqual(state["outage"], 100)
        self.assertEqual(len(state["pending"]), 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            monitor.save(path, state)
            restored = json.loads(path.read_text())
        records = monitor.update(restored, probes(), 165, restarted=True)
        event = next(r for r in records if r["type"] == "outage")
        self.assertIsNone(event["duration_seconds"])
        self.assertEqual(event["observation_gaps"], 1)
        for now in range(170, 195, 5):
            monitor.update(restored, probes(), now)
            self.assertEqual(len(restored["pending"]), 1)
        monitor.update(restored, probes(), 195)
        self.assertNotIn("outage", restored)
        self.assertEqual(len(restored["pending"]), 2)
        self.assertIn("Duration uncertain", restored["pending"][1]["message"])

    def test_flapping_does_not_duplicate_alerts_but_records_each_event(self) -> None:
        state: dict[str, Any] = {}
        records = []
        for now in range(0, 65, 5):
            records += monitor.update(state, probes("dns"), now)
        records += monitor.update(state, probes(), 65)
        records += monitor.update(state, probes("gateway"), 70)
        records += monitor.update(state, probes(), 75)
        self.assertEqual(len(state["pending"]), 1)
        events = [r for r in records if r["type"] == "outage"]
        self.assertEqual([e["duration_seconds"] for e in events], [65, 5])
        self.assertEqual(state["failed_layers"], ["dns", "gateway"])

    def test_brief_failure_is_recorded_without_notification(self) -> None:
        state: dict[str, Any] = {}
        records = monitor.update(state, probes("dns"), 100)
        records += monitor.update(state, probes("dns"), 105)
        records += monitor.update(state, probes(), 110)
        event = next(r for r in records if r["type"] == "outage")
        self.assertEqual(event["start"], 100)
        self.assertEqual(event["end"], 110)
        self.assertEqual(event["duration_seconds"], 10)
        self.assertEqual(event["failed_layers"], ["dns"])
        self.assertEqual(len([r for r in records if r["type"] == "sample"]), 2)
        self.assertNotIn("first_failure", state)
        self.assertNotIn("pending", state)

    def test_single_provider_failure_is_recorded_without_outage_event(self) -> None:
        records = monitor.update({}, probes("https:1.1.1.1"), 100)
        self.assertEqual([r["type"] for r in records], ["sample"])

    def test_gap_does_not_count_toward_alert_threshold(self) -> None:
        state: dict[str, Any] = {}
        monitor.update(state, probes("dns"), 100)
        records = monitor.update(state, probes("dns"), 200)
        self.assertEqual(records[0], {"type": "gap", "start": 100, "end": 200})
        self.assertNotIn("pending", state)
        self.assertEqual(state["first_failure"], 200)
        records = monitor.update(state, probes(), 205)
        event = next(r for r in records if r["type"] == "outage")
        self.assertIsNone(event["duration_seconds"])
        self.assertEqual(event["observed_span_seconds"], 105)

    def test_open_brief_event_survives_restart(self) -> None:
        state: dict[str, Any] = {}
        monitor.update(state, probes("dns"), 100)
        restored = json.loads(json.dumps(state))
        monitor.update(restored, probes("gateway"), 105, restarted=True)
        records = monitor.update(restored, probes(), 110)
        event = next(r for r in records if r["type"] == "outage")
        self.assertEqual(event["start"], 100)
        self.assertEqual(event["failed_layers"], ["dns", "gateway"])
        self.assertEqual(event["observation_gaps"], 1)
        self.assertIsNone(event["duration_seconds"])

    def test_heartbeat_remains_thirty_seconds_and_skips_failed_due_cycle(self) -> None:
        due = 0.0
        with patch.object(monitor, "heartbeat") as heartbeat:
            for now in range(0, 61, 5):
                due = monitor.scheduled_heartbeat(probes(), now, due)
            self.assertEqual(heartbeat.call_count, 3)
        with patch.object(monitor, "heartbeat") as heartbeat:
            due = monitor.scheduled_heartbeat(probes("dns"), 90, due)
            self.assertEqual(due, 120)
            heartbeat.assert_called_once_with(probes("dns"))

    def test_history_rotation_retention_and_size_cap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history"
            with (
                patch.object(monitor, "HISTORY_CHUNK", 100),
                patch.object(monitor, "HISTORY_LIMIT", 200),
                patch.object(monitor, "HISTORY_RETENTION", 30),
            ):
                for now in range(10):
                    monitor.record_history(path, [{"type": "sample", "time": now}], now)
                files = sorted(path.glob("*.jsonl"))
                self.assertGreater(len(files), 1)
                self.assertLessEqual(sum(p.stat().st_size for p in files), 200)
                rows = [
                    json.loads(line)
                    for p in files
                    for line in p.read_text().splitlines()
                ]
                self.assertEqual(rows[-1]["time"], 9)
                monitor.record_history(path, [], 100)
                self.assertEqual(list(path.glob("*.jsonl")), [])


if __name__ == "__main__":
    unittest.main()
