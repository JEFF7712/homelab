#!/bin/sh
# Build Valhalla routing tiles for the extract geo.py fetched (geo-build.json),
# on node-local disk in /work, then copy them to /maps/routing/<name>. The
# request file is removed only after a complete build, so a failed build
# leaves it for finish to report. No request: nothing to do.
set -eu
request=/maps/geo-build.json
if [ ! -f "$request" ]; then
  echo "routing: no build requested"
  exit 0
fi
field() { python3 -c "import json, sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])" "$request" "$1"; }
pbf="/maps/$(field pbf)"
out="/maps/$(field out)"
echo "routing: building $(basename "$out") from $(basename "$pbf")"

rm -rf /work/build
mkdir -p /work/build/tiles
valhalla_build_config \
  --mjolnir-tile-dir /work/build/tiles \
  --mjolnir-tile-extract /work/build/valhalla_tiles.tar \
  --mjolnir-admin /work/build/admins.sqlite \
  --mjolnir-concurrency 4 > /work/build/build.json
valhalla_build_admins -c /work/build/build.json "$pbf"
valhalla_build_tiles -c /work/build/build.json "$pbf"
valhalla_build_extract -c /work/build/build.json -v
# The servers copy the tiles to /data; their config names that path.
valhalla_build_config \
  --mjolnir-tile-dir /data/tiles \
  --mjolnir-tile-extract /data/valhalla_tiles.tar > /work/build/valhalla.json

rm -rf "$out.next"
mkdir -p "$out.next"
cp /work/build/valhalla_tiles.tar /work/build/valhalla.json "$out.next/"
mv -T "$out.next" "$out"
rm -rf /work/build "$request"
echo "routing: built $(basename "$out")"
