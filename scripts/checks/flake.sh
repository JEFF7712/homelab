#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
# One evaluator holding every host configuration peaks above 5 GiB, which
# with the build and the other gate lanes exceeds the 8 GiB CI job limit.
# nix-eval-jobs restarts each worker past its memory cap, so the peak stays
# flat as hosts are added. Host configurations are evaluated, not built,
# matching nix flake check; the checks themselves are built.
flake='path:.?dir=flake'
selection='flake: {
  checks = flake.checks.x86_64-linux;
  hosts = builtins.mapAttrs (_: host: host.config.system.build.toplevel) flake.nixosConfigurations;
}'
jobs="$(mktemp)"
trap 'rm -f "$jobs"' EXIT
nix-eval-jobs --force-recurse --flake "$flake#" --select "$selection" \
  --workers "${CI_EVAL_WORKERS:-1}" --max-memory-size "${CI_EVAL_MAX_MEMORY_MB:-2048}" >"$jobs"
targets_text="$(python -m scripts.checks.flake_jobs "$jobs")"
mapfile -t targets <<<"$targets_text"
# A killed check build prints only "run nix log", and the CI container is
# gone by then, so show the unit suite's tail here to name the stuck test.
nix build --no-link --keep-going "${targets[@]}" || {
  rc=$?
  nix log "$flake#checks.x86_64-linux.repository-contract" 2>/dev/null | tail -n 80
  exit "$rc"
}
