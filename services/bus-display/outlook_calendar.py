from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from time import monotonic
from zoneinfo import ZoneInfo

import recurring_ical_events
import requests
from icalendar import Calendar

MADISON = ZoneInfo("America/Chicago")


@dataclass(frozen=True)
class CalendarEvent:
    title: str
    location: str
    start: datetime
    end: datetime


def upcoming_events(data: bytes, now: datetime) -> list[CalendarEvent]:
    calendar = Calendar.from_ical(data)
    if not isinstance(calendar, Calendar):
        raise TypeError("Expected one calendar")
    events = []
    for component in recurring_ical_events.of(calendar).between(
        now, now + timedelta(days=14)
    ):
        if str(component.get("STATUS", "")).upper() == "CANCELLED":
            continue
        start = component.decoded("DTSTART")
        end = component.decoded("DTEND", start)
        if not isinstance(start, datetime) or not isinstance(end, datetime):
            continue
        start = start.replace(tzinfo=start.tzinfo or MADISON).astimezone(MADISON)
        end = end.replace(tzinfo=end.tzinfo or MADISON).astimezone(MADISON)
        location = str(component.get("LOCATION", "")).strip()
        if start <= now or not location:
            continue
        events.append(
            CalendarEvent(str(component.get("SUMMARY", "Event")), location, start, end)
        )
    return sorted(events, key=lambda event: event.start)


def event_fields(event: CalendarEvent | None) -> dict[str, str | int]:
    if event is None:
        return {
            "destination": "No upcoming destination",
            "event": "No upcoming events",
            "event_title": "No upcoming events",
            "event_time": "Next 14 days",
            "event_loc": "No timed events with locations",
        }
    start = event.start.strftime("%a %m/%d %-I:%M %p")
    end = event.end.strftime("%-I:%M %p")
    return {
        "destination": event.location,
        "event": event.title,
        "event_title": event.title,
        "event_time": f"{start} - {end}",
        "event_loc": event.location,
        "event_start_at": int(event.start.timestamp()),
        "event_end_at": int(event.end.timestamp()),
    }


class OutlookCalendar:
    def __init__(self, url_file: Path):
        self.url = url_file.read_text().strip()
        if not self.url.startswith("https://outlook.office365.com/"):
            raise ValueError("Expected an HTTPS Outlook calendar feed")
        self.data: bytes | None = None
        self.fetched_at: datetime | None = None
        self.next_fetch = 0.0
        self.failed = False
        self.lock = Lock()

    def fields(
        self, now: datetime, refresh: bool = True
    ) -> dict[str, str | bool | int]:
        if refresh and monotonic() >= self.next_fetch:
            self.next_fetch = monotonic() + 300
            try:
                response = requests.get(self.url, timeout=10)
                response.raise_for_status()
                upcoming_events(response.content, now)
                with self.lock:
                    self.data = response.content
                    self.fetched_at = now
                    self.failed = False
            except (requests.RequestException, ValueError, KeyError, TypeError):
                with self.lock:
                    self.failed = True
        with self.lock:
            data, fetched_at, failed = self.data, self.fetched_at, self.failed
        if data is None or fetched_at is None or now - fetched_at > timedelta(hours=1):
            return {
                "destination": "Calendar unavailable",
                "event": "Calendar unavailable",
                "event_title": "Calendar unavailable",
                "event_time": "Retrying Outlook",
                "event_loc": "Calendar feed unavailable",
                "calendar_stale": True,
                "calendar_available": False,
                "calendar_age_sec": 0,
            }
        events = upcoming_events(data, now)
        fields = event_fields(events[0] if events else None)
        return {
            **fields,
            "calendar_stale": failed,
            "calendar_updated_at": fetched_at.isoformat(),
            "calendar_available": True,
            "calendar_age_sec": max(0, int((now - fetched_at).total_seconds())),
        }
