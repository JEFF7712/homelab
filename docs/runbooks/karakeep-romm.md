# Karakeep and RomM

Flux owns `gitops/karakeep/` and `gitops/romm/`. Upstream images are declared in
those directories' inventory-only `registry-sources.yaml` files, imported through
the registry lane before any workload consumes their local digest.

## Access and first use

- <https://karakeep.rupan.dev>: bookmark capture, browser archiving, full-text
  search, notes and uploaded assets. AI tagging, summaries and embeddings are
  disabled; no paid inference consumer is provisioned.
- <https://romm.rupan.dev>: ROM library management and supported browser emulators.
  Hasheous metadata is enabled without account credentials. ScreenScraper,
  RetroAchievements and SteamGridDB can be configured later with the owner's
  accounts; these are optional and are not fabricated during installation.

Both hostnames inherit the existing owner-email-only wildcard Cloudflare Access
application. They also require application login. Browser extensions and mobile
clients must satisfy Access as well; an app password alone does not bypass it.
DNS CNAMEs point to the homelab tunnel. Never add an Access bypass for these hosts.

Initial application accounts use username `rupan`. Karakeep uses the owner's
email for login. Bootstrap passwords are stored with the application secrets;
read them on your own trusted terminal, then change them in the app:

```sh
kubectl -n karakeep get secret karakeep-secrets -o jsonpath='{.data.BOOTSTRAP_PASSWORD}' | base64 -d
kubectl -n romm get secret romm-secrets -o jsonpath='{.data.BOOTSTRAP_PASSWORD}' | base64 -d
```

Secrets are SOPS-encrypted in `gitops/secrets/{karakeep,romm}.sops.yaml` and
recoverable with the existing Flux/operator identity. Namespace declarations
live in `gitops/secrets/namespaces.yaml` so secret creation precedes app startup.
Do not rotate the authentication/database keys when restoring data.

RomM starts with an empty library. Upload owned/homebrew ROMs in its web UI or
place platform folders under `/romm/library/roms/<platform>/`. The retained
`romm/state` NFS PVC contains library, saves, config and fetched resources.
It is separate from Gameyfin's existing PC game library; neither library is moved.

## Storage and scheduling

`homelab-03` hosts three retained local SSD PVs: Karakeep SQLite (2 GiB),
Meilisearch (2 GiB), and RomM MariaDB (4 GiB). NixOS tmpfiles owns their directories
under `/persist`. PV capacities are scheduling declarations, not filesystem
quotas: monitor the host's free space. Local databases use single replicas and
Recreate deployments. SQLite WAL is enabled only on this local filesystem.

Large Karakeep assets and RomM state use retained NFS PVCs on `nas-01`.
Namespaces and PVCs are protected from Flux pruning; static PVs use Retain.
Removing an application does not authorize deleting its data.

Karakeep Chrome is unprivileged and isolated from other namespaces and private
network destinations. Both namespaces allow public HTTP(S) egress and cluster
DNS. Only their web port is reachable from Cloudflare; database/search/browser
ports have no external service. Application images run without added Linux
capabilities or Kubernetes API tokens. Karakeep's s6 runtime and RomM's nginx
configuration generation require writable container filesystems; state is still
mounted explicitly on persistent volumes.

## Backups and recovery

Nightly at 01:15 America/Chicago, `karakeep-backup` takes an online SQLite backup,
checks its integrity and publishes a tar archive containing `db.db` and `assets/`.
At 01:35, `romm-backup` takes a transactional MariaDB dump, verifies the gzip and
completion footer, then archives it with `assets/` (saves) and `config/`.
Seven days of successful archives are retained. Failed backups do not prune
successful archives. Live file archives do not freeze uploads; avoid deleting
assets while a backup is running.

Both jobs write into the existing shared `backups/db-dumps` NFS directory via
retained static PV aliases. The existing 03:00 `dumps-to-r2` job encrypts and copies
that directory to the existing R2 repository. ROM files and fetched cover art are
excluded from the offsite archive; their local NAS snapshot coverage does not
provide site-loss protection. Keep originals of your ROM library separately.
Secrets recover from encrypted Git, not from the database archive alone.

Run fresh backups and inspect them:

```sh
HOMELAB_AUTHORIZED=1 kubectl -n karakeep create job --from=cronjob/karakeep-backup karakeep-backup-manual
HOMELAB_AUTHORIZED=1 kubectl -n romm create job --from=cronjob/romm-backup romm-backup-manual
kubectl -n karakeep logs job/karakeep-backup-manual
kubectl -n romm logs job/romm-backup-manual -c dump
kubectl -n romm logs job/romm-backup-manual -c assets
```

For recovery, first restore into **new isolated volumes** and verify the archive;
never overwrite the running database as a test. Extract a Karakeep archive,
check `PRAGMA integrity_check` and bookmark counts, and retain both `db.db` and
`assets/`. For RomM, extract `database.sql.gz`, import it into an isolated MariaDB
of the pinned major version, verify users/platforms/ROM counts, and recover
`assets/` and `config/` alongside the separately recovered ROM library. Restore
the original SOPS keys. Only after validating the recovered app should its source
PVC bindings be changed and delivered with `just ship`.

A failed homelab-03 SSD requires this restore to replacement local storage;
these apps do not automatically fail over their databases. Meilisearch is
regenerable using Karakeep's admin reindex action. A failed rollout is recovered
by reverting application manifests through `just ship`; preserve volumes and
check upstream database migration compatibility before downgrading an image.

## Verification

```sh
kubectl -n flux-system get kustomizations karakeep romm
kubectl -n karakeep get deployments,pods,pvc,cronjobs
kubectl -n romm get deployments,pods,pvc,cronjobs
curl -I https://karakeep.rupan.dev
curl -I https://romm.rupan.dev
nix develop ./flake -c python -m unittest tests.test_personal_apps_backup tests.test_cloudflare_tunnel -v
```

Anonymous public requests must redirect to Cloudflare Access. App health is
checked separately through a localhost port-forward to `/api/health` (Karakeep)
and `/api/heartbeat` (RomM). Acceptance also saves and searches a Karakeep page,
uploads a disposable homebrew ROM, runs both backup Jobs and verifies restored
SQLite and MariaDB data without touching the live databases.
