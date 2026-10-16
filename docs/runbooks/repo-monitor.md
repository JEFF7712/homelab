# Repository health monitor

## Scope and ownership

The repository monitor answers one question continuously: are the Forgejo development
repositories still mirrored to GitHub, and is Woodpecker CI still validating their
branches. It runs as the native NixOS service `repo-monitor` on `homelab-04`
(`10.0.30.14`), beside Woodpecker, and exposes Prometheus metrics on port `9110`.
NixOS owns the service and its thresholds. Flux owns the Prometheus scrape job, alert
rules, and Grafana dashboard under `gitops/observability/`. The Forgejo and GitHub
read tokens live in an out-of-band file, never in the Nix store.

The monitor is strictly read-only. It issues `GET` requests to the Forgejo API, the
GitHub API, and the Woodpecker health endpoint, and it never mutates remote state.

## Data flow

- Repository selection comes from the authoritative `config/ci/projects.json` manifest,
  with `config/ci/applications.json` classifying application CI contexts. Both files are
  embedded at build time (`flake/modules/repo-monitor.nix`), so onboarding a repository
  needs no monitor change, only a manifest change and a redeploy.
- Mirror verdicts compare the Forgejo default-branch head with the GitHub default-branch
  head. Identical heads are healthy. When the heads differ, the GitHub compare API
  decides the relationship: `ahead` (Forgejo has commits GitHub lacks) is normal
  asynchronous lag, bounded by `MIRROR_GRACE_SECONDS`; `behind` or `diverged` is a real
  divergence. A compare `404` means GitHub does not have the Forgejo head commit yet,
  which is also lag, not a broken check. Tag refs are compared when both sides report
  them, and a Forgejo push-mirror `last_error` is a verified fault (state `fault`), not a
  monitoring error.
- CI verdicts are read from Forgejo commit statuses with a `ci/woodpecker/` context
  prefix, because Forgejo is the authoritative aggregator that Woodpecker reports into.
  This keeps the monitor on the existing Forgejo read token instead of needing a
  Woodpecker API token. The expected context per repository is
  `ci/woodpecker/development-validation` for development projects,
  `ci/woodpecker/application-validation` for application projects, and
  `ci/woodpecker/validation-v2` for the `homelab` repository. The expected context must
  match exactly; a success on an unrelated Woodpecker context (for example a release
  pipeline) does not stand in for the required validation.

### The `homelab` mirror is special

The `homelab` GitHub mirror is driven by CI (`scripts/ci/mirror.sh`), not a standard
Forgejo push mirror, and it intentionally adds GitHub-only commits. It is configured as
a `specialMirrors` entry with `append` mode: the monitor expects the Forgejo head to be
an ancestor of the GitHub head. GitHub compare reports that ancestor relationship as the
Forgejo head being `identical` or `behind` the GitHub head, so those two statuses are
healthy. A Forgejo head that GitHub has not received is lag by grace, and only a genuine
fork (`diverged`) is reported as divergence. Do not weaken the standard push-mirror
checks to accommodate this repository; keep it isolated to the `append` entry.

## What "error" means versus "unhealthy"

A failed API call (Forgejo, GitHub, or Woodpecker unreachable, or a rejected token) is a
monitoring error, not a repository fault. The monitor emits `state="error"` for that
repository. A per-repository check that cannot be verified (a permission failure on one
repository, a compare error, or a `push_mirrors` call that fails with anything other than
`404`) is treated the same way: it counts toward `repo_monitor_check_errors_total` and
fails the collection, so it cannot hide behind healthy service probes. A `404` on
`push_mirrors` means no mirror is configured and is ignored. In both cases the single
signal is `RepoMonitorStalled` (collection has stopped succeeding) plus the matching
service-down alert, and no per-repository mirror or CI fault fires. `RepoMonitorStalled`
also fires when the process has never completed a successful collection since it started
(`repo_monitor_last_success_timestamp_seconds` stays `0`), bounded by
`repo_monitor_start_timestamp_seconds`. Only a successful collection can raise a mirror
or CI fault. This separation is deliberate: never page on a fault the monitor could not
actually verify.

A verified push-mirror failure is different: when Forgejo itself reports a `last_error`
on the push mirror, that is a real, verifiable fault. The monitor emits `state="fault"`
and `repo_mirror_push_error{...} = 1`, which raises `RepoMirrorPushFailed` while the
collection still counts as successful (all dependencies answered and every check was
verifiable).

