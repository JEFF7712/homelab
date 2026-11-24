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
   `/data/books/handoff` (mode 0777: qBittorrent writes as uid 911, the
   sidecar empties it as uid 1000).
3. The `ingest-mover` sidecar in the calibre-web pod (uid 1000, the same user
   cwa-init gives the ingest folder) renames handed-off files
   into `/data/books/ingest` every minute. The library is NFS: writes from the
   qBittorrent pod never raise the inotify events Calibre's ingest watcher
   needs, but a rename inside the calibre-web pod does.
4. Calibre imports the book into the library; Bookshelf sees it on its next
   folder rescan.

Bookshelf's Completed Download Handling is turned off (Settings → Download
Clients): its import would duplicate Calibre's. Queue entries therefore stay "downloaded"
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
  `No such file or directory: 'calibredb'` (or a download 500 with
  `NoneType + str`) means `calibre_postinstall` could not link Calibre's
  tools into `/usr/bin`; the container needs a writable root filesystem.
- Re-run a hand-off for a finished torrent from the qBittorrent pod, as
  qBittorrent's user so the handoff folder stays writable for the hook:
  `s6-setuidgid abc sh /books-handoff/qbit-finished.sh books "<content path>"`.
- After an NFS outage the calibre-web pod can stick in
  `CreateContainerConfigError: failed to prepare subPath`; delete the pod.

## Weekly Bookshelf maintenance

`bookshelf-missing-search` (`gitops/media/bookshelf-missing-search.yaml`,
script `bookshelf-missing-search/weekly.py`, Sundays 03:47) rescans the
library, removes queue entries whose book Calibre has imported (Bookshelf
only: the torrent keeps seeding, nothing is blocklisted), then searches every
monitored book with no file. It reads only `BOOKSHELF_API_KEY` from
`media-app`. Run it on demand with `kubectl -n media create job
--from=cronjob/bookshelf-missing-search <name>`. Bookshelf's health page
keeps warning "Enable Completed Download Handling"; that is expected.
