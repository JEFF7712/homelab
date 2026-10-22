#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
export SKIP_TESTS=1
export SKIP_NIX_EVAL=1
export CHECK_FROM_ALL=1

# Independent lanes run concurrently; each lane's output is buffered and
# printed in lane order once all lanes finish, so logs stay readable and
# every failure is reported (not just the first). Set CHECK_SERIAL=1 to
# restore sequential execution for debugging.
log_dir="$(mktemp -d)"
trap 'rm -rf "$log_dir"' EXIT
lane_names=()
lane_pids=()

run_lane() {
  local name="$1"
  shift
  lane_names+=("$name")
  if [[ "${CHECK_SERIAL:-0}" == "1" ]]; then
    local lane_rc=0
    python -m scripts.ci.run "check-$name" -- "$@" >"$log_dir/$name.log" 2>&1 || lane_rc=$?
    printf '%s' "$lane_rc" >"$log_dir/$name.rc"
    lane_pids+=("")
  else
    python -m scripts.ci.run "check-$name" -- "$@" >"$log_dir/$name.log" 2>&1 &
    lane_pids+=("$!")
  fi
}

# When the flake check below will run, repository-contract already runs the
# full unit suite in the Nix sandbox, so the local Python lane shrinks to
# the Nix-integration subset instead of running the suite twice. Callers
# that need a JUnit report from this lane (CI_TEST_REPORT) keep the full run.
if [[ "${SKIP_FLAKE_CHECK:-0}" != "1" && -z "${CI_TEST_REPORT:-}" ]]; then
  export CI_UNIT_TESTS_EXTERNAL="${CI_UNIT_TESTS_EXTERNAL:-1}"
fi

run_lane agent-workflows bash scripts/checks/agent-workflows.sh
run_lane python bash scripts/checks/python.sh
run_lane registry bash scripts/checks/registry.sh
run_lane nix bash scripts/checks/nix.sh all
run_lane gitops bash scripts/checks/gitops.sh
run_lane tofu bash scripts/checks/tofu.sh
run_lane home-assistant bash scripts/checks/home-assistant.sh
run_lane docs bash -c 'python -m scripts.checks.docs && python scripts/checks/whitespace.py'
if [[ "${SKIP_SECRET_SCAN:-0}" != "1" ]]; then
  run_lane secrets bash scripts/checks/secrets.sh
fi
if [[ "${SKIP_FLAKE_CHECK:-0}" != "1" ]]; then
  run_lane flake bash -c "nix flake check 'path:.?dir=flake' --no-write-lock-file"
fi

rc=0
for i in "${!lane_names[@]}"; do
  name="${lane_names[$i]}"
  pid="${lane_pids[$i]}"
  lane_rc=0
  if [[ -n "$pid" ]]; then
    wait "$pid" || lane_rc=$?
  else
    lane_rc="$(cat "$log_dir/$name.rc")"
  fi
  printf '===== %s =====\n' "$name"
  cat "$log_dir/$name.log"
  if [[ "$lane_rc" -ne 0 ]]; then
    printf 'lane %s failed with exit %s\n' "$name" "$lane_rc" >&2
    rc=1
  fi
done
exit "$rc"
