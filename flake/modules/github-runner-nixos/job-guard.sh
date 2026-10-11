#!/usr/bin/env bash
set -eu

reject() {
  printf 'This runner accepts only nixos-config artifact workflows from main.\n' >&2
  exit 1
}
[ "${GITHUB_REPOSITORY:-}" = JEFF7712/nixos-config ] || reject
[ "${GITHUB_REF:-}" = refs/heads/main ] || reject
case "${GITHUB_EVENT_NAME:-}:${GITHUB_WORKFLOW_REF:-}" in
  push:JEFF7712/nixos-config/.github/workflows/build-toplevel.yml@refs/heads/main|\
  workflow_dispatch:JEFF7712/nixos-config/.github/workflows/build-toplevel.yml@refs/heads/main|\
  push:JEFF7712/nixos-config/.github/workflows/build-iso.yml@refs/heads/main|\
  workflow_dispatch:JEFF7712/nixos-config/.github/workflows/build-iso.yml@refs/heads/main|\
  schedule:JEFF7712/nixos-config/.github/workflows/build-iso.yml@refs/heads/main) ;;
  *) reject ;;
esac
