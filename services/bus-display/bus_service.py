#!/usr/bin/env python3
"""
Madison Metro Transit Departure Board Backend for 538 W Washington Ave.
Polls Madison Metro GTFS-Realtime feeds, filters departures to UW Campus
(Chemistry Building, Morgridge Institute / Discovery Building), and serves
formatted JSON for the ESP32-S3 e-paper/LCD transit dashboard.
"""

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests
from dashboard_payload import dashboard_payload
from journey_planner import JourneyPlanner, realtime_updates
from outlook_calendar import MADISON, OutlookCalendar
from transit_map import TransitMap
from walk_weather import FORECAST_REFRESH, WalkWeather

# Add current directory to path for local gtfs_realtime_pb2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gtfs_realtime_pb2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("bus-display")

PORT = int(os.environ.get("PORT", "8080"))
TRIPS_URL = "https://metromap.cityofmadison.com/gtfsrt/trips"
VEHICLES_URL = "https://metromap.cityofmadison.com/gtfsrt/vehicles"
POLL_INTERVAL_SEC = 15
calendar_file = os.environ.get("OUTLOOK_CALENDAR_URL_FILE")
outlook_calendar = OutlookCalendar(Path(calendar_file)) if calendar_file else None

# Key origin stops serving 538 W Washington Ave:
ORIGIN_STOPS = {
    "2894": {"name": "Univ @ Bassett", "walk_min": 5, "priority": 1},
    "2226": {"name": "Broom @ Wash", "walk_min": 4, "priority": 2},
    "973": {"name": "Lake @ Johnson", "walk_min": 5, "priority": 1},  # Route 80 & 82
    "1006": {"name": "Wash @ Bedford", "walk_min": 1, "priority": 3},  # Route E
    "2242": {"name": "Wash @ Bedford", "walk_min": 1, "priority": 3},  # Route 81 & E
    "1230": {"name": "Wash @ Bassett", "walk_min": 2, "priority": 3},
}

# Clean fallback destinations if headsign is absent
ROUTE_DEFAULT_DESTS = {
    "80": "Memorial Union / Campus",
    "81": "Memorial Union",
    "82": "Campus Night Loop",
    "C": "Campus",
    "D": "Campus",
    "R": "Campus",
    "28": "Campus",
    "38": "Campus",
    "65": "Campus",
    "E": "Capitol Square",
}

# Load precompiled trips lookup map if available
TRIPS_MAP = {}
trips_map_path = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "trips_map.json"
)
if os.path.exists(trips_map_path):
    try:
        with open(trips_map_path) as f:
            TRIPS_MAP = json.load(f)
        logger.info(f"Loaded {len(TRIPS_MAP)} trip definitions from trips_map.json")
    except Exception as e:
        logger.warning(f"Could not load trips_map.json: {e}")

transit_map = TransitMap(Path(__file__).with_name("map_shapes.json"))
journey_planner = JourneyPlanner(
    Path(__file__).with_name("journey_data.json"), transit_map
)
walk_weather = WalkWeather(tuple(journey_planner.data["home"]))


# Global cached dashboard payload
current_payload_lock = threading.Lock()
transit_failed = False
current_payload = {
    "time": "--:--",
    "destination": "UW Chemistry Bldg",
    "event": "Campus Transit",
    "leave_by": "--:--",
    "routes": [],
    "buses": [],
}


def clean_destination(raw_dest: str, route_id: str) -> str:
    """Format destination for clean readability on display."""
    if not raw_dest:
        return ROUTE_DEFAULT_DESTS.get(route_id, "Campus")

    d = raw_dest.strip()
    if "U.W. HOSPITAL" in d.upper():
        return d.title()
    if "MEMORIAL UNION" in d.upper():
        return "Memorial Union"
    if "EAGLE HEIGHTS" in d.upper():
        return "Eagle Heights / Campus"
    if "JUNCTION" in d.upper():
        return d.title()
    if "MCKEE" in d.upper():
        return "McKee / Fitchburg"
    if "CAPITOL" in d.upper():
        return "Capitol Square"
    if "WHITNEY" in d.upper():
        return d.title()

    # Capitalize cleanly
    return d.title()


