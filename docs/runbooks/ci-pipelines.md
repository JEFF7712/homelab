# CI pipelines

Forgejo is primary; Woodpecker runs on homelab-04. This describes the migration
branch implementation. Live acceptance is tracked in
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

All three agents use Docker. Sandbox jobs receive no production credentials,
host mounts, Docker socket, privileged mode, or host Nix trust. Containers
have an 8 GiB memory limit and two CPU quota. The Docker daemon uses ci.slice.
Sandbox has two workflow slots; trusted and deploy agents have one each.
All operations share production-authority concurrency with limit one.

The immutable server configuration extension verifies the signed request,
repository, event, current main SHA, deployment operator, and successful
validation parent. Repository YAML cannot select an executor, command, secret,
or privileged container. Policy failures return 403 and fail the pipeline.
Repository settings must clear extension overrides and all trusted permissions.

The patched Woodpecker server records the deployment operator before policy
evaluation and rejects restart query variable overrides. Retain the patch
during upgrades. Deployment rights remain restricted to project operators.

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
- **Documentation changes**: When a change modifies only documentation and
  markdown files, only docs and whitespace checks run, completing in ~2 seconds.
- **Application changes**: When changes are confined to allowlisted application
  areas (e.g. Home Assistant configurations, automations, custom components),
  repository validation runs with `SKIP_FLAKE_CHECK=1`. Linters, type checks, and
  unit tests run in ~15 seconds while avoiding full fleet evaluation.
- **Full offline gate**: Main branch pushes, periodic cron runs, release tags, and
  any changes touching Nix files or infrastructure paths unconditionally run the
  full offline gate (`just check` with `nix flake check`).

### Dependency caching and timing visibility

All pipelines resolve the dev shell and build outputs from the local Attic
substituter (`http://10.0.30.20:8080/homelab?priority=30`). Setting priority 30
ensures the local cache takes precedence over `cache.nixos.org` (priority 40).
`just cache-populate` and `scripts/ci/cache.sh` use `--ignore-upstream-cache-filter --jobs 8`
to ensure all devShell dependencies are cached locally.

Timing visibility is provided by `scripts.ci.run` and `scripts.ci.timings_report`:
- Queue wait time is tracked from `CI_PIPELINE_CREATED` and reported separately under
  `[WAIT / QUEUE]`, clearly distinguishing runner queue contention from execution duration.
- Individual lanes (`fmt`, `jev`, `gate`) and substeps (`provision`, `gate-checks`)
  report resource usage and execution times under `[EXECUTION BREAKDOWN]`.

A current main push additionally runs opnsense-plan, cloudflare-plan, and
sync-to-github and cache-publish after validation. A push that changes
registry/images.lock.json or registry/images.inventory.json also runs
registry-lock-import after validation, so a reviewed lock entry is copied
into the registry by the same pipeline that lands it; when the push diff
cannot be read, the import runs anyway. The maintenance cron runs registry
lock import, registry drift,
retention reconciliation, and first-party promotion. Promotion creates a PR.
The cron also runs the webhook watchdog ungated: it fails when a recent
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

The custom OPNsense agent ACL is reconciled by `opnsense-apply`. It plans
file and cache drift before firewall mutation, then converges and verifies
the ACL through a dedicated `opnsense_acl_ssh_key` file secret available only
to that protected deployment operation. The router host key is pinned in
source. Provision the SSH credential and activate the updated immutable
Woodpecker catalog before requesting this operation. See
[OPNsense agent access](opnsense-agent-access.md) for provisioning, recovery,
and the distinction between this deployment authority and agent read access.

For NixOS fleet changes, use the `deploy-fleet-dry-run` operation first, then
the dependent `deploy-fleet` operation only after reviewing the dry-run result
and obtaining explicit deployment authorization. These catalog operations run
the full configured fleet; the CI policy rejects per-run variable overrides,
so they do not provide host-level target selection. The operation catalog
enforces the prerequisite and consumes fresh validation for the same main
revision. Apply operations expire after 24 hours. For a scoped deployment, use
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

Agent flow per change, after explicit authorization and successful preflight
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

The deploy policy (`scripts/ci/policy.py`) admits `homelab-agent` as a
deployment author alongside the human operators. Fresh green main validation
and the operations catalog gates still apply unchanged, so the bot can only
trigger reviewed, validated commits. Triggering a Woodpecker manual deploy
(for example `deploy_to=opnsense-apply`) additionally needs the bot's own
Woodpecker API token: sign in to Woodpecker once as `homelab-agent` via
Forgejo, create a token from that profile, and store it as
`~/.config/woodpecker/token` (mode 0600) on each agent machine. Never commit
either token.

## Recovery

GitLab CI is disabled by default. An offline runner or GitLab webhook is not
failover. homelab-04-dr disables Woodpecker agents and enables fresh Docker
GitLab runners. The scheduler must also match the state authority.

Both Flux webhook receivers reference the same GitRepository. Changing
receiver type does not change its source. Use the explicit cutover and
restore procedure in [Forgejo disaster recovery](forgejo-disaster-recovery.md).
