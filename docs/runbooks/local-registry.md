# Local container registry operations

## Scope and ownership

`registry.rupan.dev` is the only container registry endpoint for the new homelab. Zot runs as a native NixOS service on `nas-01`, stores data in `tank/registry`, and is reverse proxied with host-managed TLS. Attic remains the Nix binary cache. NixOS owns the host service, storage, DNS client configuration, and k3s runtime configuration. Flux owns Kubernetes consumers. CI owns image import and host deployment.

The registry uses explicit imports, not pull-through synchronization. An imported image is addressed by its destination digest before a consumer is changed. Public registries, Helm repositories, Git repositories, ACME, and application egress remain separate dependencies.

## Current baseline

The 2026-09-06 and 2026-09-07 preflight observed:

- `nas-01` at `10.0.30.20`, with `tank` online and 8.92 TiB available.
- The independent XFS backup disk at `/mnt/backup-2tb`, with 123 GiB available. Registry backup deployment must stop if the initial inventory plus retention headroom does not fit.
- Three Ready k3s servers at `10.0.30.11` through `10.0.30.13`, running k3s `v1.35.7+k3s1`.
- No `/etc/rancher/k3s/registries.yaml` on any k3s server.
- No exact public DNS record for `registry.rupan.dev`. The public wildcard currently resolves the name through Cloudflare Tunnel. The declared AdGuard rewrite is therefore required for private clients to reach `10.0.30.20`.
- Attic, GitLab Runner, and NFS active on the NAS. No zot or HTTPS listener was present.

Treat these as timestamped observations. Repeat the preflight before changing a host.

## Required runtime material

Provision these as protected files before the corresponding NixOS activation. Do not commit their contents or place them in the Nix store.

| Host | Path | Required content |
| --- | --- | --- |
| `nas-01` | `/persist/zot/htpasswd` | bcrypt htpasswd entries for active node, importer, maintenance, and publisher identities |
| `nas-01` | `/persist/zot/access-control.json` | generated from the committed lock by `scripts.registry access-control` |
| `nas-01` | `/persist/zot/restic-password` | independently retained restic repository password |
| `nas-01` | `/persist/zot/cloudflare-dns-api-token` | narrowly scoped Cloudflare DNS API token for DNS-01 |
| each k3s node | `/persist/secrets/k3s-registries.yaml` | generated node-only registry authentication and explicit upstream rewrites |
| NAS runner | protected container auth file | importer or project-specific publisher credential, never an administrator credential |

Keep the restic password outside the backup repository as encrypted recovery material. A restic repository cannot recover the password stored only inside itself.

Configure the following protected GitLab variables as file variables:

| Variable | Scope |
| --- | --- |
| `SSH_DEPLOY_KEY`, `HOSTS_KNOWN` | Existing strict host deployment identity and known-hosts data |
| `REGISTRY_HTPASSWD_FILE` | bcrypt entries for every generated ACL identity |
| `REGISTRY_CLOUDFLARE_TOKEN_FILE` | Cloudflare DNS-01 token |
| `REGISTRY_RESTIC_PASSWORD_FILE` | independently retained restic password |
| `REGISTRY_NODE_PASSWORD_FILE` | password for the `node` htpasswd identity |
| `REGISTRY_SOURCE_AUTH_FILE` | OCI auth file for source registries |
| `REGISTRY_IMPORTER_AUTH_FILE` | destination OCI auth file for the ongoing `importer` identity |
| `REGISTRY_MAINTENANCE_AUTH_FILE` | recovery-only destination auth file for the `maintenance` identity |

Generate the policy before creating htpasswd entries:

```sh
just registry-access-control /tmp/zot-access-control.json
jq -r '[.adminPolicy.users[], .repositories[].policies[]?.users[]] | unique[]' \
  /tmp/zot-access-control.json
```

The generated policy identities are `node`, `importer`, `maintenance`, and one
`publisher-<project>` identity for each first-party destination in the lock.
Initially create htpasswd entries only for `node`, `importer`, and
`maintenance`. Add a project publisher's
htpasswd entry and protected producer credential only when that producer is
authorized for cutover. A policy entry without an htpasswd credential is inert.
Zot chooses the longest matching repository path, so every exact application
policy explicitly includes both its publisher and the node reader. The CI
platform job regenerates the same policy from the reviewed lock and installs it
as a runtime file.

