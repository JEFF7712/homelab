# Agent Task: bus-display

Status: `complete`

Base commit: `27e6ce112c97f3ea74efb8421556d3dc1934bb5f`

Owner: `codex`

Session: `bus-display` (aggregates `bus-display-map`, `bus-display-map-focus`, `bus-display-map-pan`, `bus-display-map-tall`, `bus-display-calendar-routing`)

Exported at: `2026-10-06T00:00:00+00:00`

## Objective

Live Madison Metro departure board backend at `/bus-display` plus Kubernetes
deployment: GTFS-Realtime departures and vehicle positions, Outlook calendar
class routing, and a GPS-correct transit map for the ESP32 firmware.

## Acceptance criteria

- [x] GPS anchors align with baked map and distinct trips use distinct shapes; stale vehicles hidden
- [x] Only displayed departure paths render, GTFS colors match, apartment-to-stadium viewport is tight and GPS agrees
- [x] Map panned one block east at same zoom; basemap and GPS projection agree
- [x] 552x300 map with added north coverage; no connection/age/advisory clutter text
- [x] Calendar buildings select catchable direct buses, leave timing, and recommended map path; partial-path and action UI correct
- [x] Backend tests pass, image builds, Kubernetes manifests registered, registry inventory updated

## Iterations (all complete, superseded details removed)

1. `bus-display-map` (2026-10-05T20:16Z): corrected map GPS positions and
   trip-specific GTFS paths.
2. `bus-display-map-focus` (2026-10-05T20:37Z): nearby-departure-only paths,
   GTFS colors, tighter viewport.
3. `bus-display-map-pan` (2026-10-05T20:43Z): eastward pan without zoom change.
4. `bus-display-map-tall` (2026-10-05T20:52Z): taller map, more north
   coverage, clutter text removed.
5. `bus-display-calendar-routing` (2026-10-05T22:00Z): calendar-driven
   journey planning with leave timing and recommended path.

Each iteration ended with `Remaining work: None`; per-step fingerprints and
device-build logs live only in this repo's ignored `.agent-state/evidence/`
and the firmware checkout at `/home/rupan/projects/bus-route-display/`, not
in git.

## Owned source

- `services/bus-display/` (backend: `bus_service.py`, `dashboard_payload.py`,
  `transit_map.py`, `journey_planner.py`, `outlook_calendar.py`,
  `build_journey_data.py`, `build_map_shapes.py`, `journey_data.json`,
  `map_shapes.json`, `map_viewport.json`, `trips_map.json`,
  `gtfs_realtime_pb2.py`, `Dockerfile`, tests, `README.md`)
- `gitops/websites/bus-display/` (`namespace.yaml`, `service.yaml`,
  `deployment.yaml`, `network-policy.yaml`, `kustomization.yaml`)
- `gitops/websites/kustomization.yaml` (registers `bus-display`)
- `gitops/clusters/homelab-01/websites.yaml` (allows `bus-display` deploy)
- `registry/images.inventory.json`, `registry/images.lock.json`
  (`apps/bus-display:0.0.2`)

## Remaining work

- Regenerate `journey_data.json` and `map_shapes.json` when Metro publishes
  the next schedule (current schedule runs through December 5, 2026).
- Keep `map_viewport.json` identical to the firmware asset configuration.
- Container deployment needs the separately mounted Outlook URL file; local
  laptop activation does not deploy the Kubernetes workload.

## Verification

- `uv run --project services/bus-display pytest -q services/bus-display`: exit 0
- Firmware builds and live board checks recorded per iteration in ignored
  `.agent-state/evidence/checks/` and `/home/rupan/projects/bus-route-display/build/`
- Full offline gate via `just check-changed` before commit

## Next action

none
