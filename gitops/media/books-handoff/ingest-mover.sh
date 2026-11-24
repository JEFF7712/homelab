#!/bin/sh
# calibre-web sidecar: move handed-off books into the ingest folder from
# inside this pod. The media library is NFS, so writes made by the
# qBittorrent pod never raise the inotify events Calibre's ingest watcher
# waits for; a rename made here does.
set -u
handoff=${BOOKS_HANDOFF_DIR:-/books/handoff}
ingest=${BOOKS_INGEST_DIR:-/books/ingest}
interval=${BOOKS_HANDOFF_INTERVAL:-60}
move_ready() {
  for file in "$handoff"/*; do
    [ -f "$file" ] || continue
    name=$(basename "$file")
    if [ -e "$ingest/$name" ]; then
      echo "skip $name: already waiting in ingest"
      continue
    fi
    mv "$file" "$ingest/$name" && echo "handed off $name"
  done
}
if [ "${1:-}" = --once ]; then
  move_ready
  exit 0
fi
while :; do
  move_ready
  sleep "$interval"
done
