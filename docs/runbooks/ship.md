# Shipping changes

`just ship` is the delivery path for agents and humans. Run it from a feature
branch whose work is committed. It replaces the hand-written push, PR, poll,
merge, deploy, and Flux-watch sequence, and it fails with the cause instead of
leaving a sleep loop running.

```sh
just ship                          # deliver with automatic routes
just ship --no-merge               # stop once the PR is green
just ship --deploy none            # merge and wait for Flux, run no operations
just ship --deploy opnsense-apply  # explicitly select an operation
just ship --resume <merge-sha>     # finish an already merged deployment
just ship --json                   # machine-readable summary on stdout
just ship --verify 'curl --fail https://service.example/health'
```

## What it does

1. Preflight: refuses `main`, a detached HEAD, uncommitted tracked changes, or
   a branch with nothing beyond `origin/main`.
2. Runs `just check-changed origin/main` (skip with `--no-checks`).
3. Pushes `HEAD` through the `git-agent` alias so `homelab-agent` owns the
   branch. A missing alias is an error, never a fallback to `origin`.
4. Opens the PR, or reuses an open PR for the same branch.
5. Waits for `ci/woodpecker/validation-v2` on the PR head. On failure it prints
   the tail of each failing Woodpecker step and exits nonzero.
6. Merges. When Forgejo reports the head is behind main, it updates the PR
   branch and waits for validation again.
7. When operations are planned, waits for the main push pipeline covering the
   merge commit to finish with successful source validation. Unrelated failed
   operations do not invalidate that validation. If main advances to a commit that contains the
   merge, it follows the newer tip, because the superseded pipeline is
   cancelled. A pipeline killed while the merge is still the tip (a restart
   of an older main pipeline cancels it) is restarted once; a second kill
   fails the ship.
8. Runs the deployment operations routed from the changed paths, each with its
   catalog prerequisite, against that main pipeline.
9. When an operation prints a promotion patch (base64 encoded between the
   `BEGIN/END PROMOTION PATCH` markers), ships it as its own PR on a temporary
   worktree, recursively, with the same deadline.
10. When `gitops/` or `registry/` changed, requests a Flux source reconcile and
    waits until each Kustomization containing a changed path reports Ready at
    a revision that includes the merge. It falls back to the root `flux-system`
    Kustomization when no narrower one matches.

The default overall timeout is 90 minutes (`--timeout`).

## Routes

`config/ci/ship-routes.json` maps changed paths to catalog operations. All
current routes are automatic, including fleet, CI-host, workspaces, OPNsense,
Cloudflare and application releases. CI-host-only paths select that host instead
of also deploying the entire fleet. `--deploy` selects an explicit operation
list and includes prerequisites; `--deploy none` merges without operations.

The task request authorizes catalog operations, including policy and credential
changes. Agent and operator identities use the same validated delivery path.
Saved OpenTofu plans must be recognized and complete; authorization, data and
connectivity risks are reported for the agent to assess against the task scope
and recovery evidence, without a human approval hold. Production credentials
stay in CI and validation remains credential-free.

An operation can finish against an older main revision when every intervening
change is known to affect a different catalog resource group. Both the deployed
policy and the operation runner verify ancestry and input changes. Unknown paths,
shared scripts, catalog changes and credentials invalidate conservatively. The
same source SHA and validation parent still bind all prerequisite artifacts;
plans are never transferred across revisions. Related changes supersede queued
work, and `ship` retries its prerequisite against newer main. Resource locks
prevent an older same-resource deployment from overwriting a newer one.

Fleet operations evaluate the desired system on every selected host, compare it
with the live system, and skip unchanged hosts. Changed closures are prepared
before any drain; activation uses the exact evaluated path. Failed activation
or host health checks restore and verify the previous system before stopping.
Newly failed systemd units fail health verification. See
[NixOS host deployment](nixos-host-deployment.md).

`just ship --resume <merge-sha>` verifies that the commit is already in main,
selects its routes and follows deployment without pushing or opening another PR.
Pass `--deploy <ops>` to recover only selected operations. Source validation must
pass, but unrelated failed main-pipeline operations do not block deployment.
Required operation prerequisites and exact source/artifact checks still apply.
Main advancing triggers revalidation and a retry within the overall deadline.

The Woodpecker policy service on homelab-04 runs the policy and operation
catalog from its deployed NixOS generation, not from main. A merged change
to either, including a new operation or a new secret on an existing one,
takes effect only after the `ci-host` route deploys homelab-04. When the
change adds the `deploy-ci-host` operations themselves, or restarts the
Woodpecker server or agents, use the local deployment path with
`just deploy-fleet --target homelab-04`. A server restart cancels active
workflows through gRPC even when their agents stay running. The task’s standing delivery authorization applies. For recovery after an
approved switch, follow [CI recovery](ci-pipelines.md#recovery).

## Credentials

`ship` reads the `homelab-agent` token from the `tea` login and the Woodpecker
token from `WOODPECKER_TOKEN` or `~/.config/woodpecker/token`. Operations run in
CI with CI-held credentials. It never prints a token. The approvals service is
retained for outstanding legacy requests: before the new policy is deployed,
`ship` can still file the old policy's passkey request and follow its result.
After migration, catalog operations require no operator token or passkey.

## Acceptance

`ship` proves CI, operation, and Flux convergence. It does not prove the
user-visible behavior. Supply the task’s acceptance checks with repeatable `--verify COMMAND` options;
they run after operations and Flux convergence, within the delivery deadline.
A nonzero exit fails delivery; fix or recover and resume. With these checks,
the JSON state is `verified`; without them it is `deployed`. The agent must
verify the requested behavior using the subsystem runbook before reporting
completion. Fleet recovery restores configuration, not persistent-data migrations.

`ship` logs API queue and execution times separately for each operation and
includes them in its JSON result. Queue time does not identify whether a resource
lock or runner capacity caused the wait. Measure before adding runners.

Promotion worktrees are removed on exit. Their unique local branches are deleted
only after confirmed successful merge; failed attempts retain the branch for
recovery. Fetches prune all origin branch tracking refs, including in main-only
workspace clones.
