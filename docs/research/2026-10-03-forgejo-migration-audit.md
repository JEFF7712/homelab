# Forgejo migration audit, 2026-10-03

The hosting cutover works, but the migration is incomplete. CI isolation,
deployment parity, mirroring, state protection, and disaster recovery do not
meet the migration plan's acceptance criteria.

This was a read-only infrastructure audit. No pushes, API writes, deployments,
service interruptions, test PRs, state locks, or credential changes were made.
Local verification and this report are the only work products.

## Scope and evidence

Source snapshot: `1da0c7c7631552cd54da98c47dbbfba12059a57f`.
Forgejo and Flux live tip: `a5af24517bce49161e640323508e2fc897e042cd`.
The live tip adds only `docs/runbooks/pod-agent.md` relative to the reviewed
checkout, so migration configuration is identical in both snapshots.

Evidence was collected on October 3 in America/Chicago, crossing October 4 UTC.
Checks included SSH service/configuration inspection, SQLite queries opened in
read-only mode, Forgejo webhook delivery records, Woodpecker pipeline logs,
GitLab GET requests, Git remote refs/history, Garage bucket metadata and signed
S3 GET requests, and live Flux/External Secrets status. Credential values and
state attributes were not included in the report. State resources were compared
using canonical JSON SHA-256 fingerprints.

The full pinned `nix develop ./flake -c just check` gate passed, including 1,286
Python tests (13 skipped), provider validation, registry checks, rendered GitOps schemas,
documentation, secret scanning, and the applicable Nix flake checks. Evidence:
`.agent-state/evidence/checks/verify-uzzf0m__.json` and its companion log.
The command ran through `just agent-verify --record --task forgejo-migration-audit`.
Source parity probes are in
`.agent-state/evidence/forgejo-migration-audit/source-probes.json`.

## Findings

### P1: PR and feature code can execute on the trusted host agent

Both [lint](../../.woodpecker/lint.yaml) and
[offline-checks](../../.woodpecker/offline-checks.yaml) accept pull requests and
select `tier: trusted`, `type: local`. Lint also accepts feature-branch pushes.
[woodpecker.nix](../../flake/modules/woodpecker.nix) configures no server-side
configuration extension. The live repository also has an empty configuration
extension endpoint, fork-only approval, and trust enabled for networks, volumes,
and security. Agent labels are supplied by repository YAML.

All observed workflows used trusted agent 18. Sandbox agent 16 and deploy agent
17 have never received work (`last_work=0`). The trusted account is a Nix trusted
user; its service has `ProtectSystem=no` and `NoNewPrivileges=no`. Repository
approval is not equivalent to forcing approved untrusted code into containers.

The live shared `/persist/woodpecker/agent.env` is mode 0644 and contains
`WOODPECKER_AGENT_SECRET`. Readability was confirmed separately as both
`woodpecker-ci` and `woodpecker-deploy`, without printing the value. Local-backend
commands also execute in the agent's own user/environment context.

