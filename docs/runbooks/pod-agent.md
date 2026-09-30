# pod-agent on the homelab

pod-agent (DistroJeff LLC storefront ops) runs in k3s under `gitops/pod-agent/`:
two Deployments (dashboard, discord-bot) and fourteen CronJobs mirroring the
laptop systemd table. All stateful pods are pinned to `homelab-01` with a
`ReadWriteOnce` hostPath volume, because `state.db` is a single-writer SQLite
database with `fcntl` locks. There is exactly one writer rule: the laptop
timers and the cluster CronJobs must never run at the same time, or Etsy
token rotation races.

## Layout

- `gitops/pod-agent/namespace.yaml` — `pod-agent` namespace.
- `gitops/pod-agent/volume.yaml` — PV `pod-agent-data-nvme`
  (`/persist/pod-agent` on `homelab-01`, 5Gi) + bound PVC. Same pattern as
  `gitops/immich/postgres-volume.yaml`.
- `gitops/pod-agent/configmap.yaml` — non-secret env: paths into `/data`,
  CLI-auth homes, provider rotation.
- `gitops/pod-agent/externalsecret.yaml` — `pod-agent-env` (30 keys from
  GitLab) + `pod-agent-age` (age identity key file for backup verify).
- `gitops/pod-agent/dashboard.yaml` — owner desk, ClusterIP service.
- `gitops/pod-agent/network-policy.yaml` — dashboard ingress from the
  `cloudflare` namespace only, so the public hostname is the sole path in.
- `gitops/pod-agent/discord-bot.yaml` — approvals gateway bot.
- `gitops/pod-agent/cronjobs-daily.yaml` — backup, observe, cycle, price,
  sweep. `cronjobs-frequent.yaml` — veto-sweep, oauth-refresh, email-watch,
  watchdog, and planner-recovery. `cronjobs-weekly.yaml` — privacy-purge, learn, discover,
  shipping. Schedules use `timeZone: America/Chicago` to keep laptop
  wall-clock times.
- `gitops/clusters/homelab-01/pod-agent.yaml` — Flux Kustomization.

