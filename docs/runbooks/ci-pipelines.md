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

Push, PR, tag, manual, and cron events run validation-v2: dependency provisioning,
formatting, the production Jev decision fixture gate, and the full offline gate. The
required Forgejo status is ci/woodpecker/validation-v2. Validation has no secrets.
PRs never receive production workflows, regardless of target branch or YAML.

A current main push additionally runs opnsense-plan, cloudflare-plan, and
sync-to-github and cache-publish after validation. The maintenance cron runs registry drift,
retention reconciliation, and first-party promotion. Promotion creates a PR.
The external Obsidian publisher must stop writing homelab main before enabling
branch protection.

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
Woodpecker secrets are image restricted and event scoped. Only selected main
push and maintenance workflows receive their required secrets.

## Recovery

GitLab CI is disabled by default. An offline runner or GitLab webhook is not
failover. homelab-04-dr disables Woodpecker agents and enables fresh Docker
GitLab runners. The scheduler must also match the state authority.

Both Flux webhook receivers reference the same GitRepository. Changing
receiver type does not change its source. Use the explicit cutover and
restore procedure in [Forgejo disaster recovery](forgejo-disaster-recovery.md).
