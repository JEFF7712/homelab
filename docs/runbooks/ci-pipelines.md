# CI pipelines

Runner lanes, validation jobs, and path-based routing for CI pipelines.
The sovereign CI engine is Woodpecker CI (`http://ci.internal:8000`), triggered
by push webhooks from Forgejo (`http://git.internal:3000`).

## Woodpecker CI Architecture

- **Server:** Woodpecker CI server running on `homelab-04` (`10.0.30.14:8000`),
  authenticated via Forgejo OAuth2.
- **Agent Tiers:**
  - `trusted`: Local host execution (`tier=trusted,type=local`) for `main` branch
    commits. Executes `.woodpecker/lint.yaml`, `.woodpecker/offline-checks.yaml`,
    and `.woodpecker/tofu-plan.yaml` within the Nix development environment.
  - `deploy`: Dedicated local agent (`tier=deploy,type=local`) reserved exclusively
    for production deployments.
  - `sandbox`: Docker container executor (`tier=sandbox,type=docker`) isolated for
    untrusted pull requests and feature branches.
- **Pipeline Workflows:**
  - `lint`: Code formatting and gitleaks secrets scan.
  - `offline-checks`: Dependency provisioning and full offline test gate (`scripts/checks/all.sh`).
  - `tofu-plan`: OpenTofu validation and speculative plan execution against Garage S3 state.


## Runner lanes

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

## Validation lanes

Validation jobs are interruptible so new commits cancel superseded checks.
Deployment, mirror sync, and registry mutations retain their cancellation defaults.
The dedicated secret scan checks the pushed commit range or the MR diff base
through HEAD. Scheduled, manual, tag, and new-branch pipelines scan all history.
Missing history is fetched before scanning; an invalid range fails the job.
Repository tests omit their duplicate secret scan; local `just check` retains it.
Flake checks and cache population share the `nix-flake-evaluation` resource group,
so only one runs at a time across pipelines. Their logs include elapsed time
and Nix evaluation statistics for performance comparisons. Cache population runs
against the same tracked flake source to reuse validated check outputs. The cache
job runs automatically on main and remains blocking and manual on release tags.
Fleet deployment retains that dependency and always checks the full flake and
builds all fleet outputs in its preflight, before loading SSH credentials,
including when upload credentials are unavailable.

CI assigns formatting, YAML linting, registry policy, and secret scanning to
dedicated jobs. Repository tests retain type checks, rendered Kubernetes validation,
and provider validation. The flake job owns the sandboxed unit suite and
publishes its JUnit report, including failures recovered from the Nix build log.
The subsequent flake and cache checks reuse its successful derivation instead of
running the suite again. The sandbox includes `gitleaks` so secret-scan regression
tests still execute. Nix-dependent registry integration tests run once outside
the sandbox in the repository job and publish `nix-integration.xml`; they are
excluded from the sandbox suite. Local `just check` retains the full gate.
Validation jobs publish timing artifacts.
Jobs have explicit time limits and retry once for runner infrastructure failures.
Deployment and registry mutations do not retry automatically.

## Path-based routing

Push and merge-request jobs classify the complete changed-path set. Changes only
to root documentation (`README.md`, `HARDWARE.md`, `AGENT_MAP.md`, `AGENTS.md`,
`CLAUDE.md`) and Markdown under `docs/` run documentation checks without unit
tests or fleet checks. Changes confined to `gitops/voice/`, `home-assistant/www/`,
and documentation retain all repository checks and sandboxed unit tests but skip
fleet evaluation and cache builds. Formatting, YAML, registry policy, and secret
scanning remain required in both lanes. Every other path, including Nix files
inside those application directories, selects full validation. Missing history,
new branches, unrelated bases, tags, schedules, web/API pipelines, and
`CI_FULL_VALIDATION=1` also select full validation. Each routed job publishes its
decision and changed paths under `artifacts/ci/`; no required dependency is omitted.

GitHub mirroring uses a separate resource group, checks the current source tip
before each push, and uses an exact remote lease. Its temporary authentication
helper and cache credentials are isolated to their respective jobs.

The read-only OPNsense dataplane check depends on the validated plan, so optional
manual registry jobs do not block it. Infrastructure mutations retain manual gates.
