import math
from datetime import datetime
from typing import Any

from outlook_calendar import MADISON

TRANSIT_MAX_AGE = 90


def dashboard_payload(
    cached: dict[str, Any], failed: bool, now: float
) -> dict[str, Any]:
    payload = dict(cached)
    updated = float(payload.pop("feed_timestamp", 0))
    age = max(0, int(now - updated)) if updated else 0
    available = bool(updated and age <= TRANSIT_MAX_AGE)
    stale = failed or not available
    payload.update(
        transit_available=available,
        transit_stale=stale,
        transit_age_sec=age,
        time=datetime.fromtimestamp(now, MADISON).strftime("%H:%M"),
    )
    routes = []
    if not stale:
        for raw in cached.get("routes", []):
            departure = int(raw["departure_at"])
            if departure < now:
                continue
            route = dict(raw)
            route["minutes"] = math.ceil((departure - now) / 60)
            route["arrival"] = datetime.fromtimestamp(departure, MADISON).strftime(
                "%-I:%M %p"
            )
            route["live"] = True
            routes.append(route)
    payload["routes"] = routes[:3]
    shown_routes = {route["route"] for route in payload["routes"]}
    payload["buses"] = (
        []
        if stale
        else [
            bus
            for bus in cached.get("buses", [])
            if bus["route"] in shown_routes
            and bus.get("observed_at")
            and now - bus["observed_at"] <= TRANSIT_MAX_AGE
        ][:4]
    )
    return payload
