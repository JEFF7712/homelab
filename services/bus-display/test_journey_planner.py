import json
from datetime import date, datetime
from pathlib import Path

import pytest
from journey_planner import BOARD_BUFFER, JourneyPlanner, service_epoch
from outlook_calendar import MADISON
from transit_map import TransitMap


def at(hour: int, minute: int = 0, day: int = 6) -> int:
    return int(datetime(2026, 10, day, hour, minute, tzinfo=MADISON).timestamp())


@pytest.fixture
def planner(tmp_path: Path) -> JourneyPlanner:
    stop = ["home", 1, 0, 0, "0", "0", 0]
    alight = ["class", 2, 15 * 60, 15 * 60, "0", "0", 1000]
    data = {
        "home": [43.07, -89.392],
        "origins": ["home"],
        "buildings": [
            {
                "name": "Chemistry Building",
                "short": "Chemistry",
                "aliases": ["Chemistry Building"],
                "lat": 43.073,
                "lon": -89.405,
            }
        ],
        "stops": {
            "home": {"name": "Home Stop", "lat": 43.07, "lon": -89.392},
            "class": {"name": "Class Stop", "lat": 43.073, "lon": -89.405},
        },
        "trips": {
            "good": {
                "route": "C",
                "service": "weekday",
                "pattern": "out",
                "base": 9 * 3600 + 30 * 60,
            },
            "wrong": {
                "route": "D",
                "service": "weekday",
                "pattern": "in",
                "base": 9 * 3600 + 30 * 60,
            },
        },
        "patterns": {
            "out": {"shape": "shape", "stops": [stop, alight]},
            "in": {
                "shape": "shape",
                "stops": [
                    ["class", 1, 0, 0, "0", "0", 0],
                    ["home", 2, 900, 900, "0", "0", 1000],
                ],
            },
        },
        "shapes": {
            "shape": [
                [43.07, -89.392, 0],
                [43.073, -89.405, 1000],
                [43.08, -89.41, 2000],
            ]
        },
        "calendar": {
            "weekday": {
                "start_date": "20260816",
                "end_date": "20261205",
                **{
                    day: "1" if day not in ("saturday", "sunday") else "0"
                    for day in (
                        "monday",
                        "tuesday",
                        "wednesday",
                        "thursday",
                        "friday",
                        "saturday",
                        "sunday",
                    )
                },
            }
        },
        "exceptions": {},
    }
    path = tmp_path / "journeys.json"
    path.write_text(json.dumps(data))
    return JourneyPlanner(path, TransitMap(Path(__file__).with_name("map_shapes.json")))


def plan(
    planner: JourneyPlanner,
    updates: dict | None = None,
    now: int | None = None,
    **fields: object,
) -> dict:
    return planner.plan(
        {
            "calendar_available": True,
            "event_loc": "Chemistry Building Room 1311",
            "event_start_at": at(10),
            "transit_stale": False,
            **fields,
        },
        updates or {},
        now or at(9),
    )


def update(**fields: object) -> dict:
    return {
        "good": {
            "date": "20261006",
            "timestamp": at(9),
            "relationship": 0,
            "delay": None,
            "stops": {},
            **fields,
        }
    }


def test_match_building_and_direct_trip_order(planner: JourneyPlanner) -> None:
    result = plan(planner)
    assert result["destination_name"] == "Chemistry"
    assert [r["trip_id"] for r in result["routes"]] == ["good"]
    best = result["routes"][0]
    assert best["recommended"] and not best["live"]
    assert best["leave_at"] == at(9, 30) - BOARD_BUFFER - 60
    assert best["journey_arrival_at"] == at(9, 48)
    assert best["segments"] and len(best["segments"]) == 1
    assert "arrive ~9:48 AM" in result["journey_action"]


@pytest.mark.parametrize("field", [4, 5])
def test_pickup_and_dropoff_restrictions(planner: JourneyPlanner, field: int) -> None:
    planner.data["patterns"]["out"]["stops"][0 if field == 4 else 1][field] = "1"
    assert not plan(planner)["routes"]


def test_uncatchable_bus_and_walking_fallback(planner: JourneyPlanner) -> None:
    result = plan(planner, now=at(9, 28))
    assert not result["routes"]
    assert result["journey_action"].startswith("Walk ")
    result = plan(planner, now=at(9, 54))
    assert result["journey_action"] == "No on-time direct trip"


