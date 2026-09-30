#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
if [[ "${CHECK_FROM_ALL:-0}" != "1" ]]; then
  ruff format --check scripts/agent tests/test_agent_*.py
  ruff check --select E,F,I,UP --ignore E501 scripts/agent tests/test_agent_*.py
  pyright scripts/agent
fi
if [[ "${SKIP_TESTS:-0}" != "1" ]]; then
  python -m unittest discover -s tests -p 'test_agent_*.py' -v
fi
shellcheck hooks/* .muse/harness/hooks/*.sh scripts/checks/*.sh scripts/ci/*.sh 2>/dev/null || [[ ! -d hooks ]]
