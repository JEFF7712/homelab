# Offline library (Kiwix and maps)

Owner: `gitops/offline/`. Storage: `media2/offline` on nas-01, see
[NFS access](nfs-access.md#offline-archive). Image:
`gitops/offline/registry-sources.yaml`. Tests:
`python -m unittest tests.test_offline_library tests.test_offline_geo tests.test_nas_storage`.

Wikipedia, Stack Exchange, DevDocs, LibreTexts, iFixit, Project Gutenberg
and similar ZIM archives are served read-only at <http://kiwix.homelab>, and
a world street map (Protomaps planet, from OpenStreetMap) at
<http://maps.homelab>. Both names point at VIP 10.0.40.18, where the
`offline-web` nginx (`gitops/offline/web.yaml`) serves `maps.homelab` from
the archive and proxies every other name to Kiwix. It is LAN-only plain HTTP
on purpose: reading it needs no WAN, no Cloudflare tunnel and no certificate
that could expire during a long outage. Each book has a stable alias without
its date (`/content/wikipedia_en_all_maxi`), so bookmarks survive updates;
the map keeps its view in the URL (`#zoom/lat/lon`).

Inside the US the map also searches places and addresses (Photon) and plans
driving, cycling and walking routes (Valhalla), both from data in the
archive. Outside the US it is a map only.

## Layout

`/tank/offline/kiwix/` on nas-01:

| Path | Contents |
| --- | --- |
| `zim/` | Served releases, `<book>_<YYYY-MM>.zim` |
| `library.xml` | What kiwix-serve lists; rebuilt from `library-files.txt` |
| `previous/` | One superseded release per book, for rollback |
| `staging/` | Partial downloads (`.part`), resumed by the next run |
| `fetch-failed.json` | Books (and `maps`, `map assets`) whose last fetch failed |

`/tank/offline/maps/` on nas-01:

| Path | Contents |
| --- | --- |
| `planet_<YYYYMMDD>.pmtiles` | World map builds (about 140 GB each) |
| `planet.pmtiles` | Symlink to the served build |
| `assets/` | Viewer scripts, fonts and sprites from `maps.json` |
| `staging/` | Partial map, search and extract downloads |
| `search/photon-<YYYYMMDD>/` | Unpacked Photon US index (about 30 GB) |
| `search/photon-<version>.jar` | Photon server, pinned by SHA-256 in `geo.json` |
| `routing/us-<YYMMDD>/` | Valhalla US tiles (`valhalla_tiles.tar`) and `valhalla.json` |
| `search/current`, `routing/current` | Symlinks to the served data |

### Search and directions

`maps-search` (Photon in a Java image) and `maps-routing` (Valhalla) run one
pod each on homelab-05 (or homelab-04). Each copies its `current` data from
the NAS to node-local NVMe at start, because Photon's index must not run from
spinning disk; that takes a few minutes, and until the first build exists the
pods wait in `Init`. nginx on `offline-web` sends `/api/search`,
`/api/reverse` and `/api/route` to them.

The `geo-update` CronJob (the 1st of each month, 04:00) refreshes them once
the served data is older than `max_age_days` in
`gitops/offline/kiwix-update/geo.json` (90):

1. `fetch` downloads the newest Photon US index from the GraphHopper dump
   server and the newest Geofabrik US extract, both checked against their
   published MD5, and unpacks the index beside the served one.
2. `build` (Valhalla image) builds routing tiles from the extract on
   node-local disk (about an hour, up to 14 GiB RAM) and copies them to
   `routing/`.
3. `finish` points `current` at the new data, restarts both servers through
   the `geo-update` service account (allowed to restart only those two),
   then searches for Chicago and plans a Chicago to Milwaukee route through
   `maps.homelab`. Only then does it delete the old data; if anything fails
   it points `current` back and restarts again.

Run it now (first build, or after a failure):

```sh
HOMELAB_AUTHORIZED=1 kubectl -n offline create job --from=cronjob/geo-update geo-update-manual-$(date +%s)
```

## Updates

The `kiwix-update` CronJob (Sundays 03:00 America/Chicago) does this for
each book in `gitops/offline/kiwix-update/books.json`:

1. `fetch` finds the newest release in the Kiwix OPDS catalog, downloads it
   into `staging/` at up to 20 MiB/s, and checks its size, SHA-256 (from the
   catalog metalink) and ZIM header. Only then does it rename it into `zim/`,
   beside the release it replaces. It refuses a download that would leave
   less than 100 GiB free. A failed book keeps serving its current release.
   Download mirrors sometimes send a short or damaged copy: a download that
   stops short or stalls is resumed (five tries, a minute apart), and one
   that fails its SHA-256 has only the 4 MiB pieces whose metalink SHA-1
   differs fetched again (three rounds) before it is discarded.
   Then, once the served map is older than `max_age_days` in
   `gitops/offline/kiwix-update/maps.json` (90), it downloads the newest
   Protomaps planet build the same way, checks its size, MD5 (from the build
   list) and PMTiles header, and points `planet.pmtiles` at it. It also
   installs the viewer files pinned by SHA-256 in `maps.json` whenever that
   list changes.
2. `library` rebuilds `library.xml` from the newest release of each book and
   replaces it atomically; `--monitorLibrary` reloads it without a restart.
3. `finish` waits until the server lists exactly the library's books, opens
   each one's main page, and runs a search on each indexed book. Failed
   checks are retried until the timeout, because the second replica may
   still be loading the new library. Only then does it move superseded
   releases to `previous/`, deleting the older copy there. It then loads the
   map viewer, a font and the planet header through `offline-web`, and only
   then deletes older planet builds. Any failed fetch or check fails the
   Job, which raises the `JobFailed` alert.

To add or remove a book, edit `books.json` (`name` and optional `flavour`,
as in `<name>_<flavour>_<date>.zim`) and ship. Find names with
`curl -sL 'https://opds.library.kiwix.org/catalog/v2/entries?q=<term>&lang=eng'`.
A removed book's files are left in `zim/` and still served (the fetch log
says "not in books.json"); delete them by hand to reclaim the space.