The retired migration identity has no policy entry, htpasswd entry, or CI
credential. The old bootstrap import path is removed. Node rollout verifies the
full lock with the read-only node identity before activation. The ongoing
`registry_update_import` job uses the upstream-only `importer` identity.

Zot authorization roles are intentionally separate:

- nodes can read required `apps/**` and `upstream/**` repositories but cannot create, update, or delete;
- a project publisher can create and update only its `apps/<project>` repository;
- the importer can create and update only `upstream/**`;
- the maintenance identity is the only identity allowed to delete or administer.

Verify these boundaries with attempted allowed and denied operations before cutover.

## Non-destructive dataset preparation

Do not run disko against the existing tank. On `nas-01`, first inspect `zpool status tank`, `zfs list tank/registry`, and `findmnt /tank/registry`. If the dataset is absent, the authorized operation is equivalent to:

```sh
sudo zfs create -o mountpoint=legacy tank/registry
sudo install -d -o zot -g zot -m 0750 /tank/registry
sudo mount -t zfs tank/registry /tank/registry
```

If it already exists, verify its pool identity, mountpoint, ownership, and contents instead of recreating it. Service activation must fail when `tank-registry.mount` is unavailable.

## Deployment sequence

1. Re-run `just status cluster`, `just status network`, NAS capacity and mount checks, exact Cloudflare DNS lookup, and the source/rendered/live image inventory.
2. Review the target-specific Nix diff and confirm the dataset operation, credential paths, backup capacity, and rollback generations.
3. Trigger `deploy_registry_platform`. It provisions runtime material, creates or validates `tank/registry`, deploys `nas-01`, and requires the mount plus active zot and nginx units.
4. Trigger `deploy_registry_dns`. Verify `registry.rupan.dev` resolves to `10.0.30.20` from the NAS, runner, all nodes, and an authorized NetBird client. The job also requires a TLS-valid anonymous `/v2/` request to return HTTP 401.
5. Verify anonymous denial, trusted HTTPS hostname validation, UI availability after authentication, zot persistence, mount failure behavior, and node-exporter metrics.
6. Generate the locked copy plan. Import current, bootstrap, and retained rollback images from the NAS runner. Verify destination manifest or index digest equality, platform coverage, and required referrers.
7. Trigger `deploy_registry_node_01` through `_05`. The control-plane jobs are dependency-chained (`_01` → `_02` → `_03`); agent jobs `_04` and `_05` chain from `registry_lock` and `_04`. All use the `homelab-0N-registry` NixOS variants. The first job authenticates as the read-only node identity and verifies the complete lock before any activation. Each generates the protected `registries.yaml` from the same lock.
8. Each node job requires the local config, active k3s, API readiness including etcd, and all nodes Ready before the next job becomes available. Do not delete node image caches.
9. Prove an uncached local pull with a disposable image digest that is already in the lock. Use fresh containerd, kubelet, and zot access logs as evidence.
10. Enable the separately reviewable Flux consumer cutover only after `python -m scripts.registry verify` passes from the node network. Migrate a small stateless workload first, then cohorts, then bootstrap/system images.
11. Disable configured upstream fallback only when every configured source registry and exception is locally complete. The offline policy check must reject any undeclared public image reference.
12. Update producer pipelines so first-party builds publish only to `apps/<project>`. Keep existing external repositories and credentials until every producer and rollback consumer is accounted for.

All registry deployment and mutation jobs are manual. A repository push does not itself authorize triggering them.

## Flux cutover paths

> [!NOTE]
> **Cutover Complete**: All 17 Flux Kustomizations have been migrated to point directly to their canonical base paths (`./gitops/<subsystem>` and `./gitops/clusters/homelab-01`). Pinned `registry.rupan.dev` digests and HelmRelease post-renderers are now standard in the base manifests. The cutover mapping below is retained for audit and provenance reference.

