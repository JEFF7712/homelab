# Syntax: docker/dockerfile:1
FROM python:3.14-slim-bookworm AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY server.py /app/server.py

ENV PYTHONUNBUFFERED=1 \
    CHATTERBOX_PORT=10201 \
    CHATTERBOX_METRICS_PORT=8001 \
    CHATTERBOX_DEVICE=cuda

EXPOSE 10201 8001

USER 1000:1000

ENTRYPOINT ["python3", "/app/server.py"]