Implement enforcement outside repository-controlled labels, event/branch-aware
approval and agent authorization, and per-agent credentials protected from job
code. Remove host execution for untrusted inputs before testing an adversarial
PR. Also restrict Forgejo/Woodpecker signup: both are presently open. The
repository is currently private, so this finding does not assert anonymous
access to its PRs. See the [Woodpecker server configuration](https://woodpecker-ci.org/docs/administration/configuration/server)
and [local backend security guidance](https://woodpecker-ci.org/docs/2.8/administration/backends/local).

### P1: GitLab is divergent and its DR mirror is failing

**Resolved 2026-10-06.** GitLab `main` was force-synced to Forgejo `main`
(`5502f04`); the former exclude-by-writer GitLab-only commits are
duplicated in Forgejo's history. `git-dr-mirror.service` on nas-01 now
reports "Everything up-to-date" and runs on a five-minute timer. All
site/publisher writers now push via Forgejo PRs only (`nix-agent` and
`obsidian-vault` deploy workflows are the canonical examples). GitLab's
remaining use is a read-only DR mirror. The old GitLab direct-write roll
flow (`secrets.HOMELAB_GITLAB_TOKEN`) was removed from the obsidian deploy
workflow during this reconciliation.

As of 2026-10-03: Forgejo `main` was `a5af245`; GitLab `main` was `d36436f`. They have diverged:
Forgejo has one exclusive documentation commit and GitLab has three exclusive
Obsidian deployment/registry commits authored by `github-actions[bot]`.
Neither tip contains the other. The external writer's source workflow was not
available in this checkout, but its commits establish that GitLab is still a
write target.

The Forgejo push-mirror record had `sync_on_commit=1`, an eight-hour periodic
interval, and a nonempty error: GitLab rejects a force push to protected `main`.
GitLab's protection was independently verified with `allow_force_push=false`.
The mirror timestamp is October 3 at 21:56:10 UTC, but the error and differing
refs show that this timestamp is not proof of successful replication.

The plan's `git merge --ff-only gitlab/main` recovery step cannot reconcile this
actual divergence. Preserve and reconcile both histories, migrate every writer
to Forgejo, then establish the DR mirror policy. Simply allowing force pushes
would risk deleting the three GitLab-only commits. Git mirroring is asynchronous;
the local-development runbook's claim of synchronous backup is incorrect.

### P1: Deployment and maintenance CI were not migrated

**Status 2026-10-06.** Woodpecker now covers `validation-v2` (sandbox docker
tier) plus the `opnsense-plan`, `cloudflare-plan`, `sync-to-github`, and
`cache-publish` push-gated operations via the policy config extension in
[policy.py](../../scripts/ci/policy.py), and a 10-minute maintenance cron
(via Woodpecker cron named `maintenance`) that fans out to
`registry-drift-check`, `registry-retention-reconcile`,
`registry-promote-first-party`, `registry-lock-import`, `opnsense-inventory`,
`nas-proof`, and `deploy-fleet-dry-run`. Maintenance cron and the deployment
events required the corresponding Woodpecker repo secrets to be widened
to include the `cron` event for the secrets their operations consume
(`ssh_deploy_key`, `hosts_known`, `registry_importer_auth_file`,
`opnsense_plan_api_key`/`opnsense_plan_api_secret`, `opnsense_ca_file`,
`opnsense_backup_recipient`). All records are verified by successful `cron`
validation pipelines on 2026-10-06. Direct Gitlab deploy jobs remain for
observation plus legacy Fleet/HA/registry-node provisioning; these still
run under the provisioning path because they need privileged host access
and write semantics and are intentionally gated behind `deployment` events
with operator-appointed pipeline variables.

[.gitlab-ci.yml](../../.gitlab-ci.yml) contains 48 concrete jobs and 12 named
resource groups. Woodpecker contains three workflows and two groups. There are
no deployment-event workflows; the live repository has `allow_deploy=0`, no
configured Woodpecker secrets, and an unused deploy agent.

Fleet and Home Assistant deployment, OPNsense reconciliation/apply/dataplane
checks, Cloudflare plan/import/apply, registry import/promotion/retention/drift,
publisher provisioning, cache publication, and GitHub mirroring have no
equivalent active Woodpecker workflows. A running deploy service does not
provide these operations. Release tags and schedules also have no matching
workflow event filters. Feature pushes receive lint rather than the full gate.

The workflow named [tofu-plan](../../.woodpecker/tofu-plan.yaml) provisions
providers with `init -backend=false`, then calls
[tofu.sh](../../scripts/checks/tofu.sh), which only runs formatting and
`tofu validate`. It performs no backend access, refresh, speculative plan, saved
plan, or artifact handoff. The Garage artifact bucket exists but is empty.

Retain explicit manual promotion, source-bound plan artifacts, least-privilege
credentials, and the appropriate shared mutation locks when migrating these
jobs. A three-workflow green pipeline is not full legacy CI parity.

### P1: State locking and offsite/platform recovery are absent

Both [OPNsense](../../tofu/opnsense/backend.tf) and
[Cloudflare](../../tofu/cloudflare/backend.tf) use Garage S3, but omit
`use_lockfile` and DynamoDB locking. State is therefore unlocked. Woodpecker
workflow concurrency does not protect access from another scheduler or machine.
The [OpenTofu S3 documentation](https://opentofu.org/docs/language/settings/backends/s3/)
describes the explicit opt-in for native locking.

Garage is healthy, with one replica and two readable state objects. OPNsense
has six resource groups and Cloudflare one; every canonical resource fingerprint
matches the corresponding retained GitLab state. However, Garage states have
serial 1 and new lineages, while GitLab has serials 26 and 8 and different
lineages. Resource preservation is verified; exact state lineage preservation
is not. A rollback must account for this rather than blindly pushing state.

The state and artifact buckets share a single read/write key. Separate planning
and deployment identities were not implemented. State access uses HTTP.

No source-defined offsite backup or live backup timer covers Garage,
Forgejo metadata, or Woodpecker. Historical `tank/forgejo` and `tank/s3`
snapshots exist, but their newest snapshots are October 1 at 22:00 UTC; neither
dataset has a current Sanoid policy in
[nas-data.nix](../../flake/modules/nas-data.nix). `zroot/persist` has no snapshots.
Forgejo SQLite is in `/persist/forgejo/data`, Garage metadata in
`/persist/garage/meta`, and Woodpecker SQLite on a different host in
`/persist/woodpecker/server-data`. Repository/data snapshots alone do not recover
these databases, OAuth configuration, webhooks, and credentials. Existing
Restic/R2 jobs cover other data, not these paths.

The retained GitLab states are currently readable and their resources match,
which offers a historical recovery source. It is not ongoing offsite state
replication. No restore or locking drill was performed against production.

### P1: Woodpecker silently skips checks formerly assigned to other jobs

`offline-checks` sets `CI_LINT_EXTERNAL=1`. In
[all.sh](../../scripts/checks/all.sh) this skips the registry policy/check-plan
gate, and in [gitops.sh](../../scripts/checks/gitops.sh) it skips YAML linting.
The remaining workflows run neither check. `fmt-check` covers Python, Nix, and
OpenTofu, not YAML. Rendered schema validation still runs, but does not replace
registry policy or YAML linting. Remove this flag or add the missing dedicated
checks before using CI success as the acceptance gate.

### P2: Flux webhook delivery is broken; polling is the working fallback

Forgejo's Flux webhook targets the public Cloudflare hostname, which is absent
from [Forgejo's allowed webhook hosts](../../flake/modules/forgejo.nix). Recent
delivery records explicitly fail with `webhook can only call allowed HTTP
servers`. Woodpecker webhook deliveries succeed with HTTP 200.

Both Flux receivers are Ready, but that status only proves receiver
initialization. The latest Forgejo deliveries fail. All 21 Kustomizations and
the root GitRepository nevertheless reconcile the Forgejo live tip successfully
through the configured one-minute polling path. Webhook latency claims are
unverified. Add the exact intended endpoint to the allowlist or use an approved
internal endpoint, then measure actual delivery and reconciliation.

The retained GitLab receiver targets the same Forgejo GitRepository. It does
not change its URL or provide automatic GitLab failover.

### P2: GitLab rollback is not an immediately usable fallback

`gitlab-runner.service` is absent on homelab-04 and inactive/absent on the NAS.
GitLab still has two registered, unpaused project runners, both offline;
shared runners are disabled. Hourly scheduled pipelines still start and leave
jobs pending or waiting for resource groups. Thus GitLab is not an inactive
mirror-only scheduler, even though there are no executors to run its jobs.

The plan's `systemctl start gitlab-runner` rollback fails because the unit was
removed. Token filenames still exist on homelab-04, contradicting the claim
that the token directory was cleared; their contents/validity were not checked.
Recreating an executor, restoring credentials, reviewing queued schedules, and
ensuring one mutation authority are required before fallback execution.

The retained GitLab YAML supplies HTTP-backend variables, while both current
backend files require S3. Re-enabling a runner alone will neither use the old
HTTP state nor work during a NAS/S3 outage. Flux needs an explicit source and
authentication cutover as well. The plan proposes rebootstrap but provides no
tested, maintained recovery runbook or completed outage drill.

### P2: GitLab dependencies and GitHub mirroring remain unmigrated

[External Secrets](../../gitops/eso/secretstore.yaml) still reads project
85910419 on GitLab.com. All 18 observed ExternalSecrets are currently healthy,
so this is a working retained dependency, not a current outage. A fresh cluster
or credential refresh is not sovereign during a GitLab outage. Existing
materialized Kubernetes secrets can remain usable, subject to each consumer.

[Renovate](../../gitops/automation/renovate-gitlab.yaml) still targets the GitLab
homelab repository, and its reviewer/dashboard still use GitLab APIs. This
creates a competing branch/MR workflow rather than operating on Forgejo.

GitHub `main` is `6bb18a7`, different from both active tips. No Woodpecker
workflow calls [mirror.sh](../../scripts/ci/mirror.sh). That script still requires
GitLab variables and writes a GitLab-primary README notice. GitHub is therefore
not a verified current mirror or fallback.

Forgejo has no branch protection or required CI status checks. Flux already
applied `a5af245`, whose Woodpecker pipeline 19 failed in `offline-gate` because
the Cloudflare provider 5.26.0 package was missing from `.terraform/providers`.
The cause of that runner dependency failure was not reproduced. Pipeline 18
passed on the reviewed local commit, and the local full gate passed again.
CI failures are reported, but do not currently prevent main from reaching Flux.

## Fallback behavior observed

| Failure | Current behavior | Acceptance |
| --- | --- | --- |
| Forgejo-to-Flux webhook fails | One-minute source polling reconciles successfully | Verified from existing failures |
| Woodpecker unavailable | Git and Flux can operate independently; no active alternate CI executor | Partial, no outage injection |
| Forgejo/NAS unavailable | Flux retains its existing artifact and workloads; new source fetches cannot use GitLab automatically | Manual recovery required |
| Garage/NAS lost | Retained GitLab historical states are readable; no automatic offsite sync or fresh backup proof | Incomplete |
| GitLab unavailable | Primary Git/CI can remain local, but ESO refresh and GitLab automation depend on SaaS | Partial |
| Latest CI fails | Forgejo main is still reconciled by Flux | Observed, no required-status gate |
| Forgejo restored with divergent DR commits | Mirrors are enabled by default; protection currently rejects destructive pushes | Recovery workflow untested |

## Repair order and remaining acceptance

1. Enforce untrusted-job isolation and protect agent authentication.
2. Reconcile GitLab-only commits without deleting them; migrate all writers and
   verify main/branch/tag parity and mirror error state.
3. Restore CI coverage and provider provisioning reliability, then migrate
   deployment/maintenance workflows with explicit authority and secrets.
4. Add state locking, separate identities, consistent platform backups, and
   offsite state recovery. Test restore and lock contention in an isolated copy.
5. Repair the Flux webhook and verify actual deliveries. Decide and enforce the
   required CI status policy before Flux receives protected main updates.
6. Replace the historical rollback notes with a maintained procedure covering
   executor recreation, state/backend selection, source/authentication cutover,
   stale schedules, divergent history, and safe restoration of mirroring.
7. Complete controlled sandbox-escape, concurrency, promotion, mirror recovery,
   and NAS-loss drills before marking the migration complete.

The migration task export's `complete` label and historical pipeline 6 success
do not establish these acceptance criteria. It records no source-bound
verification entries and omits several criteria from the original plan.
