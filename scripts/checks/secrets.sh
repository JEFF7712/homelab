#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

base=""
case "${CI_PIPELINE_SOURCE:-}" in
  merge_request_event) base="${CI_MERGE_REQUEST_DIFF_BASE_SHA:-}" ;;
  push)
    if [[ -z "${CI_COMMIT_TAG:-}" ]]; then
      base="${CI_COMMIT_BEFORE_SHA:-}"
    fi
    ;;
esac

if [[ "$base" =~ ^[0-9a-f]{40}$ && ! "$base" =~ ^0+$ ]]; then
  if ! git cat-file -e "$base^{commit}" 2>/dev/null; then
    git fetch --no-tags origin "$base"
  fi
  if [[ $(git rev-parse --is-shallow-repository) == true ]]; then
    git fetch --no-tags --unshallow origin
  fi
  git merge-base --is-ancestor "$base" HEAD
  gitleaks detect --source . --redact --log-opts="$base..HEAD"
else
  if [[ -n "${CI_PIPELINE_SOURCE:-}" && $(git rev-parse --is-shallow-repository) == true ]]; then
    git fetch --no-tags --unshallow origin
  fi
  gitleaks detect --source . --redact
fi
