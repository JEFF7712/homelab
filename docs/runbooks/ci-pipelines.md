# CI pipelines

Forgejo is primary and Woodpecker runs on homelab-04. GitLab is a mirror kept
for disaster recovery only; see
[Forgejo disaster recovery](forgejo-disaster-recovery.md).

## Execution and authority

The nixos-config GitHub mirror uses hosted runners for PR validation. Its
homelab-04 runners are reserved for the `build-toplevel.yml` and `build-iso.yml`
artifact workflows on `refs/heads/main`. The immutable
`ACTIONS_RUNNER_HOOK_JOB_STARTED` script from
`flake/modules/github-runner-nixos/job-guard.sh` rejects every other event,
repository, ref, or workflow before job steps execute. This restriction belongs
to the runner configuration because a fork can change workflow `runs-on` labels.
Deploy `flake/modules/github-runner-nixos.nix` to homelab-04 before relying on
the restriction. Its offline behavior tests are
`tests/test_github_runner_nixos_guard.py`.

All agents use Docker. Sandbox jobs receive no production credentials,
host mounts, Docker socket, privileged mode, or host Nix trust. Containers
have an 8 GiB memory limit and two CPU quota. The Docker daemon uses ci.slice.
Validation runs in the slim `ci` dev shell; protected operations run in
the `ops` shell, which adds registry, cache, and release clients but leaves
out the virtualization and GUI closure of the default shell (2.4 GiB instead
of 3.9 GiB), so an operation container copies far less from Attic before it
starts. `cache-publish` pushes all three shells. The three homelab-04 agents
each have two workflow slots. An additional sandbox-only agent on homelab-05
has one slot, bringing sandbox capacity to three. Trusted and deployment
concurrency stay unchanged. Its server-side filter enforces `tier=sandbox`.
The new host CI slice is limited to three CPUs and 10 GiB, with that budget
reserved from the kubelet alongside the Omarchy VM. Jobs retain the two-CPU
and 8 GiB container limits.

Job Nix builds as the container's root (`build-users-group =` in the
server-owned `NIX_CONFIG`). The `nixos/nix` image's `nixbld` users carry the
host's build UIDs (30001+) and Docker does not remap them, so a host
nix-daemon build, such as a fleet dry-run's `--build-host` on homelab-04 or
homelab-05, killed every job builder holding the same UID: the job failed
with `builder failed due to signal 9 (Killed)` and no OOM recorded anywhere.
Without a sandbox the build users isolated nothing.

The remote agent uses a dedicated SSH tunnel with a pinned homelab-04 host
key. Its account can forward only to `127.0.0.1:9000`, cannot open a shell
or remote listener, and has no sudo access. The server's gRPC listener
remains on loopback. The agent token and tunnel key are encrypted in
`secrets/hosts/homelab-05.sops.yaml`; jobs cannot access those files, the
Docker socket, or the host tunnel. Both CI hosts block container NFS egress.

Operations serialize on the resource
they act on: the catalog `group` becomes the concurrency group
`resource-<group>` with limit one, so two OpenTofu applies on the same stack
never overlap while a cache push and a Home Assistant apply can. Scheduled
read-only work shares the single `scheduled-read-only` group, so maintenance
holds at most one trusted slot and never queues an operator's plan behind it.

The immutable server configuration extension verifies the signed request,
repository, event, validated main ancestry and operation inputs, deployment identity, and successful
validation parent. Repository YAML cannot select an executor, command, secret,
or privileged container. Policy failures return 403 and fail the pipeline.
Repository settings must clear extension overrides and all trusted permissions.

The patched Woodpecker server records the deployment operator before policy
evaluation and rejects restart query variable overrides. Retain the patch
during upgrades. Deployment rights include the enrolled `homelab-agent` identity; task delivery needs no separate operator approval.

## Validation and operations

Validation runs across push, PR, tag, manual, and cron events. The required
Forgejo commit status context is `ci/woodpecker/validation-v2`. Validation has no
secrets. PRs never receive production workflows, regardless of target branch or YAML.