def fetch_and_update():
    global current_payload, transit_failed
    try:
        resp = requests.get(
            TRIPS_URL,
            headers={"User-Agent": "BusDisplay/1.0 (ESP32-S3 Homelab Service)"},
            timeout=10,
        )
        if resp.status_code != 200:
            with current_payload_lock:
                transit_failed = True
            logger.error(f"GTFS-RT feed returned HTTP {resp.status_code}")
            return
        data = resp.content
    except Exception as e:
        with current_payload_lock:
            transit_failed = True
        logger.error(f"Failed to fetch GTFS-RT feed: {e}")
        return

    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        feed.ParseFromString(data)
    except Exception as e:
        with current_payload_lock:
            transit_failed = True
        logger.error(f"Failed to parse GTFS-RT protobuf: {e}")
        return

    now = time.time()
    departures = []

    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue

        tu = entity.trip_update
        route_id = tu.trip.route_id
        trip_id = tu.trip.trip_id
        if tu.trip.schedule_relationship != 0:
            continue

        # Determine headsign
        headsign = ""
        if trip_id and trip_id in TRIPS_MAP:
            headsign = TRIPS_MAP[trip_id][1]

        for stu in tu.stop_time_update:
            if stu.schedule_relationship == 1:
                continue
            sid = stu.stop_id
            if sid in ORIGIN_STOPS:
                # Arrival or departure timestamp
                arr_ts = (
                    stu.departure.time
                    if stu.HasField("departure") and stu.departure.time > 0
                    else (
                        stu.arrival.time
                        if stu.HasField("arrival") and stu.arrival.time > 0
                        else 0
                    )
                )
                if arr_ts == 0:
                    continue

                mins = int(round((arr_ts - now) / 60.0))
                # Only include upcoming departures (0 to 75 minutes)
                if 0 <= mins <= 75:
                    delay_sec = (
                        stu.arrival.delay
                        if stu.HasField("arrival")
                        else (stu.departure.delay if stu.HasField("departure") else 0)
                    )
                    delay_min = int(round(delay_sec / 60.0))

                    dest = clean_destination(headsign, route_id)
                    stop_info = ORIGIN_STOPS[sid]

                    departures.append(
                        {
                            "route": route_id,
                            "trip_id": trip_id,
                            "destination": dest,
                            "minutes": mins,
                            "delay": delay_min,
                            "stop_id": sid,
                            "stop_name": stop_info["name"],
                            "walk_min": stop_info["walk_min"],
                            "priority": stop_info["priority"],
                            "departure_at": arr_ts,
                        }
                    )

    # Sort departures primarily by arrival minutes, then by walk priority
    departures.sort(key=lambda d: (d["minutes"], d["priority"]))

    # Deduplicate: if same route arrives at same minute, take the closer stop
    unique_departures = []
    seen = set()
    for dep in departures:
        key = (dep["route"], dep["minutes"])
        if key not in seen:
            seen.add(key)
            unique_departures.append(dep)

    # Format output for ESP32
    routes_json = []
    for dep in unique_departures[:3]:
        routes_json.append(
            {
                "route": dep["route"],
                **transit_map.style(dep["route"]),
                "segments": transit_map.path(dep["trip_id"]),
                "destination": dep["destination"],
                "minutes": dep["minutes"],
                "delay": dep["delay"],
                "departure_at": dep["departure_at"],
                "via": f"{dep['stop_name']} / {dep['walk_min']} min walk",
            }
        )

    # Current local time for Madison (America/Chicago)
    local_now = datetime.now()
    time_str = local_now.strftime("%H:%M")

    # Smart "Leave by" calculation based on the next bus
    leave_by_str = time_str
    target_event = "Campus Direct"
    target_dest = "UW Chemistry Bldg"

    if unique_departures:
        first_bus = unique_departures[0]
        walk_min = first_bus["walk_min"]
        leave_sec = first_bus["minutes"] - walk_min - 1
        if leave_sec <= 0:
            leave_by_str = "NOW"
        else:
            leave_dt = datetime.fromtimestamp(now + leave_sec * 60)
            leave_by_str = leave_dt.strftime("%H:%M")

        target_dest = first_bus["destination"]
        target_event = f"via {first_bus['stop_name']}"

    # Fetch vehicles feed for real-time corridor map tracking
    active_buses = []
    try:
        v_resp = requests.get(
            VEHICLES_URL,
            headers={"User-Agent": "BusDisplay/1.0 (ESP32-S3 Homelab Service)"},
            timeout=10,
        )
        if v_resp.status_code == 200:
            v_feed = gtfs_realtime_pb2.FeedMessage()
            v_feed.ParseFromString(v_resp.content)
            if not v_feed.header.timestamp or now - v_feed.header.timestamp > 90:
                raise ValueError("Vehicle feed is stale")
            for entity in v_feed.entity:
                if not entity.HasField("vehicle"):
                    continue
                v = entity.vehicle
                rt = v.trip.route_id
                if not rt:
                    continue
                if not v.HasField("position"):
                    continue
                if not v.timestamp or now - v.timestamp > 90:
                    continue
                bus = transit_map.vehicle(rt, v.position.latitude, v.position.longitude)
                if bus is not None:
                    bus["observed_at"] = v.timestamp
                    bus["trip_id"] = v.trip.trip_id
                    bus["service_date"] = v.trip.start_date
                    active_buses.append(bus)
            active_buses.sort(key=lambda b: (b["x"], b["y"], b["route"]))
    except Exception as e:
        logger.warning(f"Failed to fetch or parse vehicles feed: {e}")

    buses_json = active_buses

    payload = {
        "time": time_str,
        "destination": target_dest,
        "event": target_event,
        "leave_by": leave_by_str,
        "routes": routes_json,
        "buses": buses_json,
        "feed_timestamp": feed.header.timestamp,
        "trip_updates": realtime_updates(feed),
    }

    with current_payload_lock:
        current_payload = payload
        transit_failed = False

    logger.info(
        "Updated live data: %d departures, %d visible buses",
        len(routes_json),
        len(buses_json),
    )


