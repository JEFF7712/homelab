import argparse
import csv
import json
from pathlib import Path

from transit_map import VIEWPORT, shape_segments


def build(gtfs: Path, output: Path) -> None:
    with (gtfs / "trips.txt").open() as f:
        trips = {r["trip_id"]: r["shape_id"] for r in csv.DictReader(f)}
    with (gtfs / "routes.txt").open() as f:
        colors = {
            r["route_id"]: {
                "color": int(r["route_color"] or "6E6A61", 16),
                "text_color": int(r["route_text_color"] or "FFFFFF", 16),
            }
            for r in csv.DictReader(f)
        }
    points: dict[str, list[tuple[int, float, float]]] = {}
    with (gtfs / "shapes.txt").open() as f:
        for r in csv.DictReader(f):
            points.setdefault(r["shape_id"], []).append(
                (
                    int(r["shape_pt_sequence"]),
                    float(r["shape_pt_lat"]),
                    float(r["shape_pt_lon"]),
                )
            )
    shapes = {
        key: shape_segments([(lat, lon) for _, lat, lon in sorted(rows)])
        for key, rows in points.items()
    }
    shapes = {key: segments for key, segments in shapes.items() if segments}
    output.write_text(
        json.dumps(
            {
                "viewport": VIEWPORT,
                "colors": colors,
                "trips": {
                    trip: shape for trip, shape in trips.items() if shape in shapes
                },
                "shapes": shapes,
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("gtfs", type=Path)
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).with_name("map_shapes.json")
    )
    args = parser.parse_args()
    build(args.gtfs, args.output)
