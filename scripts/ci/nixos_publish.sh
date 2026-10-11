#!/usr/bin/env bash
# Publish nixos-config artifacts (laptop toplevel and installer ISO) to Attic.
# Source is Forgejo-authoritative: the main tip is resolved via the read-only
# API identity, cloned, and verified before anything builds, so a stale or
# raced checkout fails closed instead of publishing the wrong tree.
set -euo pipefail

: "${FORGEJO_SOURCE_READ_TOKEN:?FORGEJO_SOURCE_READ_TOKEN is required}"
forgejo="${FORGEJO_URL:-http://10.0.30.20:3000}"
owner_repo="${NIXOS_REPOSITORY:-JEFF7712/nixos-config}"
target_set="${NIXOS_TARGET_SET:-nixos-laptop}"

work=$(mktemp -d "${TMPDIR:-/tmp}/nixos-publish-XXXXXX")
trap 'rm -rf "$work"' EXIT

tip=$(python3 - "$forgejo" "$owner_repo" <<'EOF'
import json
import os
import sys
import urllib.request

forgejo, owner_repo = sys.argv[1:3]
request = urllib.request.Request(
    f"{forgejo}/api/v1/repos/{owner_repo}/branches/main",
    headers={"Authorization": f"token {os.environ['FORGEJO_SOURCE_READ_TOKEN']}"},
)
with urllib.request.urlopen(request, timeout=60) as response:
    print(json.load(response)["commit"]["id"])
EOF
)
echo "nixos-config main tip: $tip"
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0="url.https://git.rupan.dev/.insteadOf"
export GIT_CONFIG_VALUE_0="https://homelab-ci-source:${FORGEJO_SOURCE_READ_TOKEN}@git.rupan.dev/"
git clone --branch main --depth 50 "https://git.rupan.dev/$owner_repo.git" "$work/nixos-config"
unset GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0
actual=$(git -C "$work/nixos-config" rev-parse HEAD)
if [[ "$actual" != "$tip" ]]; then
  echo "Cloned tree $actual does not match resolved tip $tip" >&2
  exit 1
fi
echo "building $target_set from $actual"
bash scripts/ci/cache.sh "$work/nixos-config" "$target_set"
echo "published nixos-config $actual ($target_set)"
