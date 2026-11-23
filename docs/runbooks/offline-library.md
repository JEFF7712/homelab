# Offline library (Kiwix)

Owner: `gitops/offline/`. Storage: `media2/offline` on nas-01, see
[NFS access](nfs-access.md#offline-archive). Image:
`gitops/offline/registry-sources.yaml`. Tests:
`python -m unittest tests.test_offline_library tests.test_nas_storage`.

Wikipedia, Stack Exchange, DevDocs, LibreTexts, iFixit and similar ZIM
archives are served read-only at <http://kiwix.homelab> (VIP 10.0.40.18). It
is LAN-only plain HTTP on purpose: reading it needs no WAN, no Cloudflare
tunnel and no certificate that could expire during a long outage. Each book
has a stable alias without its date (`/content/wikipedia_en_all_maxi`), so
bookmarks survive updates.

## Layout

`/tank/offline/kiwix/` on nas-01:

| Path | Contents |
| --- | --- |
| `zim/` | Served releases, `<book>_<YYYY-MM>.zim` |
| `library.xml` | What kiwix-serve lists; rebuilt from `library-files.txt` |
| `previous/` | One superseded release per book, for rollback |
| `staging/` | Partial downloads (`.part`), resumed by the next run |
| `fetch-failed.json` | Books whose last fetch failed |

## Updates

The `kiwix-update` CronJob (Sundays 03:00 America/Chicago) does this for
each book in `gitops/offline/kiwix-update/books.json`:

1. `fetch` finds the newest release in the Kiwix OPDS catalog, downloads it
   into `staging/` at up to 20 MiB/s, and checks its size, SHA-256 (from the
   catalog metalink) and ZIM header. Only then does it rename it into `zim/`,
   beside the release it replaces. It refuses a download that would leave
   less than 100 GiB free. A failed book keeps serving its current release.
   Download mirrors sometimes send a short or damaged copy: a download that
   stops short or stalls is resumed (five tries, a minute apart), and one that fails its SHA-256
   has only the 4 MiB pieces whose metalink SHA-1 differs fetched again
   (three rounds) before it is discarded.
2. `library` rebuilds `library.xml` from the newest release of each book and
   replaces it atomically; `--monitorLibrary` reloads it without a restart.
3. `finish` waits until the server lists exactly the library's books, opens
   each one's main page, and runs a search on each indexed book. Failed
   checks are retried until the timeout, because the second replica may
   still be loading the new library. Only then
   does it move superseded releases to `previous/`, deleting the older copy
   there. Any failed fetch or check fails the Job, which raises the
   `JobFailed` alert.

To add or remove a book, edit `books.json` (`name` and optional `flavour`,
as in `<name>_<flavour>_<date>.zim`) and ship. Find names with
`curl -sL 'https://opds.library.kiwix.org/catalog/v2/entries?q=<term>&lang=eng'`.
A removed book's files are left in `zim/` and still served (the fetch log
says "not in books.json"); delete them by hand to reclaim the space.
Gutenberg (`gutenberg_en_all`, about 220 GB) is a natural next addition.

Run an update now (first fill, or after editing the list):

```sh
HOMELAB_AUTHORIZED=1 kubectl -n offline create job --from=cronjob/kiwix-update kiwix-update-manual-$(date +%s)
kubectl -n offline logs -f job/<name> -c fetch
```

A killed or timed-out run resumes its partial downloads on the next run.

## Rollback

If a new release is broken but passed its checksum, `finish` leaves the old
release in `zim/` and fails. To serve the old one again, move the new file
out of `zim/` (for example into `staging/`) and rerun the Job; the library
then lists the newest remaining release. For a release that was already
rotated, move it from `previous/` back into `zim/` and do the same. The
dataset also keeps three daily ZFS snapshots.

## Portable copy

The cluster copy does not help when the NAS, the cluster or power is down.
Keep an independent copy on a laptop or phone: management (10.0.10.0/24)
can mount `nas-01:/tank/offline` read-only over NFS; copy `kiwix/zim/` with
`rsync` and open it with a Kiwix reader. Organic Maps covers offline
navigation on the phone.

## Checks

- `curl -s http://kiwix.homelab/catalog/v2/entries?count=-1 | grep -c '<entry>'`
  from the LAN (the agent VM cannot reach 10.0.40.x; use
  `kubectl -n offline port-forward svc/kiwix 8080:80`).
- `kubectl -n offline get cronjob,job,pods`
- Outage test: with the WAN unplugged, search and open a book; restart a
  kiwix pod and confirm it comes back (its image is in the local registry
  and its data on the NAS).
