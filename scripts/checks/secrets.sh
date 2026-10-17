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
    git fetch --no-tags origin "$base"
  fi
  if [[ $(git rev-parse --is-shallow-repository) == true ]]; then
    git fetch --no-tags --unshallow origin || true
  fi
  git merge-base --is-ancestor "$base" HEAD
  gitleaks detect --source . --redact --log-opts="$base..HEAD"
else
  if [[ -n "$source" && $(git rev-parse --is-shallow-repository) == true ]]; then
    # Best effort: sandboxed CI clones may hold no credential for origin
    # (the clone step used its own), so a failed unshallow must not kill
    # the scan; gitleaks then covers the history that is present, which
    # is what the pre-incremental gate scanned in that environment.
    git fetch --no-tags --unshallow origin || true
  fi
  gitleaks detect --source . --redact
fi