def polling_worker():
    """Background polling loop."""
    global transit_failed
    logger.info(f"Starting GTFS-RT polling thread (interval: {POLL_INTERVAL_SEC}s)...")
    while True:
        try:
            fetch_and_update()
        except Exception as e:
            with current_payload_lock:
                transit_failed = True
            logger.error(f"Error in polling loop: {e}")
        time.sleep(POLL_INTERVAL_SEC)


class TransitRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        logger.info(f"Incoming GET {self.path} from {self.client_address[0]}")
        if self.path == "/bus-display" or self.path == "/bus-display/":
            now = time.time()
            with current_payload_lock:
                cached, failed = current_payload, transit_failed
            payload = dashboard_payload(cached, failed, now)
            updates = payload.pop("trip_updates", {})
            if outlook_calendar is not None:
                payload.update(
                    outlook_calendar.fields(
                        datetime.fromtimestamp(now, MADISON), refresh=False
                    )
                )
                payload["leave_by"] = ""
                nearby_buses = payload["buses"]
                payload["buses"] = (
                    []
                    if payload["transit_stale"]
                    else [
                        bus
                        for bus in cached.get("buses", [])
                        if bus.get("observed_at") and now - bus["observed_at"] <= 90
                    ]
                )
                payload = journey_planner.plan(payload, updates, now)
                if not payload["journey_available"]:
                    payload["buses"] = nearby_buses
            payload = walk_weather.apply(payload, now)
            data = json.dumps(payload, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/health" or self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def log_message(self, format, *args):
        pass


def run_server():
    server = HTTPServer(("0.0.0.0", PORT), TransitRequestHandler)
    logger.info(f"Starting Bus Display HTTP server on port {PORT}...")
    server.serve_forever()


def calendar_worker():
    assert outlook_calendar is not None
    while True:
        try:
            outlook_calendar.fields(datetime.now(MADISON))
        except Exception:
            logger.warning(
                "Calendar refresh failed; retrying without logging private feed details"
            )
        time.sleep(15)


def weather_worker() -> None:
    while True:
        try:
            walk_weather.refresh(time.time())
        except Exception as error:
            logger.warning("Walking forecast refresh failed: %s", type(error).__name__)
        time.sleep(FORECAST_REFRESH)


if __name__ == "__main__":
    threading.Thread(target=weather_worker, daemon=True).start()
    if outlook_calendar is not None:
        threading.Thread(target=calendar_worker, daemon=True).start()
    # Start polling thread
    t = threading.Thread(target=polling_worker, daemon=True)
    t.start()

    # Start HTTP server
    run_server()
