# Bus display backend

Serves live Madison Metro departures and vehicle positions at `/bus-display`.
The ESP32 firmware lives in `/home/rupan/projects/bus-route-display/bus_display`.

Outlook calendar support reads a published ICS URL from the file named by
`OUTLOOK_CALENDAR_URL_FILE`. Keep that file outside the repository with mode 0600.
Do not put the URL in source, logs, or container images.

The feed refreshes every five minutes. Recurrences, exclusions, moved events,
and America/Chicago daylight saving time are handled by the calendar libraries.
The next future timed event with a location within 14 days supplies the event
title, time, and destination. All-day events, cancelled events, and events
without locations are skipped. A failed refresh keeps the last valid calendar
for at most one hour, marks it stale, and then reports calendar unavailability.
Calendar refresh runs independently of transit polling.

Calendar destinations now select direct trips from the six home-area boarding
stops. `journey_data.json` contains 206 buildings from UW's official campus map,
3,634 home-serving trips and their timed stop patterns, service calendars,
exceptions, stops and full shapes from the current Metro GTFS feed. Matching
uses whole building names/aliases or addresses, tolerates room suffixes and
punctuation, and rejects unmatched or tied locations. Unknown locations retain
nearby departures and show a compact unmatched-location state.

The planner checks stop order and pickup/dropoff restrictions. It considers
service on the class date and previous service date for after-midnight trips,
including weekday masks, exceptions and GTFS noon-minus-twelve-hours DST time.
Only catchable direct trips arriving at least five minutes before class qualify.
Walking estimates use straight-line distance with a 1.3 detour factor, 1.3 m/s
speed, rounded up to minutes. Add two minutes to board, two to enter the building,
and accept exit stops within 450 meters of the building. These are estimates,
not street-level walking directions. The recommended bus leaves home latest;
up to two distinct route alternatives appear. If no direct bus qualifies, a
walk of at most 45 minutes is offered when it still arrives on time. Transfers
and non-campus geocoding are not implemented.

Scheduled trips are available beyond the live feed's horizon, including next-day
classes. Fresh realtime stop predictions/delays override matching service
instances. Cancelled trips and skipped boarding/alighting stops are excluded;
NO_DATA resets delay propagation. Madison omits service dates, so absolute stop
predictions establish the instance when possible. Date-less delay/cancellation
updates require a nearby same-day scheduled instance. Stale realtime data falls
back to explicitly scheduled trips, and no stale vehicle is shown. The schedule
currently runs through December 5, 2026; service outside calendar validity is
never invented. Regenerate the data when Metro publishes the next schedule.

Responses include independent calendar/transit freshness flags and ages.
`journey_available`, `journey_action`, `leave_at` and building marker fields drive
the class UI. Each class departure includes boarding/exit stops, boarding time,
`journey_arrival_at`, home `leave_at`, scheduled/live status, and `recommended`.
Generic nearby departures still expire from absolute `departure_at`; class
trips expire when their home leave time passes. Calendar caches expire after one
hour. Live feed and vehicle observations expire after 90 seconds.

Use `uv sync` for dependencies, `uv run pytest` for tests, and
`uv run pyright outlook_calendar.py` for calendar type checking.
The Docker image uses `requirements.txt` and includes the calendar module;
container deployment needs a separately mounted URL file and its environment
variable. Local laptop activation does not deploy the Kubernetes workload.

Each displayed departure carries GTFS `color`, `text_color`, and its trip's
clipped `segments`. These paths remain visible without an on-map vehicle.
Vehicle markers carry only GPS pixel coordinates (`x`, `y`) and `observed_at`.
For class trips, map paths cover only boarding to alighting, with the correct
GTFS route color and a white casing around the recommendation. The destination
marker uses the resolved building coordinates, replacing the old fixed bus-stop
marker. Only GPS vehicles matching a displayed trip/service instance are shown.
For generic nearby departures, route matching remains the filter. At most four
markers are returned, and unrelated routes cannot take a marker slot.
The firmware repeats this filter when a departure expires between API polls.
Vehicle observations expire independently after 90 seconds.

Once a trip has been shown, it remains selected during its walking window until
the bus departs, so the display can change from a walking deadline to a bus
countdown. Trips that were already uncatchable when first requested remain
excluded, and cancelled trips are removed.

An independent worker refreshes Open-Meteo hourly rain and showers every 15
minutes. Each route gets `walk_rain` when at least 0.1 mm is forecast in an hour
overlapping its home-to-stop walk (excluding the two-minute boarding buffer).
Forecast failures preserve the last valid result for up to one hour. Missing,
expired, and out-of-range forecasts produce no umbrella cue. Forecast requests
run outside the transit polling and HTTP request threads.

The 552 by 300 viewport uses Web Mercator with longitudes -89.4132 to -89.3883
and latitude center 43.072912344760965. The taller view adds about 400 meters north while retaining the southern edge
and the earlier eastward pan. `map_viewport.json` mirrors the firmware asset configuration.
Keep both copies identical and regenerate both assets when adjusting the view.
Map geometry records its viewport and rejects a mismatch at service startup.

Regenerate `map_shapes.json` when updating the static GTFS dataset with
`uv run python build_map_shapes.py /path/to/extracted/gtfs`. It maps trip IDs to
shapes, simplifies geometry within one map pixel, and clips each segment
independently so offscreen excursions do not create false connecting lines.
This local backend change does not deploy the Kubernetes workload.

Regenerate `journey_data.json` alongside map data using
`uv run python build_journey_data.py /path/to/extracted/gtfs`. Building pages are
cached under `/tmp/bus-display-buildings`; remove that cache only when refreshing
the building inventory is intended. The compiler rejects untimed stops and
frequency-based service rather than inventing departure times.
