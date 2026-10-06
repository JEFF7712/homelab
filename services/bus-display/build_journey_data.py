import argparse
import csv
import html
import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ORIGINS = {"2894", "2226", "973", "1006", "2242", "1230"}


def read(gtfs: Path, name: str) -> list[dict[str, str]]:
    with (gtfs / name).open() as f:
        return list(csv.DictReader(f))


def seconds(value: str) -> int:
    h, m, s = map(int, value.split(":"))
    return h * 3600 + m * 60 + s


def campus_buildings(cache: Path) -> list[dict]:
    cache.mkdir(parents=True, exist_ok=True)
    page = (
        urllib.request.urlopen("https://map.wisc.edu/buildings/", timeout=20)
        .read()
        .decode()
    )
    entries = re.findall(r'<a href="/\?initObj=([^"&]+)">([^<]+)</a>', page)

    def fetch(entry: tuple[str, str]) -> dict | None:
        code, name = entry
        path = cache / f"{code}.json"
        if path.exists():
            return json.loads(path.read_text())
        source = (
            urllib.request.urlopen(f"https://map.wisc.edu/?initObj={code}", timeout=20)
            .read()
            .decode()
        )
        match = re.search(r"window.Rails=(.*?);window.W=", source)
        if match is None:
            raise ValueError(f"Campus map has no location for {code}")
        obj = json.loads(match[1])["init_obj"]
        if not obj or not obj.get("lnglat"):
            return None
        aliases = {html.unescape(name), obj["name"], obj.get("street_address", "")}
        aliases.update(obj.get("meta", {}).get(key, "") for key in ("cname", "lname"))
        building = {
            "id": code,
            "name": obj["name"],
            "short": obj.get("meta", {}).get("cname") or obj["name"],
            "lat": obj["lnglat"][1],
            "lon": obj["lnglat"][0],
            "aliases": sorted(a for a in aliases if a),
        }
        path.write_text(json.dumps(building))
        return building

    with ThreadPoolExecutor(max_workers=4) as pool:
        return [b for b in pool.map(fetch, entries) if b is not None]


def build(gtfs: Path, cache: Path, output: Path) -> None:
    if (gtfs / "frequencies.txt").exists() and read(gtfs, "frequencies.txt"):
        raise ValueError("Frequency-based service needs a headway planner")
    stop_rows = read(gtfs, "stop_times.txt")
    wanted = {r["trip_id"] for r in stop_rows if r["stop_id"] in ORIGINS}
    stops_by_trip: dict[str, list[dict[str, str]]] = {}
    for row in stop_rows:
        if row["trip_id"] in wanted:
            stops_by_trip.setdefault(row["trip_id"], []).append(row)
    patterns: dict[str, dict] = {}
    pattern_ids: dict[str, str] = {}
    trips = {}
    for trip in read(gtfs, "trips.txt"):
        if trip["trip_id"] not in wanted:
            continue
        rows = sorted(
            stops_by_trip[trip["trip_id"]], key=lambda r: int(r["stop_sequence"])
        )
        if any(not r["arrival_time"] or not r["departure_time"] for r in rows):
            raise ValueError("Planner needs timed stop sequences")
        base = seconds(rows[0]["departure_time"])
        sequence = [
            [
                r["stop_id"],
                int(r["stop_sequence"]),
                seconds(r["arrival_time"]) - base,
                seconds(r["departure_time"]) - base,
                r.get("pickup_type") or "0",
                r.get("drop_off_type") or "0",
                float(r["shape_dist_traveled"])
                if r.get("shape_dist_traveled")
                else None,
            ]
            for r in rows
        ]
        pattern = {"shape": trip["shape_id"], "stops": sequence}
        key = json.dumps(pattern, separators=(",", ":"))
        if key not in pattern_ids:
            pid = str(len(pattern_ids))
            pattern_ids[key] = pid
            patterns[pid] = pattern
        trips[trip["trip_id"]] = {
            "route": trip["route_id"],
            "headsign": trip["trip_headsign"],
            "service": trip["service_id"],
            "pattern": pattern_ids[key],
            "base": base,
        }
    shape_ids = {p["shape"] for p in patterns.values()}
    shape_rows: dict[str, list[tuple[int, float, float, float]]] = {}
    for r in read(gtfs, "shapes.txt"):
        if r["shape_id"] in shape_ids:
            shape_rows.setdefault(r["shape_id"], []).append(
                (
                    int(r["shape_pt_sequence"]),
                    float(r["shape_pt_lat"]),
                    float(r["shape_pt_lon"]),
                    float(r["shape_dist_traveled"]),
                )
            )
    data = {
        "source": "https://transitdata.cityofmadison.com/GTFS/mmt_gtfs.zip",
        "building_source": "https://map.wisc.edu/buildings/",
        "feed": read(gtfs, "feed_info.txt")[0],
        "home": [43.0695013, -89.3924122],
        "origins": sorted(ORIGINS),
        "buildings": campus_buildings(cache),
        "trips": trips,
        "patterns": patterns,
        "stops": {
            r["stop_id"]: {
                "name": r["stop_name"],
                "lat": float(r["stop_lat"]),
                "lon": float(r["stop_lon"]),
            }
            for r in read(gtfs, "stops.txt")
        },
        "shapes": {
            key: [[lat, lon, dist] for _, lat, lon, dist in sorted(rows)]
            for key, rows in shape_rows.items()
        },
        "calendar": {r["service_id"]: r for r in read(gtfs, "calendar.txt")},
        "exceptions": {
            f"{r['service_id']}:{r['date']}": int(r["exception_type"])
            for r in read(gtfs, "calendar_dates.txt")
        },
    }
    output.write_text(json.dumps(data, separators=(",", ":")))
    print(
        f"Built {len(data['buildings'])} buildings, {len(trips)} trips, {len(patterns)} timed patterns"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("gtfs", type=Path)
    parser.add_argument(
        "--cache", type=Path, default=Path("/tmp/bus-display-buildings")
    )
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).with_name("journey_data.json")
    )
    args = parser.parse_args()
    build(args.gtfs, args.cache, args.output)
