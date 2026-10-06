import json
import math
import re
from datetime import date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from outlook_calendar import MADISON
from transit_map import HEIGHT, WIDTH, TransitMap, project, shape_segments

CLASS_BUFFER = 5 * 60
BOARD_BUFFER = 2 * 60
ENTRY_BUFFER = 2 * 60
MAX_LAST_WALK_METERS = 450
WALK_DETOUR_FACTOR = 1.3
WALK_SPEED_MPS = 1.3


def normalize(value: str) -> str:
    value = value.lower().replace("bldg", "building")
    return " ".join(re.findall(r"[a-z0-9]+", value))


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 6371000 * 2 * math.asin(min(1, math.sqrt(h)))


def walking_seconds(meters: float) -> int:
    return max(60, math.ceil(meters * WALK_DETOUR_FACTOR / WALK_SPEED_MPS / 60) * 60)


def service_epoch(day: date) -> float:
    # GTFS service days start twelve elapsed hours before local noon, including DST transitions.
    return (
        datetime.combine(day, datetime.min.time().replace(hour=12), MADISON).timestamp()
        - 12 * 3600
    )


def clock(epoch: float, now: float) -> str:
    moment = datetime.fromtimestamp(epoch, MADISON)
    prefix = (
        "%a " if moment.date() != datetime.fromtimestamp(now, MADISON).date() else ""
    )
    return moment.strftime(prefix + "%-I:%M %p")


def realtime_updates(feed: Any) -> dict[str, Any]:
    updates = {}
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        stops = {}
        for stu in tu.stop_time_update:
            row: dict[str, Any] = {"relationship": int(stu.schedule_relationship)}
            for field in ("arrival", "departure"):
                if stu.HasField(field):
                    event = getattr(stu, field)
                    row[field] = {
                        key: int(getattr(event, key))
                        for key in ("time", "delay", "uncertainty")
                        if event.HasField(key)
                    }
            stops[str(stu.stop_sequence) if stu.stop_sequence else stu.stop_id] = row
        updates[tu.trip.trip_id] = {
            "date": tu.trip.start_date,
            "relationship": int(tu.trip.schedule_relationship),
            "timestamp": int(tu.timestamp or feed.header.timestamp),
            "stops": stops,
            "delay": int(tu.delay) if tu.HasField("delay") else None,
        }
    return updates


