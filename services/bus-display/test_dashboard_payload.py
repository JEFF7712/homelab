from dashboard_payload import dashboard_payload


def fixture():
    return {
        "feed_timestamp": 1000,
        "routes": [{"route": "80", "departure_at": 1120, "minutes": 2}],
        "buses": [{"route": "80", "x": 267, "y": 77, "observed_at": 1000}],
    }


def test_fresh_feed_and_expired_departure():
    payload = dashboard_payload(fixture(), False, 1061)
    assert payload["routes"][0]["minutes"] == 1
    assert payload["routes"][0]["live"] is True
    assert payload["transit_age_sec"] == 61
    assert payload["transit_stale"] is False
    cached = fixture()
    cached["feed_timestamp"] = 1121
    assert dashboard_payload(cached, False, 1121)["routes"] == []


def test_stale_feed_clears_routes_and_vehicles():
    payload = dashboard_payload(fixture(), False, 1091)
    assert payload["routes"] == []
    assert payload["buses"] == []
    assert payload["transit_stale"] is True
    assert payload["transit_available"] is False


def test_fetch_failure_and_recovery():
    cached = fixture()
    assert dashboard_payload(cached, True, 1010)["routes"] == []
    assert dashboard_payload(cached, False, 1020)["routes"][0]["route"] == "80"
    assert "live" not in cached["routes"][0]


def test_loading_and_valid_empty_response_are_distinct():
    loading = dashboard_payload({"routes": [], "buses": []}, False, 1000)
    empty = dashboard_payload(
        {"feed_timestamp": 1000, "routes": [], "buses": []}, False, 1000
    )
    assert loading["transit_available"] is False
    assert empty["transit_available"] is True
    assert empty["routes"] == []


def test_individual_vehicle_expires_with_fresh_departure_feed():
    cached = fixture()
    cached["feed_timestamp"] = 1091
    assert dashboard_payload(cached, False, 1090)["buses"]
    assert dashboard_payload(cached, False, 1091)["buses"] == []
    assert cached["buses"]


def test_only_current_departures_appear_on_map():
    cached = fixture()
    cached["buses"] += [{"route": "D", "x": 200, "y": 80, "observed_at": 1000}]
    assert [b["route"] for b in dashboard_payload(cached, False, 1000)["buses"]] == [
        "80"
    ]
    cached["routes"][0]["departure_at"] = 999
    assert dashboard_payload(cached, False, 1000)["buses"] == []


def test_departure_path_remains_without_vehicle():
    cached = fixture()
    cached["routes"][0].update(color=0x2272B5, segments=[[10, 20, 30, 40]])
    cached["buses"] = []
    payload = dashboard_payload(cached, False, 1000)
    assert payload["routes"][0]["segments"] == [[10, 20, 30, 40]]
    assert payload["routes"][0]["color"] == 0x2272B5
    assert payload["buses"] == []


def test_unrelated_vehicles_do_not_take_the_four_map_slots():
    cached = fixture()
    cached["buses"] = [{"route": "D", "observed_at": 1000}] * 4 + cached["buses"]
    assert [b["route"] for b in dashboard_payload(cached, False, 1000)["buses"]] == [
        "80"
    ]
