# Beets + Lidarr Metadata and Media Management Design

## Context

The media stack is managed in `infrastructure/media` and deployed by Argo CD from Git.
Lidarr already manages music acquisition/import, with shared media mounted from
`media-pvc-hdd` under `/data`.

Goal: add Beets to improve metadata quality and music library management without
coupling Beets runtime to Lidarr pod lifecycle.

## Goals

- Add Beets into the media stack as a scheduled metadata management workflow.
- Reuse the shared music library (`/data/music`) already used by Lidarr.
- Keep state persistent so nightly runs are incremental and predictable.
- Keep failure isolation: Beets failures must not affect Lidarr availability.
- Keep all configuration declarative in Git for Argo CD reconciliation.

## Non-Goals

- Replacing Lidarr import/download automation.
- Building event-driven per-import hooks in this change.
- Reorganizing the wider media directory structure beyond Beets config rules.

## Options Considered

1. **Nightly CronJob (chosen)**
   - Pros: operationally clean, decoupled from Lidarr, easy to tune schedule,
     resilient retries/history controls.
   - Cons: a few extra Kubernetes objects.

2. **Sidecar/second container in Lidarr deployment**
   - Pros: fewer objects.
   - Cons: tightly coupled lifecycle, harder troubleshooting, harder upgrades.

3. **Manual Job only**
   - Pros: simplest.
   - Cons: no consistent metadata upkeep unless manually triggered frequently.

## Chosen Design

Create a standalone Beets manifest (`infrastructure/media/beets.yaml`) with:

- `ConfigMap` containing `config.yaml` for Beets settings.
- Small Beets state PVC (database/cache/state).
- `CronJob` (`beets-nightly`) in namespace `media` that runs nightly.

The job mounts:

- `media-pvc-hdd` at `/data` (read/write to `music` tree as configured).
- Beets config at `/config/config.yaml`.
- Beets state PVC at `/beets`.

Runtime and permission alignment:

- `PUID=1000`, `PGID=1000`, `TZ=America/Chicago`.
- Pod/container security context aligned with existing media workloads.

## Data Flow

1. Argo CD sync applies `beets.yaml`.
2. Nightly schedule triggers a Kubernetes Job from `beets-nightly`.
3. Beets loads config and persistent state, then processes `/data/music`.
4. Metadata/tag/path updates are applied in place.
5. Job exits; status/logs retained per history limits for troubleshooting.

## Reliability and Failure Handling

- `concurrencyPolicy: Forbid` to prevent overlapping executions.
- `restartPolicy: OnFailure` and bounded retries (`backoffLimit`).
- `activeDeadlineSeconds` to prevent hung jobs.
- Low `successfulJobsHistoryLimit` and `failedJobsHistoryLimit` to avoid
  buildup.
- Failures are visible via Job status and pod logs; Lidarr stays unaffected.

## GitOps and Argo CD Notes

- Changes are committed to Git; Argo CD reconciles cluster state automatically.
- Drift from manual cluster edits will be corrected on sync.
- Rollback is handled by reverting Git commits and allowing Argo CD to reconcile.

## Validation Plan

1. First deploy with CronJob suspended or manually trigger one smoke run.
2. Confirm Beets can read/write `/data/music` and use persistent state.
3. Confirm Lidarr still reads the same library paths after Beets run.
4. Review Job logs for matcher/tag errors and run a second execution to confirm
   incremental behavior.

## Implementation Scope

- Add new file: `infrastructure/media/beets.yaml`.
- No required changes to `infrastructure/media/lidarr.yaml` for initial rollout.
- Optional follow-up: tune Beets config rules after first production runs.
