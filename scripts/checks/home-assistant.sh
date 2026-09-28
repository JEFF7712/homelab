#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

for tool in ruff pyright python yamllint; do
  command -v "$tool" >/dev/null || {
    echo "missing required tool: $tool; run nix develop ./flake" >&2
    exit 127
  }
done

if [[ "${CHECK_FROM_ALL:-0}" != "1" ]]; then
  ruff format --check scripts/home_assistant tests/test_home_assistant_*.py
  ruff check --select E,F,I,UP --ignore E501 scripts/home_assistant tests/test_home_assistant_*.py
  pyright scripts/home_assistant
fi
if [[ "${SKIP_TESTS:-0}" != "1" ]]; then
  python -m unittest discover -s tests -p 'test_home_assistant_*.py' -v
fi
python -m scripts.home_assistant validate
if [[ "${CI_LINT_EXTERNAL:-0}" != "1" ]]; then yamllint home-assistant; fi
