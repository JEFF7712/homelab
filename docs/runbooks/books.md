# Book downloads (Bookshelf → qBittorrent → Calibre)

Bookshelf (a Readarr fork) tracks authors and grabs releases through
Prowlarr into qBittorrent's `books` category. Calibre-Web Automated
(books.rupan.dev) owns the library at `/data/books/library`; Bookshelf only
scans it to learn which books exist.

## Flow

1. qBittorrent saves `books` torrents to `/data/downloads/books` and keeps
   seeding from there. The `configure-qbittorrent` init container in
   `gitops/media/download.yaml` enforces that save path and the
   run-on-finished hook.
2. On completion the hook (`gitops/media/books-handoff/qbit-finished.sh`)
   copies one format per book (EPUB, else AZW3, else MOBI) into
   `/data/books/handoff`.
3. The `ingest-mover` sidecar in the calibre-web pod renames handed-off files
   into `/data/books/ingest` every minute. The library is NFS: writes from the
   qBittorrent pod never raise the inotify events Calibre's ingest watcher
   needs, but a rename inside the calibre-web pod does.
4. Calibre imports the book into the library; Bookshelf sees it on its next
   folder rescan.

Bookshelf's Completed Download Handling is turned off (Settings → Download
Clients): it runs as uid 1000 and cannot write the 911:1001 library, and an
import would duplicate Calibre's. Queue entries therefore stay "downloaded"
until the rescan finds the file.

## Diagnose

- Stuck in `/data/books/handoff`: `kubectl -n media logs deploy/calibre-web -c ingest-mover`.
- Nothing in handoff after a finished torrent: check the `[AutoRun]` section
  in `/config/qBittorrent/qBittorrent.conf` and the torrent's category.
- Imported books that never appear: CWA moves failures to
  `/config/processed_books/failed` in the calibre-web pod.
- CWA logs "Successfully processed" even when the import failed, and deletes
  the file from ingest either way; the copy in `/data/downloads/books` stays.
  Look for `ran into the following error` above it. `KeyError` means
  `dirs.json` (`gitops/media/configs.yaml`) lacks a key the image reads;
  `No such file or directory: 'calibredb'` means `/app/calibre` fell off the
  container `PATH` (the read-only rootfs blocks CWA's own symlink step).
- Re-run a hand-off for a finished torrent from the qBittorrent pod:
  `sh /books-handoff/qbit-finished.sh books "<content path>"`.
