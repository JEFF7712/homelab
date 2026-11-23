#!/bin/sh
# qBittorrent "run on torrent finished" hook: $1 = category, $2 = content path.
# Copies one format of each finished books torrent into the handoff folder;
# the calibre-web pod moves it into Calibre's ingest folder (see
# ingest-mover.sh). The torrent keeps seeding from its own save path.
set -eu
[ "${1:-}" = books ] || exit 0
src=${2:?content path}
handoff=${BOOKS_HANDOFF_DIR:-/data/books/handoff}
mkdir -p "$handoff"
# One record per book in Calibre: prefer EPUB, else AZW3, else MOBI.
for ext in epub azw3 mobi; do
  list=$(find "$src" -type f -iname "*.$ext")
  [ -n "$list" ] || continue
  printf '%s\n' "$list" | while IFS= read -r file; do
    name=$(basename "$file")
    [ -e "$handoff/$name" ] && continue
    # Hidden until complete: ingest-mover.sh skips dotfiles.
    cp "$file" "$handoff/.$name.part"
    mv "$handoff/.$name.part" "$handoff/$name"
  done
  exit 0
done
