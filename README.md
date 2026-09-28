# homelab-new

NixOS homelab desired state.

This repository is the source of truth for the new homelab's host, network,
Kubernetes, and external-system configuration. Runtime state and deployment
evidence are kept separate from desired state.

## Ownership

- `flake/` owns NixOS hosts, disks, host networking, k3s installation, and host secrets.
- `tofu/` owns API-managed external systems and supported OPNsense resources.
- `opnsense_reconciler/` owns OPNsense interface and FRR settings absent from the OpenTofu provider.
- `gitops/` owns all Kubernetes objects through Flux.
- `secrets/` contains only SOPS-encrypted material.

The local container registry is owned by `flake/` on `nas-01` and consumed by
k3s through pinned digests in `gitops/` base manifests. Registry content is
represented by the immutable mapping in `registry/images.lock.json`. Image
import, verification, and policy checks are explicit CI or operator actions.
See [`docs/runbooks/local-registry.md`](docs/runbooks/local-registry.md) for
the deployment sequence, credential boundaries, backup and restore procedure,
and rollback rules.

## Deployment

First installation uses `nixos-anywhere`. Subsequent NixOS activation uses `nixos-rebuild switch` via `scripts/deploy_fleet.py` from the NAS-hosted GitLab runner. OpenTofu applies and OPNsense reconciliation run only in CI. Flux reconciles Kubernetes state from Git.

Do not apply infrastructure from a laptop.

## CI runner lanes

`nas-privileged` is the protected NAS runner for deployment, OPNsense, and
registry import jobs. Its GitLab runner must be locked, protected, limited to
one job, and configured not to accept untagged jobs. Scope `SSH_DEPLOY_KEY`,
`HOSTS_KNOWN`, OPNsense, and registry credentials to the `production`
environment in GitLab.

`nas-ci` is a separate NAS runner for formatting, repository tests,
YAML/schema checks, secret scanning, registry-lock validation, and GitHub sync
across `main`, release tags, and feature branches / merge requests. Register it
with the `nas-ci` tag, locked, with untagged jobs disabled, and unprotected
access level so it can run both protected and feature pipeline jobs. Its
authentication-token file is `/persist/gitlab-runner/ci-authentication-token`;
do not give it deployment, OPNsense, or registry credentials.

Validation jobs are interruptible so new commits cancel superseded checks.
Deployment, mirror sync, and registry mutations retain their cancellation defaults.
The dedicated secret scan checks the pushed commit range or the MR diff base
through HEAD. Scheduled, manual, tag, and new-branch pipelines scan all history.
Missing history is fetched before scanning; an invalid range fails the job.
Repository tests omit their duplicate secret scan; local `just check` retains it.
Flake checks and cache population share the `nix-flake-evaluation` resource group,
so only one runs at a time across pipelines. Their logs include elapsed time
and Nix evaluation statistics for performance comparisons. Cache population runs
against the same tracked flake source to reuse validated check outputs. It runs
automatically for build inputs and scheduled or explicitly triggered main pipelines.
Other main pushes expose a blocking manual cache job. Fleet deployment always
requires its successful build, including when upload credentials are unavailable.

CI assigns formatting, YAML linting, registry policy, and secret scanning to
dedicated jobs. Repository tests retain type checks, unit tests, rendered Kubernetes
validation, and provider validation. Local `just check` retains the full gate.
Validation jobs publish timing artifacts; repository tests also publish JUnit results.
Jobs have explicit time limits and retry once for runner infrastructure failures.
Deployment and registry mutations do not retry automatically.

GitHub mirroring uses a separate resource group, checks the current source tip
before each push, and uses an exact remote lease. Its temporary authentication
helper and cache credentials are isolated to their respective jobs.

The read-only OPNsense dataplane check depends on the validated plan, so optional
manual registry jobs do not block it. Infrastructure mutations retain manual gates.

## Local workflow

Use the repository's pinned development environment and inspect the current
context before changing files:

```sh
nix develop ./flake
just agent-context
just check-changed
```

Useful registry commands are exposed through `just`:

```sh
just registry-inventory
just registry-resolve
just registry-plan
just registry-check
```

`registry-copy` and `registry-verify` are remote operations and require the
deployment authorization and protected credentials described in the runbook.
Use `just check` for the complete offline validation gate before handoff.
