#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

base=""
source="${CI_PIPELINE_SOURCE:-${CI_PIPELINE_EVENT:-}}"
case "$source" in
  merge_request_event|pull_request) base="${CI_MERGE_REQUEST_DIFF_BASE_SHA:-${CI_MERGE_BASE:-}}" ;;
  push)
    if [[ -z "${CI_COMMIT_TAG:-}" ]]; then
      base="${CI_COMMIT_BEFORE_SHA:-${CI_PREV_COMMIT_SHA:-}}"
    fi
    ;;
esac

if [[ "$base" =~ ^[0-9a-f]{40}$ && ! "$base" =~ ^0+$ ]]; then
  if ! git cat-file -e "$base^{commit}" 2>/dev/null; then
    # Sandboxed clones may hold no credential for origin (the clone step
    # used its own), so tolerate a failed fetch and check again below.
    git fetch --no-tags origin "$base" || true
  fi
  if git cat-file -e "$base^{commit}" 2>/dev/null; then
    if [[ $(git rev-parse --is-shallow-repository) == true ]]; then
      git fetch --no-tags --unshallow origin || true
    fi
    git merge-base --is-ancestor "$base" HEAD
    gitleaks detect --source . --redact --log-opts="$base..HEAD"
    exit $?
  fi
  if [[ "${GITLAB_CI:-}" == "true" ]]; then
    echo "secret scan base $base is unavailable; refusing incomplete range" >&2
    exit 1
  fi
  echo "secret scan base unavailable in this clone; scanning present history" >&2
fi

if [[ -n "$source" && $(git rev-parse --is-shallow-repository) == true ]]; then
  git fetch --no-tags --unshallow origin || true
fi
gitleaks detect --source . --redact