| Cohort | Base path | Former cutover overlay |
| --- | --- | --- |
| Single stateless canary | `./gitops/websites` | `./gitops/registry-cutover/components/websites-canary` |
| Remaining stateless sites | `./gitops/websites` | `./gitops/registry-cutover/components/websites` |
| Automation | `./gitops/automation` | `./gitops/registry-cutover/components/automation` |
| Backups | `./gitops/backups` | `./gitops/registry-cutover/components/backups` |
| Home Assistant | `./gitops/home-assistant` | `./gitops/registry-cutover/components/home-assistant` |
| Cloudflare | `./gitops/cloudflare` | `./gitops/registry-cutover/components/cloudflare` |
| Obsidian | `./gitops/obsidian` | `./gitops/registry-cutover/components/obsidian` |
| Immich | `./gitops/immich` | `./gitops/registry-cutover/components/immich` |
| Observability and registry alerts | `./gitops/observability` | `./gitops/registry-cutover/components/observability` |
| cert-manager | `./gitops/cert-manager` | `./gitops/registry-cutover/components/cert-manager` |
| External Secrets Operator | `./gitops/platform` | `./gitops/registry-cutover/components/platform` |
| NFS provisioner | `./gitops/storage` | `./gitops/registry-cutover/components/storage` |
| Flux system controllers | `./gitops/clusters/homelab-01` | `./gitops/registry-cutover/components/flux-system` |

## Import and update rules

`registry/images.lock.json` is the authoritative mapping for import, policy, update discovery, retention, and node mirror rewrites.

### CI bootstrap image

The pinned `docker.io/nixos/nix` image in `.gitlab-ci.yml` is an explicit
exception: the protected `nas-ci` runner must pull its job image before the job
can authenticate to the private registry. Keep it digest-pinned and scope the
lock exception to `.gitlab-ci.yml`; Kubernetes workloads must use locked local
registry references.

Refresh the sanitized live workload snapshot before evaluating drift. This reads
Pod, Job, and CronJob image specifications/status from the current Kubernetes
context and writes only `registry/observed-images.json`:

```sh
just registry-snapshot-live
just registry-inventory
just registry-check
```

The snapshot command is read-only against the cluster. Review the generated
snapshot diff before updating or resolving the lock.

```sh
python -m scripts.registry inventory --output registry/images.inventory.json
python -m scripts.registry resolve --inventory registry/images.inventory.json --output registry/images.lock.candidate.json
python -m scripts.registry plan --lock registry/images.lock.json
python -m scripts.registry copy --lock registry/images.lock.json --concurrency 3 --image-timeout 900 --report artifacts/registry/copy.json
python -m scripts.registry verify --lock registry/images.lock.json --report artifacts/registry/verify.json
python -m scripts.registry check --lock registry/images.lock.json
```

Resolution is network read-only. Copy is an explicit CI mutation. Verification is remote read-only. None of these commands may log credentials. Update desired state only after the destination digest is verified.

Copy imports are incremental and resumable. Each image is copied in its own bounded
worker, with a per-image timeout and progress messages on stderr. Existing matching
tags and required referrer tags are reused, and concurrency defaults to 3 (valid
values are 1 through 8).

For an upstream update, resolve the upstream tag, verify publisher authenticity according to that image's policy, copy the immutable digest and required referrers, verify the destination, then propose the local digest change. A local tag alone is not update discovery.

The `registry_resolve` CI job produces an inventory, candidate lock, and exact
copy plan without changing the registry. After review, `registry_update_import`
copies and verifies only the candidate's upstream records under the serialized
content lock. Promote
the candidate lock and matching overlay digest changes in a normal reviewed
Git commit only after that job succeeds.

## First-party promotion

First-party sites (`0.0.N` tags, plus `ism` on `latest`) roll out without a
manual pin. The scheduled `registry_promote_first_party` CI job lists producer
tags and, when the producer has already published the candidate to
`registry.rupan.dev/apps/<project>`, commits the lock, inventory, and consumer
digest updates together so `check-registry` stays green. Promotion is
verification-only: it never mirrors content, because only `publisher-<project>`
holds a write grant on its destination repository. Candidates the producer has
not published are reported as skipped.

