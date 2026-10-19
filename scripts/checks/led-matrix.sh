#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../services/led-matrix"
env -u PYTHONPATH uv run --offline --frozen --no-dev python -m unittest discover -v
ruff check --select E,F,I,UP --ignore E501 ha_source.py ha_matrix_streamer.py test_ha_source.py
ruff format --check ha_source.py ha_matrix_streamer.py test_ha_source.py
env -u PYTHONPATH uv run --offline --frozen --no-dev pyright --pythonpath .venv/bin/python ha_source.py ha_matrix_streamer.py
