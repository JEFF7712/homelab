import math
from pathlib import Path

from transit_map import (
    HEIGHT,
    MAX_SEGMENTS,
    WIDTH,
    TransitMap,
    clip,
    project,
    shape_segments,
)


def test_chemistry_anchor_matches_baked_map() -> None:
    x, y = project(43.073767, -89.406352)
    assert abs(x - 152) < 1
    assert abs(y - 124) < 1


def test_different_streets_keep_different_gps_positions() -> None:
    m = TransitMap(Path(__file__).with_name("map_shapes.json"))
    a = m.vehicle("80", 43.073767, -89.406352)
    b = m.vehicle("D", 43.071, -89.406352)
    assert a and b and a["x"] == b["x"] and a["y"] != b["y"]
    assert m.path("unknown") == []


def test_known_trips_use_their_own_shapes() -> None:
    m = TransitMap(Path(__file__).with_name("map_shapes.json"))
    a = m.vehicle("80", 43.071, -89.397)
    b = m.vehicle("D", 43.071, -89.397)
    assert a and b and m.path("4954020") and m.path("206020")
    assert m.path("4954020") != m.path("206020")
    assert len(m.path("4954020")) <= MAX_SEGMENTS
    for segments in m.shapes.values():
        for x1, y1, x2, y2 in segments:
            assert 0 <= x1 < WIDTH and 0 <= x2 < WIDTH
            assert 0 <= y1 < HEIGHT and 0 <= y2 < HEIGHT


def test_outside_and_invalid_gps_are_hidden() -> None:
    m = TransitMap(Path(__file__).with_name("map_shapes.json"))
    for lat, lon in ((0, 0), (43.2, -89.4), (math.nan, -89.4), (43.07, math.inf)):
        assert m.vehicle("80", lat, lon) is None


def test_clipping_does_not_connect_offscreen_excursions() -> None:
    assert clip((-10, 50), (20, 50)) == [0, 50, 20, 50]
    assert clip((-10, -5), (20, -5)) is None
    points = [(43.071, -89.410), (43.080, -89.405), (43.071, -89.400)]
    segments = shape_segments(points)
    assert len(segments) == 2
    assert segments[0][2:] != segments[1][:2]


def test_published_route_colors():
    m = TransitMap(Path(__file__).with_name("map_shapes.json"))
    assert m.style("80") == {"color": 0x2272B5, "text_color": 0xFFFFFF}
    for route in ("C", "D", "R"):
        assert m.style(route)["color"] == 0x333366
    assert m.style("E")["color"] == 0x2272B5


def test_viewport_includes_extra_block_east_of_apartment():
    m = TransitMap(Path(__file__).with_name("map_shapes.json"))
    assert m.vehicle("80", 43.0695013, -89.3924122)
    assert m.vehicle("80", 43.070, -89.4128)
    assert m.vehicle("80", 43.070, -89.3890)
    assert m.vehicle("80", 43.070, -89.3880) is None
    assert m.vehicle("80", 43.070, -89.4150) is None


def test_taller_view_extends_north_without_more_south():
    m = TransitMap(Path(__file__).with_name("map_shapes.json"))
    assert HEIGHT == 300
    assert m.vehicle("80", 43.0764, -89.4000)
    assert m.vehicle("80", 43.0670, -89.4000) is None
    _, bottom = project(43.0680, -89.4000)
    assert 298 <= bottom < 300
