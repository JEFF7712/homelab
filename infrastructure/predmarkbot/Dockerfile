# syntax=docker/dockerfile:1.7

# ─── Builder stage: resolve deps with uv ───
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

# Install uv. Pinned for reproducibility; bump via Renovate.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /build
COPY src/pyproject.toml src/uv.lock ./
COPY src/src ./src

# Sync the locked deps + install the package into /opt/venv.
# --no-editable ensures a proper wheel install (no .pth pointing at /build).
RUN uv sync --frozen --no-dev --no-editable

# ─── Runtime stage: minimal ───
FROM python:3.12-slim AS runtime

# Run as a non-root user. UID 1000 to match Longhorn fsGroup convention.
RUN groupadd --system --gid 1000 predmark \
 && useradd  --system --uid 1000 --gid predmark --home-dir /home/predmark predmark \
 && mkdir -p /home/predmark /var/lib/predmarkbot \
 && chown -R predmark:predmark /home/predmark /var/lib/predmarkbot

# Copy the prepared venv. /opt/venv/bin is added to PATH so `predmarkbot` works.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER predmark
WORKDIR /home/predmark

# Default: long-lived run mode. config is mounted at /etc/predmarkbot/config.yaml.
ENTRYPOINT ["predmarkbot"]
CMD ["run", "--config", "/etc/predmarkbot/config.yaml"]
