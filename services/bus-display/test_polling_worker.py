import importlib

import pytest
from dashboard_payload import dashboard_payload


def test_unexpected_poll_failure_invalidates_shared_feed(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("OUTLOOK_CALENDAR_URL_FILE", raising=False)
    service = importlib.import_module("bus_service")
    cached = {
        "feed_timestamp": 1000,
        "routes": [{"route": "80", "departure_at": 1120}],
        "buses": [{"route": "80", "progress": 0.5}],
    }
    monkeypatch.setattr(service, "transit_failed", False)

    def fail_fetch() -> None:
        raise ValueError("Invalid transit update")

    class StopPolling(Exception):
        pass

    def stop_after_iteration(_seconds: float) -> None:
        raise StopPolling

    monkeypatch.setattr(service, "fetch_and_update", fail_fetch)
    monkeypatch.setattr(service.time, "sleep", stop_after_iteration)
    with pytest.raises(StopPolling):
        service.polling_worker()
    payload = dashboard_payload(cached, service.transit_failed, 1010)
    assert payload["transit_stale"] is True
    assert payload["routes"] == []
    assert payload["buses"] == []
