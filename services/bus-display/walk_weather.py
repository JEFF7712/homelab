import math
import threading
from typing import Any

import requests
from journey_planner import BOARD_BUFFER

FORECAST_MAX_AGE = 3600
FORECAST_REFRESH = 900


class WalkWeather:
    def __init__(self, home: tuple[float, float]) -> None:
        self.home = home
        self.lock = threading.Lock()
        self.fetched_at = 0.0
        self.hours: list[tuple[int, float]] = []

    def refresh(self, now: float) -> None:
        response = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": self.home[0],
                "longitude": self.home[1],
                "hourly": "rain,showers",
                "timeformat": "unixtime",
                "forecast_days": 7,
                "timezone": "UTC",
            },
            timeout=10,
        )
        response.raise_for_status()
        hourly = response.json()["hourly"]
        times, rain, showers = hourly["time"], hourly["rain"], hourly["showers"]
        if not times or not len(times) == len(rain) == len(showers):
            raise ValueError("Incomplete walking forecast")
        hours = []
        for timestamp, amount, shower in zip(times, rain, showers, strict=True):
            if amount is None or shower is None:
                continue
            amount = float(amount) + float(shower)
            if not math.isfinite(amount) or amount < 0:
                raise ValueError("Invalid walking forecast")
            hours.append((int(timestamp), amount))
        if not hours:
            raise ValueError("Missing walking forecast")
        with self.lock:
            self.hours, self.fetched_at = hours, now

    def apply(self, payload: dict[str, Any], now: float) -> dict[str, Any]:
        with self.lock:
            hours, fetched_at = self.hours, self.fetched_at
        age = max(0, int(now - fetched_at)) if fetched_at else FORECAST_MAX_AGE + 1
        available = bool(hours and fetched_at and age <= FORECAST_MAX_AGE)
        result = {**payload, "weather_available": available, "weather_age_sec": age}
        result["routes"] = []
        for source in payload.get("routes", []):
            route = dict(source)
            start = int(route.get("leave_at", 0))
            end = int(route.get("departure_at", 0)) - BOARD_BUFFER
            route["walk_rain"] = bool(
                available
                and start
                and end > start
                and any(
                    amount >= 0.1 and timestamp > start and timestamp - 3600 < end
                    for timestamp, amount in hours
                )
            )
            result["routes"].append(route)
        return result
