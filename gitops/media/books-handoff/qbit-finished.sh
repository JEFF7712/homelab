#!/bin/sh
# qBittorrent "run on torrent finished" hook: $1 = category, $2 = content path.
# Copies one format of each finished books torrent into the handoff folder;
# the calibre-web pod moves it into Calibre's ingest folder (see
# ingest-mover.sh). The torrent keeps seeding from its own save path.
set -eu
[ "${1:-}" = books ] || exit 0
src=${2:?content path}
handoff=${BOOKS_HANDOFF_DIR:-/data/books/handoff}
staging="$handoff/.staging"
mkdir -p "$staging"
# One record per book in Calibre: prefer EPUB, else AZW3, else MOBI.
for ext in epub azw3 mobi; do
  list=$(find "$src" -type f -iname "*.$ext")
  [ -n "$list" ] || continue
  printf '%s\n' "$list" | while IFS= read -r file; do
    name=$(basename "$file")
    [ -e "$handoff/$name" ] && continue
    cp "$file" "$staging/$name"
    mv "$staging/$name" "$handoff/$name"
  done
  exit 0
done