The local registry is the source of truth for first-party images. Lock records
point at `registry.rupan.dev/apps/<project>` and GHCR stays a backup push from
the producer pipelines, not a promotion dependency. Promotion migrates
remaining externally-pointed records to the local registry once the pinned
digest is verified there. Flux picks the commit up on its normal interval. End
to end, a site push reaches the public site in roughly: site CI plus image
build, then the promotion schedule interval, then Flux reconciliation
(GitRepository `1m`, Kustomizations `10m`).

```bash
python -m scripts.registry promote --lock registry/images.lock.json --inventory registry/images.inventory.json
python -m scripts.registry promote --lock registry/images.lock.json --inventory registry/images.inventory.json --dry-run
python -m scripts.registry promote --lock registry/images.lock.json --inventory registry/images.inventory.json --only apps/rupan-dev
```

The job needs two project settings that live outside this repository: a pipeline
schedule targeting `main` (for example nightly) and a `GITLAB_PUSH_TOKEN`
project access token with `write_repository` scope (masked). Without the token
the job still reports what it would promote but commits nothing.

## Producer migration contract

Producer changes belong in their source repositories and require separate
authorization. For each first-party record in the lock, route the build to a
private runner, build once, authenticate as `publisher-<project>`, push only to
`registry.rupan.dev/apps/<project>`, and emit the destination digest plus source
commit. Reject untrusted pull-request jobs and any attempt to use importer or
maintenance credentials. Update the matching Flux overlay only after remote
verification of that digest.

Known producer checkouts are:

- `/home/rupan/businesses/apolline/apolline-site`
- `/home/rupan/businesses/darkbit/darkbit-site`
- `/home/rupan/businesses/distrojeff/distrojeff-site`
- `/home/rupan/projects/nix-agent`
- `/home/rupan/projects/pulse`
- `/home/rupan/projects/sites/rupanism`
- `/home/rupan/projects/sites/rupan.dev` (`apps/rupan-dev`, publisher `publisher-rupan-dev`)
- `/home/rupan/projects/old/soluble`
- `/home/rupan/projects/spatia`
- `/home/rupan/obsidian`

The legacy `/home/rupan/homelab` GitLab pipeline owns
`apps/homelab-renovate-agent` and `apps/homelab-renovate-dashboard`. No producer
checkout was found for `apps/cr-demo`, `apps/ism`, `apps/majorfinder`, or
`apps/photography`. Treat those four as owner-discovery blockers for future
publishing, while retaining and importing their exact current releases.

## Backup and restore

The `registry-backup` timer writes encrypted restic snapshots to the independent disk and does not prune them. Local sanoid snapshots provide fast rollback but are not the independent backup.

Before accepting backup:

1. Confirm `registry-backup.service` completed and `restic check` passed.
2. Restore the full repository plus `/persist/zot` into an isolated alternate path.
3. Start a separate zot instance against the restored path with isolated credentials and port.
4. Authenticate and pull retained current and rollback digests.
5. Repeat the cold pull while upstream registries are blocked only in the disposable test environment.

Do not disrupt the production router to simulate an outage. Do not enable age-based deletion or untagged cleanup until a tested retention fixture proves deployed digests, rollback digests, indexes, and referrers survive.

## Rollback

Keep the pre-registry NixOS generations and previous external image references until recovery acceptance is complete.

- Consumer failure: revert the Flux cutover commit through Git, reconcile, and verify the retained upstream digest.
- Node runtime failure: activate the recorded previous NixOS generation on only the affected node, then verify etcd and scheduling health before touching another node.
- Registry service failure: stop consumer migration, keep existing Pods running, restore the previous NAS generation, and diagnose against the preserved dataset.
- Corrupt or missing content: do not advance desired state. Re-import from the lock or restore the registry into isolation, then verify the exact digest.

## Nix snapshotter pilot

The nix-snapshotter work is a separate pilot after conventional OCI acceptance. It must first pass a disposable NixOS VM matrix for the exact pinned k3s version, authenticated Attic substitution, file-valued paths, reboot, garbage collection, cache outage, ordinary OCI compatibility, and rollback. Do not use `nix:0` references in the initial pilot. A pilot failure does not roll back or invalidate the completed OCI registry migration.
