FROM python:3.13-slim-bookworm AS runtime

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY gtfs_realtime_pb2.py /app/gtfs_realtime_pb2.py
COPY trips_map.json /app/trips_map.json
COPY bus_service.py /app/bus_service.py
COPY outlook_calendar.py /app/outlook_calendar.py
COPY dashboard_payload.py /app/dashboard_payload.py
COPY transit_map.py /app/transit_map.py
COPY journey_planner.py /app/journey_planner.py
COPY journey_data.json /app/journey_data.json
COPY map_shapes.json /app/map_shapes.json
COPY map_viewport.json /app/map_viewport.json

ENV PYTHONUNBUFFERED=1 \
    PORT=8080

EXPOSE 8080

USER 1000:1000

ENTRYPOINT ["python3", "/app/bus_service.py"]