The container image is built by
[jeff7712/pod-agent](https://github.com/JEFF7712/pod-agent) GitHub Actions
(`Dockerfile` + `.github/workflows/image.yml` in that repo) and imported to
`registry.rupan.dev/apps/pod-agent`. Manifests pin `registry.rupan.dev/apps/pod-agent@sha256:836efb...`
(the single-arch manifest the seed push stored; the ghcr index digest
`sha256:d9aa44...` covers identical config+layers but a different envelope,
because podman normalized the index on push). Tags roll forward via the
`registry_promote_first_party` job like every other first-party workload.

## One-time setup (owner / privileged CI only)

### 1. GitLab variables

Create each variable below in the GitLab project backing the
`gitlab-project` ClusterSecretStore (same store as `backups`). Values come
from the laptop `.env`; `POD_AGENT_BACKUP_IDENTITY_FILE` is the *content* of
the age key file (laptop: `~/.config/pod-agent/backup-age.key`), and
`POD_AGENT_DASHBOARD_TOKEN` is new (generate with `openssl rand -hex 32`).

| GitLab variable | Source |
| --- | --- |
| `DISCORD_WEBHOOK_ORDERS` | laptop `.env` |
| `DISCORD_WEBHOOK_SEO` | laptop `.env` |
| `DISCORD_WEBHOOK_GROWTH` | laptop `.env` |
| `DISCORD_WEBHOOK_APPROVALS` | laptop `.env` |
| `DISCORD_WEBHOOK_ERRORS` | laptop `.env` |
| `DISCORD_BOT_TOKEN` | laptop `.env` |
| `DISCORD_OWNER_USER_IDS` | laptop `.env` |
| `DISCORD_APPROVALS_CHANNEL_ID` | laptop `.env` |
| `DISCORD_SEO_CHANNEL_ID` | laptop `.env` |
| `DISCORD_ORDERS_CHANNEL_ID` | laptop `.env` |
| `DISTROJEFF_ETSY_KEYSTRING` | laptop `.env` |
| `DISTROJEFF_ETSY_SHARED_SECRET` | laptop `.env` |
| `DISTROJEFF_PRINTIFY_API_TOKEN` | laptop `.env` |
| `DISTROJEFF_PRINTIFY_SHOP_ID` | laptop `.env` |
| `DISTROJEFF_RESEND_API_KEY` | laptop `.env` |
| `DISTROJEFF_ZOHO_IMAP_USER` | laptop `.env` |
| `DISTROJEFF_ZOHO_IMAP_PASSWORD` | laptop `.env` |
| `DISTROJEFF_BUYER_EMAIL_FROM` | laptop `.env` |
| `DISTROJEFF_BUYER_EMAIL_AUTO` | laptop `.env` |
| `DARKBIT_ETSY_KEYSTRING` | laptop `.env` |
| `DARKBIT_ETSY_SHARED_SECRET` | laptop `.env` |
| `DARKBIT_PRINTIFY_API_TOKEN` | laptop `.env` |
| `DARKBIT_PRINTIFY_SHOP_ID` | laptop `.env` |
| `MERCURY_API_TOKEN` | laptop `.env` |
| `TELEGRAM_BOT_TOKEN` | laptop `.env` |
| `TELEGRAM_CHAT_ID` | laptop `.env` |
| `POD_AGENT_BACKUP_RECIPIENTS` | laptop `.env` |
| `POD_AGENT_BACKUP_IDENTITY_FILE` | content of laptop age key file |
| `POD_AGENT_DASHBOARD_TOKEN` | new random token |

Done 2026-09-28 via `glab variable import` (27 variables; all protected,
scope `*`, masked except `DISTROJEFF_BUYER_EMAIL_FROM` and
`DISTROJEFF_BUYER_EMAIL_AUTO`, which GitLab refuses to mask). Not uploaded:
`DARKBIT_ETSY_KEYSTRING` and `DARKBIT_ETSY_SHARED_SECRET` are empty on the
laptop (Darkbit has no live listing yet), and their entries were removed
from `externalsecret.yaml` accordingly. Re-add both sides if Darkbit is
ever operated.

### 2. Registry import

Done 2026-09-28, with one detour worth knowing. `provision_pod_agent_publisher`
ran green and installed the htpasswd grant plus policy, and
`REGISTRY_POD_AGENT_PUBLISHER_AUTH_FILE` (GitLab, protected, production) and
the producer's `REGISTRY_PASSWORD` (GitHub repo secret) both hold the same
generated password. But the GitHub workflow's zot push never lands:
`registry.rupan.dev` is behind Cloudflare Access from the public internet, so
`ubuntu-latest` login succeeds yet blob/manifest writes die at a 302. The
existing producers avoid this by building on the self-hosted `homelab`
runner; until pod-agent has one, its workflow publishes ghcr-only.

Seed procedure used (all over the LAN, no Access in the path): on `nas-01`
with podman, pull the ghcr tag, retag to
`registry.rupan.dev/apps/pod-agent:<sha-tag>`, push with the publisher
credential. Note podman normalizes the ghcr index to a single-arch manifest,
so the stored digest (`sha256:836efb...` for `sha-f6e3bb2`) differs from the
ghcr index digest (`sha256:d9aa44...`) while config+layers are identical.
`registry_promote_first_party` then verifies and adopts it. NAS rootfs is
only 4 GB, so point `TMPDIR` at a `/persist` scratch dir for the push and
`podman rmi` plus remove the scratch afterwards.

Follow-up: register a self-hosted `homelab` runner for `JEFF7712/pod-agent`
(like `homelab-apolline` serves apolline-site) and restore the zot push in
its workflow; delete nothing until then.

### 3. Seed the volume (before Flux enables the namespace)

Copy the live database, CLI auth, logos, and Telegram state from the laptop
to `/persist/pod-agent` on `homelab-01` (via the NAS host). Copy the
*WAL-safe* backup rather than the live `.db` files: on the laptop run
`scripts/backup_state.py snapshot`, then transfer the newest verified
snapshot and restore it as `/persist/pod-agent/state.db`. Then copy
`~/.codex/auth.json` to `/persist/pod-agent/cliauth/codex/auth.json`,
`~/.claude/.credentials.json` (plus `~/.claude.json` if present) to
`/persist/pod-agent/cliauth/claude/`, the Cursor auth database to
`/persist/pod-agent/cliauth/cursor/`, `../distrojeff/distro-logos/` to
`/persist/pod-agent/assets/distro-logos/`, and `data/telegram_bot_state.json`
to `/persist/pod-agent/telegram_bot_state.json`. Everything under
`/persist/pod-agent` must be owned by uid 101 (`chown -R 101:101`).
Memory strategies seed themselves from the image on first deploy; learnings
accumulate on the volume afterwards. Seeded 2026-09-28 (15.8 MB snapshot
with 330 proposals, CLI auth for all three providers, logos, strategies).
Note: hostPath volumes skip the fsGroup chown, so pre-create
`/persist/pod-agent` with 101:101 ownership *before* Flux first reconciles,
or the seed init CrashLoops until you do.

### 4. Smoke gate (before cutover)

With the laptop timers still enabled and authoritative, suspend every
pod-agent CronJob and run one manual job each of observe, oauth-refresh, and
the provider smoke:

```sh
kubectl -n pod-agent create job --from=cronjob/pod-agent-observe \
  observe-smoke
kubectl -n pod-agent create job --from=cronjob/pod-agent-oauth-refresh \
  oauth-smoke
kubectl -n pod-agent run provider-smoke --image=<pinned pod-agent image> \
  --env-from=secret/pod-agent-env --env-from=configmap/pod-agent-config \
  -- pod-agent governor providers smoke --shop distrojeff \
  --consume-subscription-usage
```

The third command spends real subscription usage and needs explicit owner
ack. If the codex `--sandbox read-only` or cursor `--sandbox enabled`
invocation fails inside the unprivileged container (bubblewrap/userns
denied), relax that workload's `seccompProfile` to `Unconfined` and re-run
the smoke before touching anything else.

## Cutover

Hard cutover (no parallel running): Etsy refresh tokens rotate on use, so
the laptop and the cluster must never both hold the refresher.

Done 2026-09-29: provider smoke green for codex (`healthy:true`), laptop
timers/services disabled (none remain), cluster CronJobs unsuspended,
manual oauth-refresh and observe runs verified (run_id 139, 77 listings).
Laptop DB is now stale by design; do not re-enable its timers without
stopping the cluster jobs first.

Follow-ups at cutover: Mercury returns `401 ipNotWhitelisted` from homelab
egress, so the finance scan degrades to notify-only until the homelab IP is
allowlisted in Mercury. Claude and Cursor subscription auth did not transfer
(file copy is not honored / machine-bound), so the cluster runs codex-only
rotation until those are re-established; `GOVERNOR_SUBSCRIPTION_PROVIDERS`
in `configmap.yaml` is the switch. The provider path also depends on
`POD_AGENT_PROVIDER_CONTAINMENT=best-effort` (see `docs/DEVELOPMENT.md` in
the pod-agent repo): without it every provider call fails in a pod, and no
pod spec can substitute for it.

1. Confirm the smoke gate passed and the dashboard Deployment is healthy.
2. On the laptop: `systemctl --user disable --now` every `pod-agent-*`
   timer (observe, cycle, price, sweep, veto-sweep, oauth-refresh, learn,
   discover, shipping, email-watch, watchdog, backup, privacy-purge) plus
   `pod-agent-dashboard.service` and `pod-agent-discord-bot.service`.
3. Unsuspend the cluster CronJobs (or let Flux reconcile them) and verify
   the next observe + cycle runs in the dashboard.
4. Watch `#errors` for the dead-token owner action. If Etsy 401s appear,
   re-auth from the token-authority host: port-forward if headless, then run
   `pod-agent etsy auth --shop distrojeff` with `ETSY_REDIRECT_URI`
   reachable (`http://localhost:3003/callback` by default; forward 3003).

## Reliability release, September 30

The planner-recovery CronJob is enabled alongside application release
`81d65c7`, which provides `planner recover`. It runs at minute 45 each
hour, uses the existing production data volume and credentials, and has a
1200-second deadline. `backoffLimit: 0` and `restartPolicy: Never` leave retry
decisions to the persisted planner attempt budget rather than Kubernetes.
Publishing recovery runs in the existing veto sweep and daily cycle.

Owner authorization covers source publication, image import, and production
activation. GitHub Actions run `36677365905` passed 2,828 tests (three skipped),
Pyright, and the image build. Validation uses managed Python 3.12.14, `age`,
and a delegated systemd cgroup so the process containment tests run without
weakening their requirements. The pre-rollout encrypted backup
`/data/backups/state-20260930T061520321839Z.db.age` passed decryption and
integrity verification with 396 proposals and one OAuth token row.

The production plan for September 30 was already completed before this
release. Recovery must leave that plan unchanged. The first new daily plan
under this release remains a separate acceptance gate.

Deployment verified at 06:33 UTC: Flux applied homelab commit `e3aae41`, both
Deployments are ready, and the enabled recovery job uses
`sha256:4bf67af5e1af517a0ce241243527ac60201788daaec7375394e6c0e9807aec31`.
All 222 installed package files match the tested source. The additive
`planner_attempts` table exists. Manual recovery job
`pod-agent-recovery-verify-81d65c7` completed successfully, recording task run
4212 and returning `already_ran` for planner run 50 without a new attempt or
business write. Reservation and write-log counts stayed unchanged.
Homelab pipeline `2896059825` passed all automatic jobs; unrelated deployment
jobs remain manual. A successful fresh plan and a real interrupted-publish
recovery still require separate production evidence.

Release acceptance:

1. Publish the locally tested pod-agent source after owner authorization.
   Verify the image build, import its exact source revision into the local
   registry, and verify the resulting digest before updating all pod-agent
   consumer pins through registry promotion. Preserve unrelated registry
   observations and checkout changes.
2. Back up production state and verify the backup. Keep the laptop timers
   disabled. Set planner-recovery `suspend: false` only alongside the new
   image pin, then publish the scoped GitOps change and verify Flux applies it.
3. Verify the running image and CLI, `planner_attempts` schema, and unchanged
   Etsy token authority. A synthetic failure exercise must use a temporary
   SQLite database, disabled notification credentials, and fake providers;
   never alter a real planner run or inject a failed business write.
4. Verify the next real daily cycle and hourly recovery in `/data/state.db`.
   A failed legacy run without attempt metadata stays blocked. The hourly
   job does not invent a new plan. Confirm the new daily plan reaches a
   completed action batch or an evidence-backed no-action result, and that
   the watchdog reports any continuing failure.
5. Verify publishing recovery retains unknown reservations and settles only
   verified outcomes. Confirm no repeat full publish or SEO write. A healthy
   Deployment or successful synthetic exercise alone does not establish
   production business recovery.

Rollback this release by suspending planner-recovery and reverting the
application image pins. Keep the production database in place: the new
planner-attempt table is additive, and current operation may have newer
orders or rotated OAuth credentials than a prior backup. Do not switch back
to the stale laptop database as part of an application rollback.

## Customer-case release, September 30

Application revision `5ae2fdd` adds persistent customer cases, obligations,
follow-up deadlines, and material-transition history. GitHub Actions run
`36750409346` passed 2,863 tests (three skipped), Pyright, and image publication.
The imported amd64 manifest is
`sha256:ace7f80d9f3128b4bfb16b84abd688575d530fdbaf604932b266c43d0ce22f36`;
its raw bytes match the CI image's amd64 child. Promotion tag:
`0.0.1790788596`. Existing schedules and write authority are unchanged.

Before rollout, backup job `pod-agent-pre-cases-5ae2fdd` verified encrypted
snapshot `/data/backups/state-20260930T172034410713Z.db.age`, including
decryption, integrity, and plaintext content hash. Retain the production
database and OAuth authority when reverting image pins.

After Flux applies the release, verify both Deployments, all scheduled image
consumers, `customer_cases`, `customer_case_obligations`, and
`customer_case_events`, and the authenticated Orders dashboard. Run an issue
scan with notifications disabled and compare
proposal, write-log, and spending-reservation counts before and after. Record
real case observations separately from offline tests. Supplier fulfillment
alone does not establish carrier delivery, a customer response, or a refund.

Customer cases do not yet ingest conversations or execute new refunds,
replacements, claims, or cancellation authority. Rollback the application pin
while retaining the additive tables and their audit records.

Verified after rollout: Flux applied a main revision containing release commit
`45c8b4e`; both service pods are ready on the imported digest. All 28 container
and init image pins across 16 Deployments/CronJobs match it. All 224 installed
package files match tested source, the three new tables exist, and the
authenticated Orders page serves the case section. A live scan covering 28
Printify orders and 27 Etsy receipts found no current exceptions. It made no
new proposals, business write-log entries, or spending reservations. A real
case lifecycle remains unverified until an exception occurs.

Post-rollout `just check-changed HEAD^` passed documentation, rendered
GitOps/schema, and live registry policy. Full formatting passed. The initial
rollout pipeline's offline registry gate used a stale committed observation
snapshot; the follow-up refresh records actual running images. Its drift job
also reported a separate Quartz tag/lock mismatch introduced before this
release. No Quartz desired state or registry tags were changed by this rollout.

## Rollback

1. Suspend the cluster CronJobs and scale both Deployments to 0.
2. Re-enable the laptop timers and services.
3. The cluster never writes anywhere the laptop cannot re-read: restore the
   newest `/data/backups` snapshot to the laptop if the cluster wrote newer
   rows worth keeping.

## Known risks

- Subscription CLI auth is file-based and refreshes itself
  (`/data/cliauth/*` on the volume). When a provider forces re-login, repeat
  the seed copy for that provider; there is no headless renewal.
- The image is ~3.6 GB (Python + Node + three CLIs). Keep registry
  retention tight for `apps/pod-agent` until it is slimmed (multi-stage
  build is the obvious follow-up).
- Dashboard is published at `pod.rupan.dev` through the homelab tunnel, behind
  Cloudflare Access and `DASHBOARD_TOKEN`; both gates are required, see
  `docs/runbooks/cloudflare-tunnel.md`. On the LAN the shortcut is
  `kubectl -n pod-agent port-forward svc/pod-agent-dashboard 3010:80` plus
  `?token=$POD_AGENT_DASHBOARD_TOKEN` (the network policy allows node traffic
  to the pod, so the port-forward keeps working).
- `pod-agent-canary` and `pod-agent-design` timers stay disabled, matching
  the laptop.
