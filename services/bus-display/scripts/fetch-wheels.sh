#!/usr/bin/env bash
# Populate services/bus-display/wheelhouse/ from the retained Forgejo supply
# package (JEFF7712/bus-display-wheels v1) and verify every wheel against
# wheelhouse-manifest.json. Run from the repository root:
#   FORGEJO_SOURCE_READ_TOKEN=... bash services/bus-display/scripts/fetch-wheels.sh
set -euo pipefail

: "${FORGEJO_SOURCE_READ_TOKEN:?FORGEJO_SOURCE_READ_TOKEN is required}"
forgejo="${FORGEJO_URL:-http://10.0.30.20:3000}"
service_dir="$(cd "$(dirname "$0")/.." && pwd)"
manifest="$service_dir/wheelhouse-manifest.json"
wheelhouse="$service_dir/wheelhouse"

mkdir -p "$wheelhouse"
python3 - "$forgejo" "$manifest" "$wheelhouse" <<'EOF'
import hashlib
import json
import os
import sys
import urllib.request

forgejo, manifest_path, wheelhouse = sys.argv[1:4]
with open(manifest_path) as handle:
    manifest = json.load(handle)
package = manifest["package"]
version = manifest["version"]
owner, name = package.split("/", 1)
for filename, expected in sorted(manifest["files"].items()):
    target = os.path.join(wheelhouse, filename)
    url = f"{forgejo}/api/packages/{owner}/generic/{name}/{version}/{filename}"
    request = urllib.request.Request(
        url, headers={"Authorization": f"token {os.environ['FORGEJO_SOURCE_READ_TOKEN']}"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise SystemExit(f"hash mismatch for {filename}: {actual} != {expected}")
    with open(target, "wb") as handle:
        handle.write(data)
    print(f"verified {filename}")
print(f"wheelhouse ready: {len(manifest['files'])} files")
EOF
