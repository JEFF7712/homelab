from typing import Any

import pytest
from walk_weather import FORECAST_MAX_AGE, WalkWeather


def test_rain_cue_matches_walking_window_and_expires() -> None:
    weather = WalkWeather((43.07, -89.39))
    weather.fetched_at = 1000
    weather.hours = [(3600, 0.0), (7200, 0.3), (10800, 0.0)]
    payload: dict[str, Any] = {
        "routes": [
            {"leave_at": 4000, "departure_at": 4720},
            {"leave_at": 8000, "departure_at": 8720},
        ]
    }
    result = weather.apply(payload, 1100)
    assert result["weather_available"]
    assert [r["walk_rain"] for r in result["routes"]] == [True, False]
    assert "walk_rain" not in payload["routes"][0]
    result = weather.apply(payload, 1001 + FORECAST_MAX_AGE)
    assert not result["weather_available"]
    assert not any(r["walk_rain"] for r in result["routes"])


def test_missing_forecast_has_no_rain_cue() -> None:
    weather = WalkWeather((43.07, -89.39))
    result = weather.apply({"routes": [{"leave_at": 4000, "departure_at": 4720}]}, 1000)
    assert not result["weather_available"]
    assert not result["routes"][0]["walk_rain"]


def test_failed_refresh_preserves_last_valid_forecast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    weather = WalkWeather((43.07, -89.39))
    weather.fetched_at = 1000
    weather.hours = [(7200, 0.3)]

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return {"hourly": {"time": [7200], "rain": [], "showers": [0.0]}}

    monkeypatch.setattr("walk_weather.requests.get", lambda *args, **kwargs: Response())
    with pytest.raises(ValueError):
        weather.refresh(1100)
    assert weather.fetched_at == 1000 and weather.hours == [(7200, 0.3)]
