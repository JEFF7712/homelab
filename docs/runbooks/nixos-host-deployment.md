# NixOS host deployment

Use the fleet deployer for NixOS host changes. A deployment changes shared
infrastructure under the task’s standing delivery authorization. Validate the source with
`just check-changed` first, then identify the exact host or host set that the
task authorizes.

## Deployment evidence

`just deployment-report` observes the fleet without building, copying store
paths, creating GC roots, activating systems, or changing generation links on
targets. Use the same target selection as the deployer:

```sh
just deployment-report --target homelab-04
just deployment-report --target homelab-04 --json
just deployment-report --target homelab-04 --output-dir artifacts/deployment-report/local
```

The default output is Markdown. `--json` emits schema-versioned JSON on stdout;
`--output-dir` writes both `report.json` and `report.md` on the caller. Repeated
`--target` arguments and comma-separated names are supported; omitting the
option observes the full fleet. `--flake` defaults to `./flake`; `--ssh-user`
defaults to `rupan`. SSH uses `NIX_SSHOPTS` or `SSH_OPTS`, or explicit
`--ssh-opts`, with batch mode and strict host-key checking. `--timeout` bounds
each query (default 120 seconds), not the whole fleet run.

Commit the candidate first. The command resolves a clean Git flake into a
revision-locked reference, records its SHA and source NAR hash, and evaluates
that reference with import-from-derivation disabled. Keeping the Git reference
preserves flake `self.rev` semantics. Evaluation may populate the caller's Nix
store and input cache, but never the target store. Untracked files are excluded
by Git flake semantics; add and commit all intended source files first.

Each host reports its observed running system, generation and profile pointer,
candidate derivation/output, closure sizes and delta, package changes,
build/substitution requirements, and previous retained generation. Kubernetes
hosts use the same `-registry` variants as the deployer. `configurationRevision`
is reported when available; the nixpkgs revision is never substituted for a
missing homelab source revision. The running and profile generations may differ.
If multiple retained generations match the running closure and the profile
points elsewhere, the running generation and its predecessor are unavailable;
the report records the matching labels instead of choosing one.

Measurements carry `confidence: exact|estimated|unavailable` and an explanatory
`reason`. Consumers must inspect these fields, not treat a successful process
exit or an absent number as a zero:

- Closure accounting uses target-side `nix path-info --recursive --json`,
  validates that every reference is included, and checks that every closure
  root exists on disk. Sizes are sums of unique NAR sizes, not physical disk
  usage. Candidate sizes and package comparisons remain unavailable until
  both complete closures are present on that target.
- Package identities/version sets are inferred from store names and marked
  estimated. Exact added/removed store paths are also retained, including
  rebuilds with unchanged version strings. This uses the target's native Nix
  inventory, without installing nvd on targets or copying closures to the
  caller for nvd's local-filesystem checks.
- The native `nix build --dry-run --no-link --json` plan runs on the target as
  the deployment SSH user, only if its candidate derivation already exists.
  Store queries explicitly select the target's local Nix daemon, so inherited
  `NIX_REMOTE` values cannot redirect measurements to another machine.
  Missing derivations produce an unavailable plan; the report never imports
  them. Build paths reflect that target's store and user settings. Nix may use
  configured builders, so these are required builds, not guaranteed local
  execution. Download byte totals are rounded native estimates. Unknown or
  incomplete planner output is unavailable, never an empty successful plan.
- Rollback selects the highest retained generation below the running
  generation. This is an explicit recovery identity, not necessarily the
  profile's implicit rollback when the profile points elsewhere. Availability
  requires a complete registered closure and existing root paths. This does
  not hash all file contents, preserve the closure against future pruning,
  test a boot, or undo state migrations.

Status is `complete` when all principal measurements are available, `partial`
when some evidence is unavailable, and `error` on identity/evaluation failures,
unreachable hosts or changing generation links. Partial reports exit zero;
error reports exit nonzero and must be discarded. A failed host does not hide
other hosts' evidence. Generation links and the running system are sampled
before and after collection. This detects identity changes, but does not lock
the store against concurrent GC or prove a fully atomic observation.

Fleet, workspace-host and CI-host dry-runs capture reports immediately
before and after preparation and dry activation of changed host closures. Reports
are under `artifacts/deployment-report/<operation>/{before,after}/` in the
operation receipt and their Markdown summaries appear in the logs. The first
report can have unavailable candidate measurements; the second can measure
what preparation built/copied. These reports add no authorization or
resource-budget policy. The existing dry-run still builds/copies candidates
and executes dry-activation; only the separate report command is observational.
Catalog updates become active after the CI-host deployment.

## Protected CI deployment

The `deploy-fleet-dry-run` and `deploy-fleet` operations in
`config/ci/operations.json` consider the full configured fleet and skip any
host already running its evaluated desired closure. The CI policy
rejects per-run variable overrides, so the `DEPLOY_TARGETS` environment is not
a supported way to scope these operations. Run them only when the full fleet is
within the authorized change scope. The deploy operation requires its matching
dry-run prerequisite and fresh successful validation for the same source commit.
Known changes to disjoint resources may advance main without invalidating that
source; unknown or related changes require a fresh prerequisite.
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
configured fleet, skipping unchanged live systems.

The deployer runs its connectivity and readiness preflight by default. It
prepares changed closures, then cordons and drains affected k3s nodes. It runs
the prepared closure's `switch-to-configuration dry-activate` in dry-run mode
and `switch-to-configuration switch` for apply, verifies k3s readiness and newly
failed units, then uncordons the node. Stateful k3s nodes also wait for PostgreSQL readiness. NAS deployment
checks every configured ZFS dataset before rebuilding. Keep these defaults;
do not use `--skip-preflight`, `--skip-drain`, or `--skip-cooldown` unless the
task's owning runbook explicitly requires it.

The deployer stops at the first failed host, restores its previous system after
an activation failure, and uncordons only after successful host recovery. A drain
failure also attempts to restore scheduling. It does not roll back hosts that already completed before
a later host failed. Follow the affected host or service runbook for recovery.

## Acceptance

After deployment, verify the selected hosts and dependent workloads using
`just status cluster` or the target-specific runbook. For appliances, verify the
service behavior described by that appliance's runbook. Report dry-run,
deployment, live acceptance, and recovery evidence separately. A successful
`nixos-rebuild switch` does not by itself prove the service is healthy.

## Preparation, verification and recovery

The deployer evaluates the exact `system.build.toplevel.outPath` for each host
(using the registry variant for k3s) and compares it to `/run/current-system`
on that host. It checks configured NAS datasets, then builds changed closures
with the existing remote builder and cache configuration. All selected builds
finish before the first drain. Temporary remote GC roots retain the candidate
and previous closures during delivery and are cleaned up on exit.

Activation sets the system profile and switches to the prepared store path; it
does not reevaluate the source while workloads are drained. Before disruption,
the deployer rejects a host whose live system changed during preparation. It
checks the activated path, newly failed systemd units, node readiness and existing
appliance probes. PostgreSQL rollout checks run after uncordoning its node.
The default extra cooldown is zero; `--cooldown-seconds` can request an additional
delay after health verification.

If activation or verification fails, restore the exact previously running system,
verify it and stop the rollout. A failed recovery keeps a k3s node cordoned when
possible and reports the previous closure for recovery. Configuration rollback
does not reverse database migrations or restore deleted data. The agent must
verify backups before intended data destruction and check the requested live
behavior after infrastructure convergence.
