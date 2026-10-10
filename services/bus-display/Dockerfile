# Sovereign build: digest-pinned local base, hermetic retained wheelhouse.
# Local builds need wheelhouse/ populated first (retained in Forgejo
# generic packages JEFF7712/bus-display-wheels v1; verify against the
# manifest with scripts/fetch-wheels.sh if present).
FROM registry.rupan.dev/upstream/docker.io/library/python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS runtime

WORKDIR /app

COPY requirements.txt /app/requirements.txt
COPY wheelhouse /wheelhouse

RUN pip install --no-cache-dir --no-index --find-links=/wheelhouse -r /app/requirements.txt

COPY gtfs_realtime_pb2.py /app/gtfs_realtime_pb2.py
COPY trips_map.json /app/trips_map.json
COPY bus_service.py /app/bus_service.py
COPY outlook_calendar.py /app/outlook_calendar.py
COPY dashboard_payload.py /app/dashboard_payload.py
COPY transit_map.py /app/transit_map.py
COPY journey_planner.py /app/journey_planner.py
COPY walk_weather.py /app/walk_weather.py
COPY journey_data.json /app/journey_data.json
COPY map_shapes.json /app/map_shapes.json
COPY map_viewport.json /app/map_viewport.json

ENV PYTHONUNBUFFERED=1 \
    PORT=8080

EXPOSE 8080

USER 1000:1000

ENTRYPOINT ["python3", "/app/bus_service.py"]