### Event deduplication and status preservation

Forgejo triggers both branch push and pull request webhooks for PR commits. To
prevent duplicate execution and runner queue saturation:
- When a PR event arrives for a commit that has already been validated (or is
  actively validating) by an authorized pipeline on `main` within 24 hours (or
  actively pending/running within 2 hours), the policy extension emits
  `pr-validation-reused.yaml` instead of generating a second `validation-v2`.
- Emitting `pr-validation-reused` executes a lightweight step that reports
  `ci/woodpecker/pr-validation-reused` without overwriting the required
  `ci/woodpecker/validation-v2` status on Forgejo with `pending`. The PR merge
  check remains green immediately upon push pipeline completion.
- Deduplication invalidates and runs fresh `validation-v2` if:
  - The PR originates from a fork (`from_fork: true`).
  - The PR base branch is not `main`.
  - The PR head commit does not match the pipeline commit (e.g. force-push).
  - The PR is closed.
  - The previous validation failed, expired (>24h), or was created under an
    earlier policy service version.

### Safe change-based scoping

Validation steps invoke `python -m scripts.ci.validate repository`:
- Path classification is handled by `scripts/ci/scope.py`.
- **Changed paths of a branch push** come from the policy service, which keeps
  a blobless mirror (`/var/lib/woodpecker-policy/homelab.git` on homelab-04),
  diffs the pushed commit against its merge base with main, and passes the
  list as `CI_SCOPE_FILES`. The validation clone is shallow and has no
  credentials, so it cannot compute that range itself: without the list a
  new branch has no before commit and a branch updated from main reports
  main's changes as its own, and both run the full gate. A failed or slow
  mirror fetch (20 second bound) omits the list and falls back to Woodpecker's
  `CI_PIPELINE_FILES`, then to the full gate.
- **Documentation changes**: When a change modifies only documentation and
  markdown files, only docs and whitespace checks run, completing in ~2 seconds.
- **Application changes**: When changes are confined to allowlisted application
  areas (e.g. Home Assistant configurations, automations, custom components),
  repository validation runs with `SKIP_FLAKE_CHECK=1`. Linters, type checks, and
  unit tests run in ~15 seconds while avoiding full fleet evaluation.
- **Scoped changes**: When every changed path maps to a narrow check, only those
  checks run: Python sources and tests (`python.sh`, with only the changed
  `tests.test_*` modules when tests are what changed), `gitops/` and `schemas/`
  (`gitops.sh`), `tofu/` (`tofu.sh` after provider provisioning), `registry/`
  and registry code (`registry.sh`), agent tooling (`agent-workflows.sh`), and
  single-host Nix changes under `flake/hosts/<host>/` (`nix.sh <host>`). Mixed
  narrow changes run the union of their checks. Validation infrastructure
  (`scripts/ci/`, `scripts/checks/`, CI config, flake inputs), shared Nix
  modules, and any unmapped path still force the full gate.
- **Main pushes** use the policy mirror's complete first-parent Git diff with
  rename detection disabled, preserving both sides of moved files. The same
  scoped classification applies as on branches. API display-list truncation
  cannot omit checks; missing mirror evidence falls back to full validation.
- **Full offline gate**: Periodic cron runs, release tags, and
  any changes touching shared Nix files or infrastructure paths unconditionally run the
  full offline gate (`just check`, whose flake lane is `scripts/checks/flake.sh`).

The full gate avoids repeating work its own lanes already cover: the parallel
`fmt` lane owns format and YAML lint (the gate runs with `CI_LINT_EXTERNAL=1`,
and `just fmt-check` includes `yamllint gitops home-assistant`), the secret
scan uses the incremental range scan in `scripts/checks/secrets.sh` instead of
rescanning all history (full scans remain for schedules, tags, and local runs),
and when the flake lane will run, `repository-contract` executes the full
unit suite in the Nix sandbox so the local Python lane shrinks to the
Nix-integration subset rather than running the suite twice.

