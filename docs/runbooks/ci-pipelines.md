# CI pipelines

Forgejo is primary; Woodpecker runs on homelab-04. This describes the migration
branch implementation. Live acceptance is tracked in
[Forgejo disaster recovery](forgejo-disaster-recovery.md).

## Execution and authority

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

Push, PR, tag, manual, and cron events run validation-v2: dependency
provisioning, formatting, the production Jev decision fixture gate, and
the full offline gate. Provisioning runs first (the OpenTofu gate fails
when locked providers are absent); formatting, the Jev gate, and the
full offline gate then run concurrently in the same container, and the
step fails when any of them fails. Each lane is timed and a
slowest-first summary prints at the end of the step without affecting
the result. All pipelines resolve the dev shell
and build outputs from the local Attic substituter
(http://10.0.30.20:8080/homelab), so nix develop fetches prebuilt
paths instead of downloading or rebuilding them per run;
just cache-populate and the cache-publish operation keep the dev shells
in that cache. The required Forgejo status is ci/woodpecker/validation-v2.
Validation has no secrets.
PRs never receive production workflows, regardless of target branch or YAML.

A current main push additionally runs opnsense-plan, cloudflare-plan, and
sync-to-github and cache-publish after validation. The maintenance cron runs registry drift,
retention reconciliation, and first-party promotion. Promotion creates a PR.
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

Agents open and merge Forgejo PRs with the `tea` CLI (in the dev shell) as a
dedicated machine user, never as your personal account and never with a token
committed to the repo. Branch protection (`config/ci/forgejo-protection.json`)
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
4. Optional, to push branches as the machine user instead of a personal
   account: generate a dedicated key per agent machine
   (`ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_homelab_agent -N ''`),
   register its public key on the `homelab-agent` account in the Forgejo UI
   (Settings, SSH Keys; the API requires `write:user`, which the agent token
   deliberately lacks), and add a `git-agent` Host alias in `~/.ssh/config`
   with `IdentitiesOnly yes`. Push with
   `git push git-agent:JEFF7712/homelab.git <branch>`. Without this step,
   `git push origin` authenticates with the machine's default Forgejo key.

Agent flow per change:

```sh
git push origin <branch>
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
