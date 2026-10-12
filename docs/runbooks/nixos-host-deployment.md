# NixOS host deployment

Use the fleet deployer for NixOS host changes. A deployment changes shared
infrastructure and requires explicit authorization. Validate the source with
`just check-changed` first, then identify the exact host or host set that the
task authorizes.

## Protected CI deployment

The `deploy-fleet-dry-run` and `deploy-fleet` operations in
`config/ci/operations.json` deploy the full configured fleet. The CI policy
rejects per-run variable overrides, so the `DEPLOY_TARGETS` environment is not
a supported way to scope these operations. Run them only when the full fleet is
within the authorized change scope. The deploy operation requires its matching
dry-run prerequisite and fresh successful validation for the same main commit.
See [CI pipeline operations](ci-pipelines.md) for the operation lifecycle.

## Authorized scoped deployment

The local fleet deployer accepts an explicit target. Run it from the repository
in the pinned development environment:

```sh
nix develop ./flake
just deploy-fleet --dry-run --target homelab-04
just deploy-fleet --target homelab-04
```

These commands show a single-host example. Replace `homelab-04` with the
authorized target. Review the dry-run output and confirm it names only
authorized hosts before running the apply command. Multiple `--target` values
or a comma-separated list are supported. Omitting `--target` deploys the full
configured fleet.

The deployer runs its connectivity and readiness preflight by default. It
cordons and drains k3s nodes, uses `nixos-rebuild dry-activate` in dry-run mode
and `nixos-rebuild switch` for an apply, waits for k3s readiness, then uncordons
the node. Stateful k3s nodes also wait for PostgreSQL readiness. NAS deployment
checks every configured ZFS dataset before rebuilding. Keep these defaults;
do not use `--skip-preflight`, `--skip-drain`, or `--skip-cooldown` unless the
task's owning runbook explicitly requires it.

The deployer stops at the first failed host and attempts to uncordon a node if
it was left cordoned. It does not roll back hosts that already completed before
a later host failed. Follow the affected host or service runbook for recovery.

## Acceptance

After deployment, verify the selected hosts and dependent workloads using
`just status cluster` or the target-specific runbook. For appliances, verify the
service behavior described by that appliance's runbook. Report dry-run,
deployment, live acceptance, and recovery evidence separately. A successful
`nixos-rebuild switch` does not by itself prove the service is healthy.