The flake lane evaluates every check and every host configuration with
`nix-eval-jobs` (two workers, each restarted past 2048 MiB) instead of
`nix flake check`, then builds only the checks from the evaluated
derivations. Plain k3s hosts that have a `-registry` variant are skipped,
because evaluating the variant evaluates the plain host as well. Each check
starts building as soon as it is evaluated (`scripts/checks/flake_jobs.py`),
so the unit-suite build overlaps the host evaluations. Measured at two CPUs,
evaluation takes about 48 s and peaks near 3.7 GiB; with the unit-suite
build (about 0.5 GiB) and the other lanes (about 1.6 GiB) that leaves about
2 GiB under the 8 GiB job limit, less while an uncached VM test builds. A
single `nix flake check` evaluator held all thirteen host
configurations at about 5.5 GiB, and with the unit-suite build and the other
lanes it crossed the 8 GiB job limit and killed `repository-contract` with
signal 9. `CI_EVAL_WORKERS` and `CI_EVAL_MAX_MEMORY_MB` trade memory for
evaluation time.

The flake lane uses the Git-filtered `./flake` reference, matching
`cache.sh` and `just cache-populate`. Keep these source identities aligned:
`path:.?dir=flake` includes `.git` and ignored job artifacts in the source
of `repository-contract`, producing different derivations even for the same
tracked files. A cached result under the Git reference cannot substitute
that path-based derivation. Git metadata and ignored artifacts must not
invalidate a check, while changes to tracked source must still do so.
See the [revalidation cache investigation](../research/2026-10-27-ci-revalidation-cache.md)
for the measured baseline and reproduction.

Validation runs in the slim `devShells.ci` shell (`nix develop ./flake#ci`,
about half the closure of `devShells.default`, which remains for interactive
and deployment work); `scripts/ci/cache.sh` and `just cache-populate` publish
both shells to Attic. Inside the gate, `scripts/checks/all.sh` runs its
independent lanes (Python, registry, Nix, GitOps, OpenTofu, Home Assistant,
docs, secrets, flake) concurrently with per-lane buffered output printed in
lane order, aggregating every lane failure instead of stopping at the first;
`CHECK_SERIAL=1` restores sequential execution. Locally the Python suite
dominates, so concurrency is roughly wall-neutral there, but no lane waits on
an unrelated slow lane in CI. OpenTofu provisioning
(`just provision-check-deps`) exports `TF_PLUGIN_CACHE_DIR`
(`/tmp/tofu-plugin-cache` in CI) so repeat provider
installs reuse the shared plugin cache instead of re-downloading.

VM checks use `flake/tests/vm-host-pkgs.nix` to run virtiofsd with
`--inode-file-handles=never`. The default mode requests CAP_DAC_READ_SEARCH
when running as root, which ordinary Docker jobs lack. File descriptors keep
the same VM shares and assertions working without adding container capabilities.
The local and CI gates build the same checks.