GitHub rate limiting degrades detail without failing collection. It sets
`repo_monitor_github_rate_limited`, reports `repo_service_credential_ok` as true, and
keeps `snapshot.success` true, because the credential is valid and the condition is
transient. This includes repository checks: a rate-limited mirror check reports
`state="error"` with a `rate_limit` classification but is not counted as a collection
error. Only a rejected credential, an unreachable service, or an unverifiable
repository check fails a collection.

## Required runtime material

Provision this file before activation. Do not commit its contents or place it in the
Nix store.

| Host | Path | Mode | Required content |
| --- | --- | --- | --- |
| `homelab-04` | `/persist/repo-monitor.env` | `0600`, root | `FORGEJO_READ_TOKEN` (Forgejo read token) and `GITHUB_TOKEN` (GitHub token with public-repo read scope) |

`FORGEJO_READ_TOKEN` is the same class of read-only Forgejo token the reconcilers and
mirror automation use; the monitor only calls read endpoints with it. `GITHUB_TOKEN`
raises the GitHub API rate limit and allows reads of private mirrors; a token with
public-repo read scope is sufficient when every mirror is public.

Thresholds are set declaratively in `flake/hosts/homelab-04/default.nix` (see
`homelab.repoMonitor`), not in the environment file. The service environment accepts:

| Variable | Module option | Default | Meaning |
| --- | --- | --- | --- |
| `POLL_INTERVAL_SECONDS` | `pollIntervalSeconds` | `300` | Seconds between collection cycles |
| `MIRROR_GRACE_SECONDS` | `mirrorGraceSeconds` | `5400` | Allowed asynchronous mirror lag |
| `CI_GRACE_SECONDS` | `ciGraceSeconds` | `1800` | Grace after a push before a missing validation is reported |
| `CI_STUCK_SECONDS` | `ciStuckSeconds` | `5400` | Seconds a run may stay pending before it is reported stuck |
| `CI_LOOKBACK_SECONDS` | `ciLookbackSeconds` | `604800` | Age beyond which a commit without validation is inactive, not missing |

## Metrics

| Metric | Type | Meaning |
| --- | --- | --- |
| `repo_monitor_up` | gauge | `1` when the process is serving |
| `repo_monitor_last_success_timestamp_seconds` | gauge | Unix time of the last successful collection (`0` until the first success) |
| `repo_monitor_start_timestamp_seconds` | gauge | Unix time the process started |
| `repo_monitor_collection_age_seconds` | gauge | Age of the current collection |
| `repo_monitor_projects_total` | gauge | Repositories in the manifest |
| `repo_monitor_mirrors_total` | gauge | Mirror targets checked (17 today: 16 push mirrors plus the `homelab` append mirror) |
| `repo_monitor_ci_repositories_total` | gauge | Repositories with CI validation checked |
| `repo_monitor_check_errors_total{check}` | counter | Errored checks since process start |
| `repo_monitor_github_rate_limit_remaining` | gauge | Remaining GitHub core calls, `-1` when unknown |
| `repo_monitor_github_rate_limited` | gauge | `1` when GitHub rate limiting is active |
| `repo_service_up{service}` | gauge | Service reachable (`forgejo`, `woodpecker`, `github`) |
| `repo_service_credential_ok{service}` | gauge | Credential accepted |
| `repo_mirror_healthy{repository,mode}` | gauge | `1` when the mirror is healthy |
| `repo_mirror_divergent{repository,mode}` | gauge | `1` when histories forked |
| `repo_mirror_push_error{repository,mode}` | gauge | `1` when Forgejo reports the push mirror itself is failing |
| `repo_mirror_mismatched_refs{repository,mode}` | gauge | Count of Forgejo refs missing or different on GitHub |
| `repo_mirror_lag_seconds{repository,mode}` | gauge | Forgejo head age relative to the collection |
| `repo_mirror_state{repository,state}` | gauge | Exactly one `state` series per repository is `1` (`healthy`, `lagging`, `divergent`, `mismatch`, `fault`, `error`) |
| `repo_ci_healthy{repository}` | gauge | `1` when the latest validation succeeded |
| `repo_ci_missing_validation{repository}` | gauge | `1` when a recent push has no validation |
| `repo_ci_stuck{repository}` | gauge | `1` when a validation is past the stuck threshold |
| `repo_ci_pipeline_age_seconds{repository}` | gauge | Age of the latest validation |
| `repo_ci_state{repository,state}` | gauge | Exactly one `state` series per repository is `1` (`success`, `failure`, `pending`, `missing`, `none`, `error`) |

