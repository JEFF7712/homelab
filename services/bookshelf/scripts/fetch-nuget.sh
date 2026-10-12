#!/usr/bin/env bash
# Fetch the retained Bookshelf NuGet feed from Forgejo generic packages
# (JEFF7712/bookshelf-nuget v1) and verify every package against
# nuget-manifest.json. Run from the repository root:
#   FORGEJO_SOURCE_READ_TOKEN=... bash services/bookshelf/scripts/fetch-nuget.sh
set -euo pipefail

: "${FORGEJO_SOURCE_READ_TOKEN:?FORGEJO_SOURCE_READ_TOKEN is required}"
forgejo="${FORGEJO_URL:-http://10.0.30.20:3000}"
service_dir="$(cd "$(dirname "$0")/.." && pwd)"
manifest="$service_dir/nuget-manifest.json"
feed="$service_dir/nuget-feed"

mkdir -p "$feed"
python3 - "$forgejo" "$manifest" "$feed" <<'EOF'
import hashlib
import json
import os
import sys
import urllib.request

forgejo, manifest_path, feed = sys.argv[1:4]
with open(manifest_path) as handle:
    manifest = json.load(handle)
package = manifest["package"]
version = manifest["version"]
owner, name = package.split("/", 1)
for filename, item in sorted(manifest["files"].items()):
    target = os.path.join(feed, filename)
    if os.path.exists(target):
        with open(target, "rb") as handle:
            data = handle.read()
    else:
        url = f"{forgejo}/api/packages/{owner}/generic/{name}/{version}/{filename}"
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"token {os.environ['FORGEJO_SOURCE_READ_TOKEN']}"},
        )
        with urllib.request.urlopen(request, timeout=300) as response:
            data = response.read()
        with open(target, "wb") as handle:
            handle.write(data)
    actual = hashlib.sha256(data).hexdigest()
    if actual != item["sha256"]:
        raise SystemExit(f"hash mismatch for {filename}")
print(f"nuget feed ready: {len(manifest['files'])} files")
EOF
