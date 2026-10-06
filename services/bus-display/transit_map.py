import json
import math
from itertools import pairwise
from pathlib import Path

MAX_SEGMENTS = 96
VIEWPORT = json.loads(Path(__file__).with_name("map_viewport.json").read_text())
WIDTH, HEIGHT = VIEWPORT["width"], VIEWPORT["height"]
LON_MIN, LON_MAX, LAT_CENTER = (
    VIEWPORT[key] for key in ("lon_min", "lon_max", "lat_center")
)


def mercator_y(lat: float) -> float:
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def project(lat: float, lon: float) -> tuple[float, float]:
    x = (lon - LON_MIN) / (LON_MAX - LON_MIN) * WIDTH
    y = HEIGHT / 2 - (mercator_y(lat) - mercator_y(LAT_CENTER)) * WIDTH / math.radians(
        LON_MAX - LON_MIN
    )
    return x, y


def clip(a: tuple[float, float], b: tuple[float, float]) -> list[int] | None:
    dx, dy = b[0] - a[0], b[1] - a[1]
    lo, hi = 0.0, 1.0
    for p, q in (
        (-dx, a[0]),
        (dx, WIDTH - 1 - a[0]),
        (-dy, a[1]),
        (dy, HEIGHT - 1 - a[1]),
    ):
        if p == 0:
            if q < 0:
                return None
        elif p < 0:
            lo = max(lo, q / p)
        else:
            hi = min(hi, q / p)
        if lo > hi:
            return None
    return [
        round(a[0] + lo * dx),
        round(a[1] + lo * dy),
        round(a[0] + hi * dx),
        round(a[1] + hi * dy),
    ]


def simplify(
    points: list[tuple[float, float]], tolerance: float = 1.0
) -> list[tuple[float, float]]:
    if len(points) <= 2:
        return points
    a, b = points[0], points[-1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    denom = dx * dx + dy * dy
    distances = []
    for p in points[1:-1]:
        t = (
            max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / denom))
            if denom
            else 0
        )
        distances.append(math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy))
    index = max(range(len(distances)), key=distances.__getitem__) + 1
    if distances[index - 1] <= tolerance:
        return [a, b]
    return simplify(points[: index + 1], tolerance)[:-1] + simplify(
        points[index:], tolerance
    )


def shape_segments(points: list[tuple[float, float]]) -> list[list[int]]:
    projected = simplify([project(lat, lon) for lat, lon in points])
    segments = [
        s
        for a, b in pairwise(projected)
        if (s := clip(a, b)) is not None and s[:2] != s[2:]
    ]
    return segments if len(segments) <= MAX_SEGMENTS else []


class TransitMap:
    def __init__(self, path: Path) -> None:
        data = json.loads(path.read_text())
        if data["viewport"] != VIEWPORT:
            raise ValueError(
                "Map geometry and viewport differ; regenerate map_shapes.json"
            )
        self.colors: dict[str, dict[str, int]] = data["colors"]
        self.trips: dict[str, str] = data["trips"]
        self.shapes: dict[str, list[list[int]]] = data["shapes"]

    def vehicle(self, route: str, lat: float, lon: float) -> dict | None:
        if not math.isfinite(lat) or not math.isfinite(lon) or not -85 < lat < 85:
            return None
        x, y = project(lat, lon)
        if not 0 <= x < WIDTH or not 0 <= y < HEIGHT:
            return None
        return {
            "route": route,
            "x": min(WIDTH - 1, round(x)),
            "y": min(HEIGHT - 1, round(y)),
        }

    def path(self, trip: str) -> list[list[int]]:
        return self.shapes.get(self.trips.get(trip, ""), [])

    def style(self, route: str) -> dict[str, int]:
        return self.colors.get(route, {"color": 0x6E6A61, "text_color": 0xFFFFFF})