## Alerts

Alerts live in `gitops/observability/kube-prometheus-stack/repo-monitor-rules.yaml`
(`PrometheusRule/repo-monitor`, group `repo-monitor.rules`).

| Alert | Severity | Fires when |
| --- | --- | --- |
| `RepoMonitorStalled` | critical | No successful collection in over 30 minutes, or none since startup |
| `RepoMonitorForgejoDown` | critical | Forgejo API unreachable |
| `RepoMonitorWoodpeckerDown` | critical | Woodpecker health endpoint unreachable |
| `RepoMonitorGithubUnreachable` | warning | GitHub API unreachable |
| `RepoMonitorCredentialInvalid` | critical | A read token was rejected |
| `RepoMonitorGithubRateLimited` | warning | GitHub rate limiting is active |
| `RepoMirrorDiverged` | critical | A mirror history diverged |
| `RepoMirrorPushFailed` | warning | Forgejo reports a push mirror in a failed state |
| `RepoMirrorLagging` | warning | A mirror stayed behind past the grace period |
| `RepoMirrorRefsMismatched` | warning | A Forgejo ref is missing or different on GitHub |
| `RepoCiValidationMissing` | warning | A recent push has no validation |
| `RepoCiPipelineStuck` | warning | A validation stayed pending past the stuck threshold |

The scrape job is `repo-monitor` in
`gitops/observability/kube-prometheus-stack/release.yaml` (`additionalScrapeConfigs`),
targeting `10.0.30.14:9110` every 5 minutes with the label `host=homelab-04`. The
dashboard is `homelab-repository-health`
(`gitops/observability/grafana/dashboard-repository-health.yaml`).

## Deploy and verify

Deployment is authorized infrastructure work. The monitor is a host service, so it
follows the host deployment path; the scrape job, rules, and dashboard follow Flux.

1. Confirm `/persist/repo-monitor.env` exists on `homelab-04` with mode `0600` and valid
   tokens before the first activation.
2. Deploy the host configuration for `homelab-04` (the runbook owner sets the exact
   command; do not deploy from an unverified plan).
3. Confirm the unit: `systemctl status repo-monitor` and `journalctl -u repo-monitor -n 50`
   on `homelab-04`.
4. Confirm the endpoint locally: `curl -sf http://10.0.30.14:9110/metrics | head`.
5. After Flux reconciles, confirm the target is up in the Prometheus targets UI
   (`job="repo-monitor"`) and that `repo_monitor_up` is `1`.

## Diagnose

- `RepoMonitorStalled` only: check `repo_service_up` and `repo_monitor_check_errors_total`
  first. A stalled collection with all services up usually means a rejected credential,
  a per-repository permission or compare failure, or a manifest that no longer parses.
- `state="error"` everywhere: the dependency is unreachable or the token is rejected.
  Fix the dependency or credential; there is no repository fault to chase. A single
  `state="error"` series means that one repository's check could not be verified.
- A single `repo_mirror_divergent` or `RepoCiValidationMissing`: inspect that repository
  on Forgejo. For divergence, do not force-push; investigate the mirror history by hand.
- `RepoMirrorPushFailed` or `state="fault"`: the Forgejo push-mirror job itself is
  failing. Check the Forgejo mirror settings and remote credentials for that repository.
- Persistent `RepoMirrorLagging`: the GitHub mirror has stayed behind past the grace
  period without a verified push-mirror error. Check the Forgejo push-mirror job.
- `RepoMonitorGithubRateLimited`: verify `GITHUB_TOKEN` is present and valid. Without it
  the unauthenticated limit is easily exhausted.

## Validation

```
nix develop ./flake -c python -m unittest tests.test_repo_monitor -v
nixfmt --check flake/modules/repo-monitor.nix
bash scripts/checks/gitops.sh
```

`tests/test_repo_monitor.py` covers healthy and lagging mirrors, divergence, ref
mismatch, a missing GitHub head commit within and past grace, the `homelab` append mirror
(both directions), a verified push-mirror failure, an unverifiable `push_mirrors` call,
CI success, failure, stuck, missing, and inactive states, exact-context matching, GitHub
rate limiting on both the probe and repository checks, service, credential, and
per-repository check failures, malformed manifests, the client retry paths, the rendered
start timestamp, and an end-to-end `--once` run against the real manifest with the
network seam replaced.
