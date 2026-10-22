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

The deployed policy checks agent mutations against the authorization and storage
source baseline captured in its active NixOS generation. A caller cannot grant
itself permission by editing the checked-out catalog. The check is scoped to what
the operation activates: every operation is gated on the operation runner and risk
module it executes from its own commit; host deployments (anything that runs
`deploy-fleet`) are also gated on CI policy, the operation catalog, approvals,
projectctl authorization and scopes, workspace and NetBird access, SSH/sudo/Nix
trust settings, registry ACLs, state credentials, disk layouts and SOPS
recipients, plus newly changed Nix lines that touch those settings;
`opnsense-apply` is also gated on the agent ACL. Ordinary service code, release
scripts, checks, hooks and application catalogs are not authority, so a pending
policy change never blocks unrelated releases. The full lists and saved-plan rules
live in `scripts/ci/deployment_risk.py`.

OpenTofu applies inspect the exact saved plan after loading its prerequisite
artifacts. Authorization-resource changes, persistent-data deletion/replacement,
interface/gateway deletion and bulk destruction (50 or more resources) require
approval. Ordinary DNS edits and small firewall/reservation cleanup do not.
A risk rejection makes `ship` file an approval request automatically. Do not
retry with an operator token or change catalog permissions to evade the decision.

These checks govern supported delivery and assume trusted configuration authors.
They are not a sandbox for hostile NixOS code with root authority, nor a complete
semantic classifier for arbitrary commands or manifests. Keep direct mutations
within the existing authorization contract and obtain human approval for
high-risk changes even when a source classifier does not recognize them.

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
workflows through gRPC even when their agents stay running. Required
authorization and storage approvals still apply. For recovery after an
approved switch, follow [CI recovery](ci-pipelines.md#recovery).

## Credentials

`ship` reads the `homelab-agent` token from the `tea` login and the Woodpecker
token from `WOODPECKER_TOKEN` or `~/.config/woodpecker/token`. CI accepts high-risk
operations only from a human operator, so for those `ship` uses
`WOODPECKER_OPERATOR_TOKEN` or `~/.config/woodpecker/operator-token` when
present. Without one, which is every agent machine, it files the operation on
the [approvals dashboard](approvals.md), prints the request link, and waits
for the operator's passkey approval; the dashboard triggers the operation and
`ship` follows its pipeline. A declined or expired request fails the ship.
It never prints a token. Deployment operations run in CI with CI-held credentials, so a
service publish never depends on the caller's Docker login.

## Acceptance

`ship` proves CI, operation, and Flux convergence. It does not prove the
user-visible behavior. After it reports `done`, check the changed behavior
as the subsystem runbook describes.
