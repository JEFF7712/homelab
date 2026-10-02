# Lidarr queue hygiene

## Why this exists

Music flows into Lidarr from two paths with different metadata sources:

- soularr / spotify-sync request by Spotify identity,
- Arr-Extended downloads from streaming sources,
- Lidarr imports only what it can match against MusicBrainz metadata.

Singles, mixtapes, and regional tracks that MusicBrainz does not know (or
knows with very different track lengths) stall forever as
`completed` / `importFailed` in the queue, with orphaned directories under
`/config/extended/import/`. Observed 2026-10-02: 45 items / 2.0G, all
`importFailed`, newest ~8h old, oldest ~28h old. Typical messages:

- `Couldn't find similar album for [...]` (release unknown to Lidarr)
- `Worst track match: 34.8% vs 60% [track length, track title]` (streaming
  track lengths differ from the MusicBrainz release)

A second, independent failure mode: Lidarr's per-track automatic searches
hammer apibay.org, which answers `429 TooManyRequests`, so Prowlarr backs
The Pirate Bay off for an hour at a time and Lidarr reports
`Indexers unavailable due to failures`. Mitigation applied 2026-10-02:
automatic search disabled for The Pirate Bay in Lidarr (RSS and interactive
search stay on). Note: a Prowlarr full sync may re-enable it; re-check
`enableAutomaticSearch` on the Lidarr indexer if the 429s return.

## What the CronJob does

`arr-queue-hygiene` (nightly 06:15 America/Chicago, manifest
`gitops/media/arr-queue-hygiene.yaml`) purges queue items stuck in
`importFailed` longer than `RETENTION_DAYS` (default 3):

1. Bulk-removes them from the Lidarr queue (`removeFromClient=false`,
   `blocklist=false`, so a later metadata refresh can still import a
   re-grab).
2. Moves their `/config/extended/import/<dir>` folders to a dated
   quarantine directory on the same PVC.
3. Posts a digest to ntfy when `NTFY_URL` / `NTFY_TOPIC` are set.

It never unmonitors artists/albums, so request pressure from soularr is
unchanged; unmatching releases will re-download and re-stall. That is
expected. The permanent fix for a repeat offender is curation: unmonitor
the album in Lidarr, or manual-import it once (see below).

## Operating the queue by hand

Queue status (inside the cluster):

```sh
kubectl exec -n media deploy/lidarr -c lidarr -- sh -c \
  'curl -s -H "X-Api-Key: $LIDARR_API_KEY" \
  http://localhost:8686/api/v1/queue/status'
```

List stuck items with reasons:

```sh
kubectl exec -n media deploy/lidarr -c lidarr -- sh -c \
  'curl -s -H "X-Api-Key: $LIDARR_API_KEY" \
  "http://localhost:8686/api/v1/queue?page=1&pageSize=50" \
  > /tmp/q.json; python3 -c "import json;
  d=json.load(open(\"/tmp/q.json\"));
  [print(r[\"title\"], \"|\", r[\"trackedDownloadState\"]) \
  for r in d[\"records\"]]"'
```

Quarantined downloads live under
`/config/extended/import-quarantine-<date>/` on the `lidarr-config` PVC.
Review with `beet import` from the beets job image, or delete them. Files
are re-downloadable streaming grabs, so deleting is safe.

## Verifying the Pirate Bay indexer

```sh
# indexer id 5 in Prowlarr; expect no recent 429/backoff entry
kubectl exec -n media deploy/prowlarr -c prowlarr -- sh -c \
  'KEY=$(grep -o "<ApiKey>[^<]*" /config/config.xml | cut -d">" -f2); \
  curl -s -H "X-Api-Key: $KEY" \
  http://localhost:9696/api/v1/indexerstatus'
```

Lidarr flags the outage as an `IndexerStatusCheck` health warning:

```sh
kubectl exec -n media deploy/lidarr -c lidarr -- sh -c \
  'curl -s -H "X-Api-Key: $LIDARR_API_KEY" \
  http://localhost:8686/api/v1/health'
```
