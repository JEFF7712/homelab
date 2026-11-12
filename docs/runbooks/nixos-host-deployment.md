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

## SSH deployment identities

The common fleet module treats `homelab-nas-deploy` as a root-equivalent legacy
automation identity: `rupan` retains passwordless sudo for system activation.
Its authorized-key entry uses `restrict` (no PTY, forwarding or user startup
scripts) and accepts connections only from the inventory addresses of the NAS
and the trusted CI host, currently `10.0.30.20` and `10.0.30.14`. A stolen copy
on another infrastructure machine cannot authenticate with this key. The key
still executes noninteractive deployment commands from the trusted sources;
this is source containment, not a forced-command deployment interface.

The operator's two original keys and the personal `developer@agent-rupan`
workstation key remain unrestricted. The development VM intentionally has the
same operator authority as the laptop; see [agent workspaces](agent-workspaces.md).
Do not revoke these operator identities to restrict an automation workflow.

On 2026-11-04 the CI host's SSH journal showed current CI container connections
using the first operator key, not the legacy `homelab-nas-deploy` key. Restricting
the legacy key therefore does not remove the current CI/operator credential
sharing. Replacing the protected CI `ssh_deploy_key` secret with a dedicated
identity is a separate coordinated rotation; verify the public fingerprint and
every consumer before doing so. A dedicated identity must also account for
CI-host self-deployment: connections from its own Docker bridge retain a
container source, whereas other fleet targets see the CI host's masqueraded
address. Do not grant all RFC1918 or `172.16.0.0/12` sources: the latter also
contains operator VM networks. The current legacy key needs no Docker exception.
The secondary agent on homelab-05 provides sandbox execution only, not the deploy
tier, and is not an allowed deployment-key source.

After an approved activation, verify noninteractive deployment from NAS/CI,
rejection of this specific key from another infrastructure host, and continued
operator access. Changes to agent access controls follow the exact-change
approval path; do not bypass it while recovering a failed deployment.

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

homelab-04, the CI host, deploys last. Its switch restarts Docker and kills the
deploy job driving it. The switch itself finishes in the transient
`homelab-fleet-switch` unit (`journalctl -u homelab-fleet-switch`), but the job
never verifies or uncordons the node, and the pipeline fails. The deployer
annotates every cordon it places with `homelab.fleet/deploy-cordon`. A later
run that finds such a node already on the desired system, Ready and without
failed units uncordons it. Recover with
`just ship --resume <main sha> --deploy deploy-fleet` after the fleet Lease
expires (5 minutes). A failed host recovery or reboot keeps its cordon without
the mark, and only a person releases it.

## Nightly nixpkgs update

The `fleet-lock-update` operation (`scripts/ci/fleet_lock_update.py`) moves
only the fleet's `nixpkgs` input to the current nixos-unstable and publishes
`flake/flake.lock` on the `flake-lock/nixpkgs` branch, whose PR merges on the
required checks. It refuses an update that moves any other input. A lock that
fails validation leaves the PR open; fix the failure on that branch or let the
next night refresh it. The merged update is delivered by `deploy-fleet` and
takes effect on a new kernel at the next kernel reboot.

The nightly cycle (UTC schedules in `config/ci/woodpecker-crons.json`):

- `fleet-lock` at 05:23 proposes the update.
- `nightly` at 08:17 runs `deploy-fleet` after its dry run and delivers main.
  When main would activate authorization or storage changes the deployed
  baseline lacks, the schedule shows a `deploy-fleet-held` step naming them
  instead, exactly as an agent deployment would wait; approve the grant or
  ship the change, and the next night delivers.
- `fleet-reboot` at 09:05 and 10:05 reboots hosts onto the installed kernel;
  whichever run falls inside 03:00-04:30 America/Chicago acts.

## Kernel reboots

A switch installs a new kernel, initrd and modules but keeps running the old
ones, so they take effect only after a reboot. `just fleet-reboot`
(`scripts/fleet_reboot.py`, the `fleet-reboot` operation) reboots, one host at
a time, each host whose booted kernel, initrd or modules differ from its
running system. It only runs between 03:00 and 05:00 America/Chicago and
starts no host after 04:30; outside the window it exits without doing anything.

It never deploys: a host is rebooted only when its boot profile is the running
system, which a gated deployment activated and verified. Order and checks:

- k3s nodes and the DNS appliance in inventory order. k3s nodes are cordoned
  and drained, then must boot the installed system with no newly failed unit,
  report Ready (and Postgres primaries for homelab-01) before they are
  uncordoned.
- nas-01 next, only when a platform backup succeeded in the last two hours, no
  backup, restore drill or repository maintenance is running and
  `zpool status -x` is healthy. Otherwise it is deferred to the next night with
  an ntfy notice. After its reboot the datasets, NFS and caches are checked.
- homelab-04 last, because the CI job runs there. It is drained, a transient
  `homelab-reboot-watch-homelab-04` unit on homelab-01 uncordons it once it
  reports Ready on the installed kernel (within 20 minutes), and its reboot is
  scheduled 90 seconds out. If the watcher gives up, the node stays cordoned:
  check `journalctl -u homelab-reboot-watch-homelab-04` on homelab-01.

The first failure stops the run, leaves that node cordoned and sends an urgent
ntfy alert. Inspect the host, then `kubectl uncordon <node>` once it is
healthy. Preview the plan at any time, read-only:

```sh
nix develop ./flake#ops -c just fleet-reboot --dry-run --ignore-window --no-notify
```

## Acceptance

After deployment, verify the selected hosts and dependent workloads using
`just status cluster` or the target-specific runbook. For appliances, verify the
service behavior described by that appliance's runbook. Report dry-run,
deployment, live acceptance, and recovery evidence separately. A successful
`nixos-rebuild switch` does not by itself prove the service is healthy.

### NVIDIA driver changes

`flake/modules/nvidia.nix` pins NVIDIA `595.104.02` so the certificate-trust
security rollout can switch live while matching the GPU hosts' loaded driver.
A live switch to `595.112.07` failed the CDI generator with an NVML
driver/library mismatch and triggered automatic rollback. Upgrade the driver
as part of a rolling reboot, then verify the CDI generator and NVML metrics
at `http://<host>:9835/metrics` before returning each GPU node to service.
`nvidia-smi` is unsuitable for this check because it crashes with the current
glibc; the exporter reads NVML directly.

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