To update the map viewer, change a version in `maps.json`, replace its
`sha256` with the new file's (`curl -sL <url> | sha256sum`), and ship; the
next run installs the new set. Use releases at least two weeks old.

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
The [doomsday guide](../DOOMSDAY.md) has the full procedure for each case;
in short:
Keep an independent copy on a laptop or phone: management (10.0.10.0/24)
can mount `nas-01:/tank/offline` read-only over NFS; copy `kiwix/zim/` with
`rsync` and open it with a Kiwix reader. `maps/planet.pmtiles` opens in
any PMTiles viewer, but Organic Maps is the better choice on a phone: it
has offline search and routing anywhere it has a downloaded region. Search
and directions here need the cluster; the data (`maps/search`,
`maps/routing`) is on the NAS and is worth copying too.

## Checks

- `curl -s http://kiwix.homelab/catalog/v2/entries?count=-1 | grep -c '<entry>'`
  from the LAN (the agent VM cannot reach 10.0.40.x; use
  `kubectl -n offline port-forward svc/offline-web 8080:80`).
- `curl -s -H 'Host: maps.homelab' -r 0-6 http://<vip or forward>/planet.pmtiles`
  prints `PMTiles`.
- `curl -s -H 'Host: maps.homelab' 'http://<vip or forward>/api/search?q=Chicago&limit=1'`
  returns a feature, and a POST to `/api/route` with two US points returns a
  `trip`.
- `kubectl -n offline get cronjob,job,pods`
- Outage test: with the WAN unplugged, search and open a book and pan the
  map; restart a kiwix and an offline-web pod and confirm they come back
  (their images are in the local registry and their data on the NAS).
