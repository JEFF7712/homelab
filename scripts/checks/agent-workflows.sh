#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
if [[ "${SKIP_TESTS:-0}" != "1" ]]; then
  python -m unittest discover -s tests -p 'test_agent_*.py' -v
fi
shellcheck hooks/* scripts/checks/*.sh scripts/ci/*.sh 2>/dev/null || [[ ! -d hooks ]]
