#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../services/led-matrix"
if [[ -n "${MATRIX_LD_LIBRARY_PATH:-}" ]]; then
  export LD_LIBRARY_PATH="${MATRIX_LD_LIBRARY_PATH}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
# A hung test otherwise holds a CI slot until the 3 hour pipeline timeout
# (pipeline #3084). The watchdog runs inside the interpreter so it dumps every
# thread's stack, which shows where the hang is, then exits nonzero.
env -u PYTHONPATH uv run --offline --frozen --no-dev python -c '
import faulthandler, unittest
faulthandler.dump_traceback_later(300, exit=True)
unittest.main(module=None, argv=["unittest", "discover", "-v"])
'
# The whole service, as scripts/checks/python.sh lints it from the repository
# root; a matrix-only change runs this gate instead of that one.
ruff check --select E,F,I,UP --ignore E501 .
ruff format --check .
env -u PYTHONPATH uv run --offline --frozen --no-dev pyright --pythonpath .venv/bin/python ha_source.py ha_matrix_streamer.py
