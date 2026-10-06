from datetime import datetime, timedelta

import requests
from outlook_calendar import MADISON, OutlookCalendar, upcoming_events


def calendar(*events: str) -> bytes:
    return (
        "BEGIN:VCALENDAR\nVERSION:2.0\n" + "\n".join(events) + "\nEND:VCALENDAR"
    ).encode()


def event(uid: str, fields: str) -> str:
    return f"BEGIN:VEVENT\nUID:{uid}\n{fields}\nEND:VEVENT"


def test_recurrence_exclusion_and_moved_instance():
    data = calendar(
        event(
            "class",
            "DTSTART;TZID=America/Chicago:20261005T085000\nDTEND;TZID=America/Chicago:20261005T094000\nRRULE:FREQ=DAILY;COUNT=4\nEXDATE;TZID=America/Chicago:20261006T085000\nSUMMARY:CHEM 562\nLOCATION:Chemistry Room 1315",
        ),
        event(
            "class",
            "RECURRENCE-ID;TZID=America/Chicago:20261007T085000\nDTSTART;TZID=America/Chicago:20261007T100000\nDTEND;TZID=America/Chicago:20261007T105000\nSUMMARY:CHEM 562\nLOCATION:New room",
        ),
    )
    events = upcoming_events(data, datetime(2026, 10, 5, 8, tzinfo=MADISON))
    assert [(e.start.day, e.start.hour, e.location) for e in events] == [
        (5, 8, "Chemistry Room 1315"),
        (7, 10, "New room"),
        (8, 8, "Chemistry Room 1315"),
    ]


def test_skip_cancelled_all_day_missing_location_and_started():
    data = calendar(
        event(
            "cancelled", "DTSTART:20261005T150000Z\nSTATUS:CANCELLED\nLOCATION:Campus"
        ),
        event("day", "DTSTART;VALUE=DATE:20261005\nLOCATION:Campus"),
        event("online", "DTSTART:20261005T150000Z"),
        event(
            "past", "DTSTART:20261005T120000Z\nDTEND:20261005T160000Z\nLOCATION:Campus"
        ),
        event(
            "valid",
            "DTSTART:20261005T170000Z\nDTEND:20261005T180000Z\nLOCATION:Library",
        ),
    )
    events = upcoming_events(data, datetime(2026, 10, 5, 8, tzinfo=MADISON))
    assert len(events) == 1
    assert events[0].start.hour == 12


def test_daylight_saving_preserves_local_class_time():
    data = calendar(
        event(
            "class",
            "DTSTART;TZID=America/Chicago:20261030T085000\nDTEND;TZID=America/Chicago:20261030T094000\nRRULE:FREQ=DAILY;COUNT=5\nLOCATION:Chemistry",
        )
    )
    events = upcoming_events(data, datetime(2026, 10, 30, 8, tzinfo=MADISON))
    assert all(e.start.hour == 8 for e in events)
    assert events[0].start.utcoffset() == timedelta(hours=-5)
    assert events[-1].start.utcoffset() == timedelta(hours=-6)


def test_failure_cache_expiry_and_recovery(tmp_path, monkeypatch):
    url = tmp_path / "url"
    url.write_text("https://outlook.office365.com/calendar.ics")
    client = OutlookCalendar(url)
    now = datetime(2026, 10, 5, 8, tzinfo=MADISON)
    data = calendar(event("class", "DTSTART:20261005T170000Z\nLOCATION:Chemistry"))
    response = requests.Response()
    response.status_code = 200
    response._content = data
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: response)
    assert client.fields(now)["calendar_stale"] is False

    def fail(*args, **kwargs):
        raise requests.ConnectionError("private URL must not appear in responses")

    monkeypatch.setattr(requests, "get", fail)
    client.next_fetch = 0
    fields = client.fields(now + timedelta(minutes=5))
    assert fields["calendar_stale"] is True
    assert fields["destination"] == "Chemistry"
    assert fields["calendar_available"] is True
    assert fields["calendar_age_sec"] == 300
    assert (
        client.fields(now + timedelta(hours=2), refresh=False)["event_title"]
        == "Calendar unavailable"
    )
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: response)
    client.next_fetch = 0
    assert client.fields(now + timedelta(hours=2))["calendar_stale"] is False


def test_empty_calendar(tmp_path, monkeypatch):
    url = tmp_path / "url"
    url.write_text("https://outlook.office365.com/calendar.ics")
    client = OutlookCalendar(url)
    response = requests.Response()
    response.status_code = 200
    response._content = calendar()
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: response)
    fields = client.fields(datetime(2026, 10, 5, 8, tzinfo=MADISON))
    assert fields["event_title"] == "No upcoming events"
    assert fields["calendar_stale"] is False


def test_invalid_feed_and_cancelled_occurrence(tmp_path, monkeypatch):
    data = calendar(
        event(
            "class",
            "DTSTART;TZID=America/Chicago:20261005T085000\nDTEND;TZID=America/Chicago:20261005T094000\nRRULE:FREQ=DAILY;COUNT=2\nLOCATION:Chemistry",
        ),
        event(
            "class",
            "RECURRENCE-ID;TZID=America/Chicago:20261006T085000\nDTSTART;TZID=America/Chicago:20261006T085000\nSTATUS:CANCELLED",
        ),
    )
    assert len(upcoming_events(data, datetime(2026, 10, 5, 8, tzinfo=MADISON))) == 1
    url = tmp_path / "url"
    url.write_text("https://outlook.office365.com/calendar.ics")
    client = OutlookCalendar(url)
    response = requests.Response()
    response.status_code = 200
    response._content = b"<html>Sign in</html>"
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: response)
    fields = client.fields(datetime(2026, 10, 5, 8, tzinfo=MADISON))
    assert fields["calendar_stale"] is True
    assert fields["event_title"] == "Calendar unavailable"


def test_calendar_preserves_long_fields(tmp_path, monkeypatch):
    url = tmp_path / "url"
    url.write_text("https://outlook.office365.com/calendar.ics")
    client = OutlookCalendar(url)
    response = requests.Response()
    response.status_code = 200
    response._content = calendar(
        event(
            "long",
            "DTSTART:20261005T170000Z\nSUMMARY:" + "é" * 50 + "\nLOCATION:" + "é" * 80,
        )
    )
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: response)
    fields = client.fields(datetime(2026, 10, 5, 8, tzinfo=MADISON))
    assert fields["event_title"] == "é" * 50
    assert fields["event_loc"] == "é" * 80