def test_service_exceptions_and_expired_schedule(planner: JourneyPlanner) -> None:
    planner.data["exceptions"]["weekday:20261006"] = 2
    assert not plan(planner)["routes"]
    assert not planner.active("weekday", date(2026, 10, 10))
    planner.data["exceptions"]["weekday:20261010"] = 1
    assert planner.active("weekday", date(2026, 10, 10))
    assert not planner.active("weekday", date(2027, 10, 6))


def test_unknown_ambiguous_and_unavailable_locations(planner: JourneyPlanner) -> None:
    for fields in (
        {"event_loc": "Unlisted Venue"},
        {"calendar_available": False},
        {"event_start_at": at(8)},
    ):
        assert not plan(planner, **fields)["journey_available"]
    planner.data["buildings"].append(dict(planner.data["buildings"][0]))
    assert planner.building("Chemistry Building") is None
    assert planner.building("NotChemistry Building") is None


def test_delays_cancellations_skipped_stops_and_stale_fallback(
    planner: JourneyPlanner,
) -> None:
    result = plan(planner, update(delay=120))
    assert result["routes"][0]["live"]
    assert result["routes"][0]["departure_at"] == at(9, 32)
    assert result["routes"][0]["journey_arrival_at"] == at(9, 50)
    assert not plan(planner, update(delay=10 * 60))["routes"]
    assert not plan(planner, update(relationship=3))["routes"]
    assert not plan(planner, update(stops={"2": {"relationship": 1}}))["routes"]
    assert not plan(planner, update(stops={"1": {"relationship": 1}}))["routes"]
    for fields in ({"date": "20261005"}, {"timestamp": at(8)}):
        result = plan(planner, update(delay=600, **fields))
        assert result["routes"][0]["departure_at"] == at(9, 30)
        assert not result["routes"][0]["live"]
    result = plan(planner, update(relationship=3), transit_stale=True)
    assert result["routes"] and not result["routes"][0]["live"]


def test_exact_stop_prediction_and_no_data_reset(planner: JourneyPlanner) -> None:
    rt = update(
        stops={"1": {"departure": {"time": at(9, 31)}}, "2": {"relationship": 2}}
    )
    route = plan(planner, rt)["routes"][0]
    assert route["departure_at"] == at(9, 31)
    assert route["journey_arrival_at"] == at(9, 48)
    assert not route["live"]


def test_madison_feed_without_service_date(planner: JourneyPlanner) -> None:
    rt = update(date="", stops={"1": {"departure": {"time": at(9, 31)}}})
    route = plan(planner, rt)["routes"][0]
    assert route["live"] and route["departure_at"] == at(9, 31)
    rt["good"]["stops"]["1"]["departure"]["time"] = at(9, 31, day=5)
    route = plan(planner, rt)["routes"][0]
    assert not route["live"] and route["departure_at"] == at(9, 30)


def test_after_midnight_service_and_dst(planner: JourneyPlanner) -> None:
    planner.data["trips"]["good"]["base"] = 25 * 3600
    result = plan(planner, now=at(0), event_start_at=at(1, 30))
    assert result["routes"][0]["service_date"] == "20261005"
    assert result["routes"][0]["departure_at"] == at(1)
    day = date(2026, 11, 1)
    assert (
        service_epoch(day)
        == datetime(2026, 11, 1, 12, tzinfo=MADISON).timestamp() - 43200
    )


def test_vehicle_must_match_selected_trip_and_date(planner: JourneyPlanner) -> None:
    result = plan(
        planner,
        buses=[
            {"route": "C", "trip_id": "good", "service_date": "20261006"},
            {"route": "C", "trip_id": "wrong", "service_date": "20261006"},
        ],
    )
    assert len(result["buses"]) == 1
    assert result["buses"][0]["trip_id"] == "good"


def test_official_data_matches_actual_calendar_locations() -> None:
    planner = JourneyPlanner(
        Path(__file__).with_name("journey_data.json"),
        TransitMap(Path(__file__).with_name("map_shapes.json")),
    )
    for location in (
        "Chemistry Building Room 1311",
        "Helen C. White Hall Room 5181",
        "Discovery Building",
        "Morgridge Hall",
    ):
        building = planner.building(location)
        assert building is not None
        result = plan(planner, event_loc=location)
        assert result["routes"]
        assert result["routes"][0]["journey_arrival_at"] <= at(9, 55)
