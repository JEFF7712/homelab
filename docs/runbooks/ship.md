# Shipping changes

`just ship` is the delivery path for agents and humans. Run it from a feature
branch whose work is committed. It replaces the hand-written push, PR, poll,
merge, deploy, and Flux-watch sequence, and it fails with the cause instead of
leaving a sleep loop running.

```sh
just ship                          # deliver with automatic routes
just ship --no-merge               # stop once the PR is green
just ship --deploy none            # merge and wait for Flux, run no operations
just ship --deploy opnsense-apply  # also run a held infrastructure route
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
7. When operations are planned, waits for the main push pipeline of the
   merge commit to succeed. Deployment policy requires a successful parent
   pipeline, and Woodpecker reports `validation-v2` as running until the
   whole pipeline ends. If main advances to a commit that contains the
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

`config/ci/ship-routes.json` maps path prefixes to operations from
`config/ci/operations.json`. Automatic routes run under the standing
delivery authorization in `AGENTS.md`. The others are held: `ship` prints the
exact `--deploy` value to use once the user authorizes that infrastructure
change. Add a route when a new service gains a protected publish operation;
`tests/test_agent_ship.py` checks every route names a catalog operation.

| Route | Paths | Operations | Automatic |
|---|---|---|---|
| led-matrix-image | `services/led-matrix/` | `matrix-publish`, then its promotion PR | yes |
| bus-display-image | `services/bus-display/` | `bus-display-publish`, then its promotion PR | yes |
| lidarr-extended-image | `services/lidarr-extended/` | `lidarr-extended-publish`, then its promotion PR | yes |
| voice-asr-image | `services/voice-supply/` | `native-asr-publish` | no (not exercised since the Forgejo migration) |
| home-assistant | `home-assistant/` | `deploy-home-assistant-dry-run`, `deploy-home-assistant` | yes |
| opnsense | `tofu/opnsense/`, `opnsense_reconciler/` | `opnsense-plan`, `opnsense-apply` | no |
| cloudflare | `tofu/cloudflare/` | `cloudflare-plan`, `cloudflare-apply` | no |
| agent-workspace-host | workspace modules and manifest | dry run, then apply | no |
| fleet | `flake/hosts/`, `flake/modules/`, flake inputs | `deploy-fleet-dry-run`, `deploy-fleet` | no |
| ci-host | CI policy, catalogs, `woodpecker.nix`, `flake/hosts/homelab-04/`, projectctl worker, scopes, contracts and pilot profile | `deploy-ci-host-dry-run`, `deploy-ci-host` | no |

GitOps-only changes need no operation: merging deploys them, and `ship`
verifies the Flux result.

The Woodpecker policy service on homelab-04 runs the policy and operation
catalog from its deployed NixOS generation, not from main. A merged change
to either, including a new operation or a new secret on an existing one,
takes effect only after the `ci-host` route deploys homelab-04. When the
change adds the `deploy-ci-host` operations themselves, or alters the
Woodpecker agents (a deploy restarts the agent running the operation),
deploy locally with `just deploy-fleet --target homelab-04` instead.

## Credentials

`ship` reads the `homelab-agent` token from the `tea` login and the Woodpecker
token from `WOODPECKER_TOKEN` or `~/.config/woodpecker/token`. It never prints
either. Deployment operations run in CI with CI-held credentials, so a
service publish never depends on the caller's Docker login.

## Acceptance

`ship` proves CI, operation, and Flux convergence. It does not prove the
user-visible behavior. After it reports `done`, check the changed behavior
as the subsystem runbook describes.
