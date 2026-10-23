#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
# One evaluator holding every host configuration peaks above 5 GiB, which
# with the build and the other gate lanes exceeds the 8 GiB CI job limit.
# nix-eval-jobs restarts each worker past its memory cap, so the peak stays
# near workers * cap (about 3.7 GiB) as hosts are added; CI jobs get two
# CPUs, so more workers would not help. Host configurations are evaluated,
# not built, matching nix flake check; the checks themselves are built.
# A plain host with a "-registry" variant is skipped: that variant is the
# plain host plus one module, so evaluating it covers the plain host too.
flake='path:.?dir=flake'
# The selection is a Nix expression; ${name} is Nix interpolation.
# shellcheck disable=SC2016
selection='flake: let
  names = builtins.attrNames flake.nixosConfigurations;
  covered = name: builtins.elem "${name}-registry" names;
in {
  checks = flake.checks.x86_64-linux;
  hosts = flake.inputs.nixpkgs.lib.genAttrs (builtins.filter (name: !covered name) names) (
    name: flake.nixosConfigurations.${name}.config.system.build.toplevel
  );
}'
# Checks build as soon as they are evaluated, overlapping the host
# evaluations; the unit-suite build adds about 0.5 GiB to the eval peak.
# A killed check build prints only "run nix log", and the CI container is
# gone by then, so show the unit suite's tail here to name the stuck test.
nix-eval-jobs --force-recurse --flake "$flake#" --select "$selection" \
  --workers "${CI_EVAL_WORKERS:-2}" --max-memory-size "${CI_EVAL_MAX_MEMORY_MB:-2048}" |
  python -m scripts.checks.flake_jobs || {
  rc=$?
  nix log "$flake#checks.x86_64-linux.repository-contract" 2>/dev/null | tail -n 80
  exit "$rc"
}
