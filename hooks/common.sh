#!/usr/bin/env bash
set -euo pipefail
HOOKS_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck disable=SC2034
REPO_ROOT=$(cd "$HOOKS_DIR/.." && pwd)
hook_restore_session() {
  local bridge metadata selected trace
  bridge=$(command -v homelab-agent-session) || return 0
  case "$bridge" in
    "$REPO_ROOT"/.agent-state/client-sessions/*/homelab-agent-session) ;;
    *) return 0 ;;
  esac
  metadata=$(timeout 0.5 "$bridge" 2>/dev/null) || return 0
  [[ $(jq -r '.root // empty' <<<"$metadata" 2>/dev/null) == "$REPO_ROOT" ]] || return 0
  [[ $(jq -r '.schema_version // empty' <<<"$metadata" 2>/dev/null) == 1 ]] || return 0
  selected=$(jq -r '.task // empty' <<<"$metadata" 2>/dev/null) || return 0
  if [[ -z ${AGENT_TASK_ID:-} && $selected =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    export AGENT_TASK_ID="$selected"
  fi
  if [[ -z ${AGENT_HOOK_CLIENT:-} ]]; then
    AGENT_HOOK_CLIENT=$(jq -r '.client // empty' <<<"$metadata" 2>/dev/null) || return 0
    export AGENT_HOOK_CLIENT
  fi
  trace=$(jq -r '.trace // "0"' <<<"$metadata" 2>/dev/null) || return 0
  if [[ -z ${AGENT_HOOK_TRACE:-} && $trace == 1 ]]; then
    export AGENT_HOOK_TRACE=1
  fi
}
hook_load() {
  [[ ${AGENT_HOOK_ACTIVE:-0} != 1 ]] || return 1
  HOOK_INPUT=$(cat)
  command -v jq >/dev/null 2>&1 || return 1
  jq -e 'type == "object"' >/dev/null 2>&1 <<<"$HOOK_INPUT" || return 1
  hook_restore_session
  hook_trace received
}
hook_trace() {
  [[ ${AGENT_HOOK_TRACE:-0} == 1 ]] || return 0
  [[ -n ${HOOK_INPUT:-} ]] || return 0
  (cd "$REPO_ROOT" && AGENT_HOOK_ACTIVE=1 timeout 0.5 python -m scripts.agent.hook_runtime trace "$1" <<<"$HOOK_INPUT" >/dev/null 2>&1) || true
}
hook_session() { jq -r '.session_id // .conversation_id // "unknown"' <<<"$HOOK_INPUT"; }
hook_event() { jq -r '.hook_event_name // .event // empty' <<<"$HOOK_INPUT"; }
hook_ok() { hook_trace empty; printf '%s\n' '{}'; exit 0; }
hook_context() {
  local event
  hook_trace context
  event=$(hook_event)
  case "$event" in
    sessionStart)
      jq -n --arg value "$1" '{additional_context:$value}'
      ;;
    *)
      jq -n --arg value "$1" \
        '{hookSpecificOutput:{hookEventName:"SessionStart",additionalContext:$value}}'
      ;;
  esac
}
