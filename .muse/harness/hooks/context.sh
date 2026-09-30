#!/usr/bin/env bash
set -euo pipefail
payload=$(cat)
directory=$(jq -r '.cwd // empty' <<<"$payload") || { printf '%s\n' '{}'; exit 0; }
root=$(git -C "$directory" rev-parse --show-toplevel 2>/dev/null) || { printf '%s\n' '{}'; exit 0; }
[[ -f "$root/hooks/session-start" ]] || { printf '%s\n' '{}'; exit 0; }
cd "$root"
AGENT_HOOK_CLIENT=muse bash "$root/hooks/session-start" <<<"$payload"