Root CI builds also use `nix/kernel-modules-overlay.nix` for module aggregation,
applied to fleet hosts and every VM guest by `flake/flake.nix`. The pinned
[Nixpkgs aggregator](https://github.com/NixOS/nixpkgs/blob/09063bd1f2b36fd9fd793c36dfade7bd40a93dfc/pkgs/os-specific/linux/kmod/aggregator.nix)
uses a writable-directory test before running depmod and merging certificates.
For a single module source, buildEnv can link the output to the kernel's
read-only module tree. Root considers that input writable, enters it and
fails copying `modules.cer` onto itself. This stops cache publication and
can also fail an uncached Zot VM check.

The overlay replaces that permission assumption with a resolved-path check:
regeneration happens only inside the aggregation output. Linked input trees
are preserved; merged output-owned trees still regenerate dependency maps
and combine certificates. The overlay asserts the pinned upstream guard is
still present so a Nixpkgs upgrade prompts review of the workaround.
`checks.kernel-modules-aggregation` reproduces the old failure using a
writable input, verifies input bytes stay unchanged with the fix, and tests
merged certificates, empty module trees and inconsistent-version rejection.

### Dependency caching and timing visibility

Attic stores its chunks on `zroot/attic` (`/srv/attic`), the system SSD
mirror on nas-01, with a 120G quota. It moved off the single `tank` HDD on
2026-10-19: CI containers fetch shells as many small random reads, and on a
disk shared with media and NFS that held IO pressure above 80% and made
every cold step crawl. `tank/attic` keeps the pre-move copy for rollback;
remove it once the SSD store has run cleanly for a while.

### Fleet cache builder

`scripts/ci/cache.sh` uses the native builder by default for homelab, laptop
and ISO targets. The [matched CI benchmark](../research/2026-10-23-nix-fast-build-benchmark.md)
found no consistent complete-job benefit from enabling nix-fast-build for
the homelab cache lane. The full gate's bounded `nix-eval-jobs` flake lane
remains a separate validation gate.

Set `HOMELAB_CACHE_BUILDER=fast` to opt into nix-fast-build from the pinned
nixpkgs in the `ops` or `default` development shell. Its selection is
generated from the native builder's same 23 homelab targets: seven host
systems, all thirteen checks and three development shells. `just cache-populate`
publishes the same target set, including the Zot service VM check. The fast
builder uses two evaluation workers, a 2048 MiB memory budget per worker and
two concurrent build jobs.
The initial 1536 MiB budget caused worker restarts; the matched CI runs also
observed worker retries at the 4096 MiB combined budget in two fast runs.
The worker budget does not cap total process memory. The CI container's
8 GiB limit applies to evaluation and builds together.

Fast builds retain output GC roots until publication completes. The result
report must cover every requested target without failures or skipped jobs;
its output and derivation paths feed the existing source/build-input cache
publication and per-path upload retries. Missing tooling or a failed fast
build fails the job without silently retrying through another builder.

Set `HOMELAB_CACHE_BUILDER=native` to explicitly select the default builder.
Each invocation logs the backend, target count and build elapsed seconds.
Compare the same source revision and cache state, and distinguish evaluation
timing from complete build/upload timing. The fast tool's wrapper lives in
the operations tools, keeping its Python dependencies out of repository
Python and its closure out of ordinary validation shells.

All pipelines resolve the dev shell and build outputs from the local Attic
substituter (`http://10.0.30.20:8080/homelab?priority=30`). Setting priority 30
ensures the local cache takes precedence over `cache.nixos.org` (priority 40).
`just cache-populate` and `scripts/ci/cache.sh` use `--ignore-upstream-cache-filter --jobs 8`
to ensure all devShell dependencies are cached locally. `cache.sh` pushes in
batches of 200 and retries a failed batch path by path, so one NAR Attic rejects
("Bad NAR Hash or Size" on `python3-3.14.8` after the 2026-10-21 nixpkgs bump)
is reported and skipped instead of aborting the whole upload. Push a rejected
path from another machine to unblock it; the job fails only when nothing uploads.

Timing visibility is provided by `scripts.ci.run` and `scripts.ci.timings_report`:
- `[PIPELINE AGE]` measures creation to the first timed command, including
  queueing, earlier steps and shell bootstrap. Use pipeline creation and
  workflow start timestamps from the API for root-workflow queue time;
  dependent workflows also require their prerequisites' finish timestamps.
- Individual lanes (`fmt`, `jev`, `gate`) and every full-gate check report
  execution times under `[EXECUTION BREAKDOWN]`. Cgroup memory peaks,
  OOM-kill deltas and CPU-throttling deltas distinguish resource limits
  from slow checks. Concurrent checks share counters, so their values
  overlap and must not be added together. SIGKILL alone does not establish
  OOM; missing cgroup files mean unavailable evidence.
- Flake builds also report `flake-repository-contract`, `flake-topology`,
  and the other check names in the same breakdown. Each measures the Nix
  invocation, including substitution and dependencies, rather than only
  the check's test process. They overlap host evaluation and sit inside
  `check-flake`; do not add their times to the parent lane. A quick invocation
  may indicate a cache hit; confirm substitution versus a build in the Nix
  log. Source changes still require a fresh contract build, and publication
  must succeed for subsequent disposable containers to reuse its output.

The server wraps the pinned default clone plugin with at most three
attempts, 60 seconds per attempt plus a five-second kill grace, and five-
then ten-second backoff. Only transient connection errors, HTTP 429 and
selected HTTP 5xx responses retry. Credential rejection, missing content
and certificate validation failures fail immediately. Retries retain the
dedicated read-only clone identity and TLS verification and do not rerun
release or deployment commands.

A current main push additionally runs opnsense-plan and cloudflare-plan
after validation, the plans that apply operations consume. GitHub sync and
cache publication run on the maintenance schedule instead, so a merge's
pipeline, which deployment operations require to have succeeded, ends a
minute or two after validation. A push that changes
registry/images.lock.json or registry/images.inventory.json also runs
registry-lock-import after validation, so a reviewed lock entry is copied
into the registry by the same pipeline that lands it; when the push diff
cannot be read, the import runs anyway.

Cron schedules are owned by `config/ci/woodpecker-crons.json`, repository
settings by `config/ci/woodpecker-repository.json`, and the operations each
schedule runs by `SCHEDULES` in `scripts/ci/policy.py`. Reconcile the live
server with `just woodpecker-settings plan` and `just woodpecker-settings apply`.
`maintenance` runs twice an hour and carries the short work: webhook
watchdog, registry lock import, drift, retention, resolve, auth consistency,
GitHub sync, and cache publication. `promote` runs every 15 minutes with
first-party and nix-agent promotion only; it is the application release
path. Promotion opens a PR and, when the diff only moves first-party digests
(`CI_AUTO_MERGE_PROMOTION=first-party`), merges it after required validation.
`nightly`
runs the slow proofs once a day: the GC fixture, OPNsense plan, dataplane
and inventory, NAS proof, and the fleet, agent-workspace, and Home Assistant
dry runs, plus `nixos-deploy`. `weekly` runs `nixos-lock-update` and the
`registry-cve-audit` trivy scan of every locked image; both
laptop operations are described in [NixOS artifact
publication](nixos-publication.md). A schedule must finish well inside its interval; before the split,
the 30 minute schedule took about 100 minutes, queued behind itself, and every
queued operation then failed the current-main check.

A schedule run reuses main validation when a push, manual, or cron pipeline
already passed validation-v2 for the same commit within 24 hours, so it
spends no sandbox time revalidating an unchanged main. While main's own
validation of the commit is still running, a schedule runs only its ungated
watchdog and defers the rest to its next run rather than starting a second
full validation beside it. When the previous run
of the same schedule (Woodpecker records the cron name as the sender) is
still pending or running, the new run emits only a failing
`schedule-overlap` workflow: the schedule outlives its interval and needs
attention, and the repository monitor alerts on it.

A pull request from a branch of this repository never validates in its own
pipeline: Forgejo also sends the branch push, which reports the same
required status on the same commit, so the PR pipeline only records
`pr-validation-reused`. Fork PRs validate in the PR pipeline.

Woodpecker cancels a superseded pending or running pipeline for the same
branch on push and pull request events. Cron is excluded because Woodpecker
treats every schedule on main as the same branch and would cancel one
schedule with another. Because any new push pipeline on main cancels the
running one, the policy rejects the config (HTTP 409) for a main push whose
commit is no longer the tip: restarting an older main pipeline fails
instead of killing the current main's run, and a late webhook for a
superseded merge ends in error without starting. Restart the current
main's pipeline instead. An automatic push or cron
operation that starts after main has moved exits successfully with a skip
message, since the newer main runs the same work; operator-requested
deployments still fail when their commit is no longer main.

The maintenance schedule runs the webhook watchdog ungated: it fails when a recent
push or pull request has no validation status past the grace period or a
pending validation past the stuck threshold, which is how a broken
Forgejo-to-Woodpecker delivery path shows up (Forgejo does not retry).
Branch protection applies: no direct pushes to main; merges require the
green validation-v2 status. The external Obsidian publisher no longer writes
homelab main; it publishes images only and the maintenance promotion PR
carries the digest bump.

Attic optimizes its SQLite query-planner statistics before startup. Missing
statistics can make chunk hash lookups scan the valid-chunk index, stalling
uploads as the cache grows. The bounded PRAGMA optimize pass preserves cache
objects and lets the server load the appropriate hash-index query plan.

config/ci/operations.json defines all manual deployment targets. Select its
key as deploy_to when restarting successful current-main push or manual
validation. The catalog covers registry operations, publisher provisioning,
registry platform and nodes, DNS, Zigbee, LedFx, NAS proof, OPNsense,
Cloudflare, fleet, agent workspaces, Home Assistant, GitHub mirroring and cache.

`matrix-publish` builds and verifies the LED matrix image with its scoped
publisher and produces a reviewable Flux promotion patch. Provision its protected
credential and activate the immutable catalog before use. See
[Matrix releases](matrix-release.md).

The custom OPNsense agent ACL is reconciled by `opnsense-apply`. It plans
file and cache drift before firewall mutation, then converges and verifies
the ACL through a dedicated `opnsense_acl_ssh_key` file secret available only
to that protected deployment operation. The router host key is pinned in
source. Provision the SSH credential and activate the updated immutable
Woodpecker catalog before requesting this operation. See
[OPNsense agent access](opnsense-agent-access.md) for provisioning, recovery,
and the distinction between this deployment authority and agent read access.

For NixOS fleet changes, use the `deploy-fleet-dry-run` operation first, then
the dependent `deploy-fleet` operation after the agent checks the dry-run result.
The task request authorizes both operations. They consider the configured fleet
and skip hosts already running their desired closure; the CI policy rejects per-run variable overrides,
so they do not provide host-level target selection. The operation catalog
enforces the prerequisite and consumes fresh validation for the same source
revision. Known disjoint main advances preserve that revision's artifacts. Apply operations expire after 24 hours. For a scoped deployment, use
the fleet deployer's explicit `--target` option only when the task authorizes
local deployment and the [NixOS host deployment runbook](nixos-host-deployment.md)
allows it.

For GitOps workloads, merging the authorized source change lets Flux reconcile
the commit. Verify the relevant Flux Kustomization and the workload behavior
after reconciliation. A successful CI operation or Flux reconciliation alone
does not prove application acceptance.

Validation and artifacts expire after 24 hours. Apply consumes the saved
binary plan for the same SHA and validation parent. Dry-run prerequisites
use completion receipts under that identity. The runner rejects stale main,
missing prerequisites, unsafe artifact paths, and completed operations before
executing commands. File secrets use mode 0600 and temporary storage.

Provider plan identities require read-only access; deploy identities require
service-specific write access. State credentials are separate from Garage.
Woodpecker command-step secrets require empty image filters and are event scoped. Only selected main
push and maintenance workflows receive their required secrets.

## Agent Forgejo access

Agents open Forgejo PRs with the `tea` CLI (in the dev shell) as the dedicated
machine user, never as a personal account and never with a token committed to
the repo. Publishing a branch, opening a PR, and merging it change shared
repository state; each step requires explicit authorization from the task or
operator. A merge can trigger Flux deployment. Branch protection
(`config/ci/forgejo-protection.json`)
applies to the machine user too: no direct pushes to main, merge requires a
green `ci/woodpecker/validation-v2` status, so agents cannot land red code.

One-time setup, by a human in the Forgejo UI:

1. Create user `homelab-agent` and add it as a collaborator with write access
   to `JEFF7712/homelab` only.
2. As that user, generate a token with exactly `read:user`, `read:repository`,
   `write:repository`, `read:issue`, `write:issue` (`read:user` is mandatory:
   the API rejects tokens without it). No admin, user-write, or other
   scopes. When letting `tea login add` mint the token, pass the same list
   via `--scopes`.
3. On each machine agents run on: `tea login add --url http://git.internal:3000`,
   pasting the token when prompted. It lands in `~/.config/tea/config.yml`
   (mode 0600). Never copy that file into the repo or backups.
4. Required for agents that publish branches: generate a dedicated key per
   agent machine
   (`ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_homelab_agent -N ''`),
   register its public key on the `homelab-agent` account in the Forgejo UI
   (Settings, SSH Keys; the API requires `write:user`, which the agent token
   deliberately lacks), and add a `git-agent` Host alias in `~/.ssh/config`
   with `IdentitiesOnly yes`. Push with
   `git push git-agent:JEFF7712/homelab.git <branch>`. Verify the alias with
   `git ls-remote git-agent:JEFF7712/homelab.git HEAD` before the first push.
   Agent branches must use this identity; `origin` may authenticate as another
   Forgejo account.

Agents deliver changes with `just ship` ([ship runbook](ship.md)). The
manual flow per change, after explicit authorization and successful preflight
(see [the agent workflow guide](../agent-workflow.md#publishing-changes)):

```sh
git push git-agent:JEFF7712/homelab.git HEAD
just forgejo-pr "imperative title"   # opens the PR against main
# wait for validation-v2 green in the Forgejo UI, then:
just forgejo-merge <index>           # merges; prefer fast-forward so the
                                     # Forgejo to GitLab mirror stays clean
```

Rotation: revoke the token in the Forgejo UI, generate a replacement with the
same scopes, and re-run `tea login add` on each agent machine. Treat a leaked
token as compromised until revoked; it can push branches and merge PRs.

The deploy policy admits `homelab-agent` for all catalog operations under the
requested task's standing delivery authorization. No protected-source baseline,
operator-only catalog exception, or passkey grant is required. Saved plans are
validated for completeness and risks are reported; the agent must stay within
task scope and establish recovery before intended persistent-data destruction.

Source validation must pass for the operation's exact source SHA. Both policy
and runner reject superseded related inputs, while known disjoint main changes
can preserve an older validated revision. A failed unrelated main operation does
not block deployment; exact-source prerequisites remain mandatory.

Triggering deployment needs the bot's own Woodpecker API token: sign in to
Woodpecker once as `homelab-agent` via Forgejo, create a token from that
profile, and store it as `~/.config/woodpecker/token` (mode 0600) on each
agent machine. Production credentials stay in CI. Before this policy is first
activated, the previous generation can still require its legacy
[passkey approval](approvals.md); `ship` files that transition request.
Never commit tokens.

## Recovery

A Woodpecker server restart can cancel its own deployment workflow through
gRPC while the host switch completes. Before retrying, inspect the running
host generation, service health and node cordon state. If activation succeeded,
finish the remaining targets with the local `just deploy-fleet --target <host>`
path from the exact validated commit. Preserve the required drain and
health checks; a killed CI job does not prove the host switch failed.

Keep a newly registered remote agent disabled until its host credentials,
server filter, SSH tunnel and container limits have been verified. Deploy
homelab-04's tunnel account before expecting the homelab-05 tunnel to connect.
To remove the extra capacity, disable that agent in Woodpecker, let its current
job finish, then disable `homelab.woodpeckerSandbox.enable` through source and
the fleet deployment path. Restore the recorded host generation if activation
degrades the node. Recheck kubelet allocatable resources after either change.

For a stranded pipeline, compare its workflow IDs against the administrator
queue and actual agent execution before canceling that specific pipeline with
the supported `POST /api/repos/1/pipelines/<number>/cancel` endpoint. This
preserves logs. Woodpecker 3.19 can leave old workflow rows marked running
after their parent becomes killed when no agent remains to report completion.
Those rows do not establish slot usage; exclude terminal parents from active
queue and utilization calculations. Do not cancel or restart unrelated work.

GitLab CI is disabled by default. An offline runner or GitLab webhook is not
failover. homelab-04-dr disables Woodpecker agents and enables fresh Docker
GitLab runners. The scheduler must also match the state authority.

Both Flux webhook receivers reference the same GitRepository. Changing
receiver type does not change its source. Use the explicit cutover and
restore procedure in [Forgejo disaster recovery](forgejo-disaster-recovery.md).