class JourneyPlanner:
    def __init__(self, path: Path, transit_map: TransitMap) -> None:
        self.data: dict[str, Any] = json.loads(path.read_text())
        self.map = transit_map

    def building(self, location: str) -> dict[str, Any] | None:
        text = f" {normalize(location)} "
        matches = []
        for building in self.data["buildings"]:
            aliases = {normalize(alias) for alias in building["aliases"]}
            aliases.add(
                normalize(
                    re.sub(r"\s+Building$", "", building["name"], flags=re.IGNORECASE)
                )
            )
            lengths = [
                len(alias)
                for alias in aliases
                if len(alias) >= 5 and f" {alias} " in text
            ]
            if lengths:
                matches.append((max(lengths), building))
        matches.sort(key=lambda match: match[0], reverse=True)
        if not matches or (len(matches) > 1 and matches[0][0] == matches[1][0]):
            return None
        return matches[0][1]

    def active(self, service: str, day: date) -> bool:
        key = day.strftime("%Y%m%d")
        exception = self.data["exceptions"].get(f"{service}:{key}")
        if exception is not None:
            return exception == 1
        calendar = self.data["calendar"].get(service)
        return bool(
            calendar
            and calendar["start_date"] <= key <= calendar["end_date"]
            and calendar[day.strftime("%A").lower()] == "1"
        )

    def stop_name(self, sid: str) -> str:
        value = self.data["stops"][sid]["name"]
        for old, new in (
            ("University", "Univ"),
            (" at ", " @ "),
            ("N ", ""),
            ("S ", ""),
            ("W ", ""),
            ("E ", ""),
        ):
            value = value.replace(old, new)
        return value

    def stop_point(self, sid: str) -> tuple[float, float]:
        stop = self.data["stops"][sid]
        return stop["lat"], stop["lon"]

    def path(
        self, pattern: dict[str, Any], board: list[Any], alight: list[Any]
    ) -> list[list[int]]:
        points = self.data["shapes"].get(pattern["shape"], [])
        if board[6] is None or alight[6] is None or alight[6] <= board[6]:
            return []
        selected = []
        for a, b in pairwise(points):
            if b[2] < board[6] or a[2] > alight[6] or b[2] <= a[2]:
                continue
            for dist in (max(a[2], board[6]), min(b[2], alight[6])):
                ratio = (dist - a[2]) / (b[2] - a[2])
                point = (a[0] + ratio * (b[0] - a[0]), a[1] + ratio * (b[1] - a[1]))
                if not selected or point != selected[-1]:
                    selected.append(point)
        return shape_segments(selected)

    def times(
        self, pattern: dict[str, Any], base: float, update: dict[str, Any] | None
    ) -> list[tuple[float, float, bool]]:
        result = []
        delay = update.get("delay") if update else None
        for stop in pattern["stops"]:
            arrival, departure = base + stop[2], base + stop[3]
            live = False
            row = (
                update["stops"].get(str(stop[1]), update["stops"].get(stop[0], {}))
                if update
                else {}
            )
            if row.get("relationship") == 1:
                result.append((0, 0, False))
                continue
            if row.get("relationship") == 2:
                delay = None
            for field, scheduled in (("arrival", arrival), ("departure", departure)):
                event = row.get(field, {})
                if "time" in event:
                    delay = event["time"] - scheduled
                elif "delay" in event:
                    delay = event["delay"]
                if delay is not None:
                    live = True
                if field == "arrival":
                    arrival = scheduled + (delay or 0)
                else:
                    departure = scheduled + (delay or 0)
            result.append((arrival, departure, live))
        return result

    def matches_update(
        self,
        update: dict[str, Any],
        pattern: dict[str, Any],
        base: float,
        service_date: str,
        now: float,
    ) -> bool:
        if now - update["timestamp"] > 90:
            return False
        if update["date"]:
            return update["date"] == service_date
        for stop in pattern["stops"]:
            row = update["stops"].get(str(stop[1]), update["stops"].get(stop[0], {}))
            for field, offset in (("arrival", stop[2]), ("departure", stop[3])):
                prediction = row.get(field, {}).get("time")
                if prediction:
                    return abs(prediction - base - offset) < 6 * 3600
        return (
            service_date == datetime.fromtimestamp(now, MADISON).strftime("%Y%m%d")
            and abs(base - now) < 2 * 3600
        )

    def plan(
        self, payload: dict[str, Any], updates: dict[str, Any], now: float
    ) -> dict[str, Any]:
        result = {
            **payload,
            "journey_available": False,
            "journey_action": "",
            "destination_visible": False,
        }
        start = int(payload.get("event_start_at", 0))
        if not payload.get("calendar_available") or start <= now:
            return result
        building = self.building(str(payload.get("event_loc", "")))
        if building is None:
            result["journey_action"] = "Location not matched"
            return result
        destination = (building["lat"], building["lon"])
        x, y = project(*destination)
        result.update(
            destination_name=building["short"],
            destination_x=round(x),
            destination_y=round(y),
            destination_visible=0 <= x < WIDTH and 0 <= y < HEIGHT,
            journey_available=True,
            routes=[],
            buses=[],
            event_start_at=start,
            leave_at=0,
        )
        deadline = start - CLASS_BUFFER
        day = datetime.fromtimestamp(start, MADISON).date()
        candidates = []
        for service_day in (day - timedelta(days=1), day):
            midnight = service_epoch(service_day)
            service_date = service_day.strftime("%Y%m%d")
            for tid, trip in self.data["trips"].items():
                if not self.active(trip["service"], service_day):
                    continue
                pattern = self.data["patterns"][trip["pattern"]]
                base = midnight + trip["base"]
                if base > deadline or base + pattern["stops"][-1][3] < max(
                    now, start - 90 * 60
                ):
                    continue
                update = updates.get(tid) if not payload.get("transit_stale") else None
                if update and not self.matches_update(
                    update, pattern, base, service_date, now
                ):
                    update = None
                if update and update["relationship"] != 0:
                    continue
                times = self.times(pattern, base, update)
                for i, board in enumerate(pattern["stops"]):
                    if board[0] not in self.data["origins"] or board[4] != "0":
                        continue
                    departure = times[i][1]
                    walk = walking_seconds(
                        distance(tuple(self.data["home"]), self.stop_point(board[0]))
                    )
                    leave = departure - walk - BOARD_BUFFER
                    if (
                        leave < now
                        or departure < start - 90 * 60
                        or departure > deadline
                    ):
                        continue
                    for j in range(i + 1, len(pattern["stops"])):
                        alight = pattern["stops"][j]
                        if alight[5] != "0" or not times[j][0]:
                            continue
                        meters = distance(self.stop_point(alight[0]), destination)
                        if meters > MAX_LAST_WALK_METERS:
                            continue
                        last_walk = walking_seconds(meters) + ENTRY_BUFFER
                        arrival = times[j][0] + last_walk
                        if arrival > deadline or times[j][0] < departure:
                            continue
                        candidates.append(
                            {
                                "route": trip["route"],
                                **self.map.style(trip["route"]),
                                "trip_id": tid,
                                "service_date": service_date,
                                "stop_id": board[0],
                                "alight_stop_id": alight[0],
                                "departure_at": int(departure),
                                "leave_at": int(leave),
                                "journey_arrival_at": int(arrival),
                                "minutes": math.ceil((departure - now) / 60),
                                "arrival": "~"
                                + datetime.fromtimestamp(arrival, MADISON).strftime(
                                    "%-I:%M %p"
                                ),
                                "departure_label": datetime.fromtimestamp(
                                    departure, MADISON
                                ).strftime("%-I:%M %p"),
                                "destination": building["short"],
                                "via": f"{self.stop_name(board[0])} ({walk // 60}m walk)\nOff: {self.stop_name(alight[0])}",
                                "alight_name": self.data["stops"][alight[0]]["name"],
                                "last_walk_min": last_walk // 60,
                                "live": times[i][2] and times[j][2],
                                "delay": max(
                                    0, round((departure - base - board[3]) / 60)
                                ),
                                "segments": self.path(pattern, board, alight),
                            }
                        )
        candidates.sort(
            key=lambda r: (-r["leave_at"], r["journey_arrival_at"], r["last_walk_min"])
        )
        seen = set()
        for route in candidates:
            if route["route"] in seen:
                continue
            seen.add(route["route"])
            route["recommended"] = not result["routes"]
            result["routes"].append(route)
            if len(result["routes"]) == 3:
                break
        if result["routes"]:
            best = result["routes"][0]
            result.update(
                leave_at=best["leave_at"],
                leave_by=clock(best["leave_at"], now),
                journey_action=f"Leave {clock(best['leave_at'], now)}  /  arrive ~{datetime.fromtimestamp(best['journey_arrival_at'], MADISON).strftime('%-I:%M %p')}",
            )
            result["buses"] = [
                b
                for b in payload.get("buses", [])
                if any(
                    b.get("trip_id") == r["trip_id"]
                    and (
                        b.get("service_date") == r["service_date"]
                        or (
                            not b.get("service_date")
                            and abs(r["departure_at"] - now) < 6 * 3600
                        )
                    )
                    for r in result["routes"]
                )
            ][:4]
        else:
            walk = (
                walking_seconds(distance(tuple(self.data["home"]), destination))
                + ENTRY_BUFFER
            )
            if deadline - walk >= now and walk <= 45 * 60:
                leave = int(deadline - walk)
                result.update(
                    leave_at=leave,
                    leave_by=clock(leave, now),
                    journey_action=f"Walk {walk // 60} min / leave {clock(leave, now)}",
                )
            else:
                result["journey_action"] = "No on-time direct trip"
        return result
