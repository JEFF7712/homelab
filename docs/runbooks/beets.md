# Nightly beets tagging

The `media/beets-nightly` CronJob runs at 03:00 America/Chicago. Its owning
sources are `gitops/media/music.yaml` and the `beets-config` ConfigMap in
`gitops/media/configs.yaml`. Flux deploys both through the media Kustomization.

The importer tags strong MusicBrainz matches in place, fetches artwork and
embeds it. It does not move or copy music. Quiet mode skips uncertain matches;
`incremental_skip_later: true` leaves those albums eligible for a later retry.
The `musicbrainz` plugin must be explicitly enabled with beets 2.13.

`lastgenre` adds up to three whitelisted Last.fm genres to each imported album
that has no genre (`force: false` keeps existing genre tags). Navidrome reads
them from the files, and shared music mode's library lookup uses them to pick
effects and colours (see [shared-music-mode.md](shared-music-mode.md)).

The database, resume/incremental state, append-only import decision log, and
latest completed run summary live on the media-library PVC:

- `/data/.beets/library.db`
- `/data/.beets/state.pickle`
- `/data/.beets/import.log`
- `/data/.beets/last-run.json`

Keep `/config` read-only. State belongs on the writable PVC, and disposable
home/cache files belong in `/tmp`. `concurrencyPolicy: Forbid` serializes
scheduled runs. Jobs have a 20-hour deadline and no automatic retries; the next
night resumes saved progress. Initial population can span several nights.

## Verify

```sh
kubectl -n media get cronjob beets-nightly
kubectl -n media get jobs --sort-by=.metadata.creationTimestamp
kubectl -n media logs job/<job-name> --tail=80
kubectl -n media exec deploy/lidarr -c lidarr -- cat /data/.beets/last-run.json
```

Each run logs `beets_start` and a `beets_summary` with before, after and added
track/album counts. A nonzero importer exit fails the Job, retaining partial
progress. A successful importer that leaves the entire database empty also
fails. Zero additions to an already populated database can be a healthy
incremental run. An interrupted run may leave the previous summary on disk;
check its `finished_at` and the Job status together.

For a bounded acceptance run, create a Job from the reconciled CronJob using
`kubectl create job --from=cronjob/beets-nightly <unique-name> --dry-run=client
-o json`, then set its container command to `python3 /config/nightly.py
/data/music/<artist>/<album>`. Review the generated Job before creating it.
Do not overlap a manual run with another importer. Run the same bounded Job
again with a new name: it should add zero tracks and preserve its saved state.
Direct mutations require `HOMELAB_AUTHORIZED=1`.

For matching diagnostics, use the pinned image with a temporary database and
state file, only the `musicbrainz` plugin, a read-only media mount, and
`beet -vv -c <temporary-config> import --quiet --nowrite --nocopy <album>`.
This tests lookup without changing music or production importer state.

## Recovery

Revert the owning manifest change and ship the revert to restore the prior
CronJob/config. Do not delete the database or state to retry matching. Use
`beet import --from-logfile /data/.beets/import.log` for deliberate review of
skipped albums. Configuration rollback does not undo file tag/artwork changes;
recover those from media backups if necessary.

The failure diagnosed on 2026-11-12 combined an omitted MusicBrainz plugin
(zero matching candidates) with a state file defaulting to read-only
`/config/state.pickle`. Jobs exited successfully after hours of skips while
the database remained empty.
