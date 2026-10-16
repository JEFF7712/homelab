ARG PYTHON_BASE=registry.rupan.dev/upstream/docker.io/library/python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
FROM ${PYTHON_BASE}

WORKDIR /app
COPY requirements.txt /app/requirements.txt
COPY wheelhouse /wheelhouse
RUN pip install --no-cache-dir --no-index --require-hashes --find-links=/wheelhouse -r requirements.txt

COPY spotify_matrix_streamer.py ha_source.py ha_matrix_streamer.py /app/
ENV PYTHONUNBUFFERED=1 HOME=/tmp
USER 1000:1000
ENTRYPOINT ["python", "/app/ha_matrix_streamer.py"]
