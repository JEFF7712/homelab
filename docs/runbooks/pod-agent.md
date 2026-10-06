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
`registry.rupan.dev/apps/pod-agent`. Manifests pin an immutable local amd64
manifest. The release records below identify its tested source and verified
registry digest. Tags roll forward via the
`registry_promote_first_party` job like every other first-party workload.

## One-time setup (owner / privileged CI only)

### 1. Encrypted secret values

Add each value below to `gitops/secrets/homelab-values.sops.yaml` using SOPS.
The `homelab-secrets` ClusterSecretStore reads the decrypted Kubernetes Secret
(same store as `backups`). Values come
from the laptop `.env`; `POD_AGENT_BACKUP_IDENTITY_FILE` is the *content* of
the age key file (laptop: `~/.config/pod-agent/backup-age.key`), and
`POD_AGENT_DASHBOARD_TOKEN` is new (generate with `openssl rand -hex 32`).

| Secret property | Source |
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

## Customer-case action release, September 30

Application revision `36b7971` links durable cases to resolution proposals,
supplier write intents, and matching owner packets. GitHub Actions run
`36768672143` passed 2,892 tests (three skipped), Pyright, and image publication.
The CI index is
`sha256:ca43759e52381f5934df1833a779874b4cbe5df0b59f9129753ecd04a839028f`.
The imported amd64 manifest is
`sha256:0cd9075379682a0a826b2f285bb2dcc7d07af0b7a18f00b6a76cfe78cfe6dd32`;
its raw bytes match the CI child. Numeric tag `0.0.1790797816` and
`retention-deployed-0cd9075379682a0a` both resolve to this digest.

Backup job `pod-agent-pre-actions-36b7971` accepted encrypted snapshot
`/data/backups/state-20260930T195404379785Z.db.age`, including decryption,
integrity, and plaintext hash verification. It contains 401 proposals and one
OAuth token row. Keep production state and token authority during rollback.

The additive `customer_case_proposals` table preserves case/proposal links.
Executing a check, receiving a successful POST, or completing an owner packet
does not close a customer case. Retry and cancellation outcomes require later
matching supplier observations. Retry verifies production progress;
fulfillment remains separate. Supplier cancellation does not complete an Etsy
refund. Pending or failed intents remain unknown until reconciled. Atomic
per-shop/order intent reservations prevent a new proposal from repeating a
production submission or cancellation. Customer actions remain GATED, and
production retry remains disabled by default.

Rollout commit `f787984` was applied by Flux. Both services are ready on the
verified image ID, all 28 image pins across 16 workload resources match it,
all 225 installed package files match tested source, and the four case tables
exist. The authenticated Orders page and matching read-only CLI/MCP case
views verify. A notification-free scan of 28 supplier orders and 27 Etsy
receipts found zero exceptions or cases. Counts stayed at 401 proposals, 558
business write-log rows, 21 spending reservations, and one token row.
Isolated deployed-code replays verified lost-response recovery, one supplier
request per operation, and separate customer obligations with networking
disabled. No synthetic cases or business writes were inserted in production.

The initial pipeline ran during the image transition and correctly reported
the previous image in its registry gate. Its separate flake job failed
`test_registered_command_recovers_empty_workspace_from_scoped_launch` and
`test_native_session_bridge_survives_filtered_environment_and_cleans_up` in
agent-workflow code unchanged by this release. Post-rollout scoped
documentation, GitOps/schema, registry checks, and full formatting pass. The
broader flake failure remains a separate CI limitation. Refresh actual cluster
observations after rollout before using the final registry gate as evidence.

After Flux applies this release, verify both service image IDs, all 28 image
pins across 16 scheduled and service resources, the four case tables, and all
225 installed Python/JSON package files. Verify the authenticated Orders page
and read-only case views. A notification-free production scan must report
actual cases and preserve business-write and reservation counts. Exercise lost
responses and readback in an isolated temporary database with fake providers
and networking disabled; never insert synthetic cases into production.

Rollback restores the previous `ace7f80d...` image pin while retaining the
database, additive tables, current OAuth authority, and scheduled operation.
Do not re-enable the stale laptop authority. Executable customer messages and
refunds remain separate capability and authority work.

## Shared LLC budget release, September 30

Owner approval covers publication and deployment of application revision
`8eab78aa801d4581dfa119d54e61ae99082fed57`. GitHub Actions run `36780750352`
passed 2,927 tests (three skipped), full Pyright, and image publication.
The CI index is
`sha256:ec7fb02eeec43e479c1ccb71545c0229c14ce25926a47cae9f6ff191fe0a5b4f`.
The imported amd64 child is
`sha256:6e960719f584b8f177cbeeb8ec693228eabc4249b2e9721ae4b0a5857aa96a7c`.
Its raw manifest digest matches the CI child and its revision label matches the
tested source. Numeric tag `0.0.1790804801` and retention tag
`retention-deployed-6e960719f584b8f1` preserve the imported image. The previous
`retention-deployed-0cd9075379682a0a` tag was verified before rollout.

Backup job `pod-agent-pre-budget-8eab78a` accepted encrypted snapshot
`/data/backups/state-20260930T214041017342Z.db.age`, including decryption,
integrity, and plaintext hash verification. It contains 401 proposals and one
OAuth token row. Before migration, production has 558 business writes and
21 consumed reservations totaling 420 cents. The latest successful Mercury
snapshot records 13,374 cents and was about nine hours old at inspection.

The additive migration assigns existing reservations to `distrojeff-llc`
without changing their historical fields. One entity budget retains the $100
reserve, $10 daily limit, and $50 monthly limit. DistroJeff starts with those
limits, Darkbit with zero. Owner allocations share the entity caps; this
release does not assign new spending authority to another shop.

Every current cost-bearing listing action requires fresh shared cash and an
allocation, rechecked before execution. All outstanding commitments count
across date boundaries. Attempted writes cannot release funds, and a failed
write cannot reuse that proposal's reservation for a second request after a
crash. Spending since the bank snapshot also reduces available cash. Unknown
outcomes remain held until verified reconciliation establishes settlement.

After Flux applies the image, verify both service image IDs and every
scheduled/init consumer, all 226 packaged source files, budget tables, and
unchanged reservation-history hashes. Inspect `entity budget status` for both
shops and the authenticated Desk view. Run concurrent shared-cash and
interrupted-write replays only in temporary databases with fake providers and
networking disabled. Refresh actual registry observations after rollout.
The pre-rollout snapshot retains the previous running image honestly, so its
registry gate can report the expected image transition before Flux converges.

This milestone covers publication and renewal fees. It does not establish
individual bank-charge reconciliation or coverage of production, refunds,
overhead, subscriptions, taxes, replacements, or acquisition spend. Customer
messaging, refunds, and an owned storefront remain deferred by owner decision.
Rollback restores the previous image while retaining production data and OAuth
authority; old code also restores shop-scoped reservation checks.

Verified September 30 at approximately 21:59 UTC: Flux applied rollout
commit `327939a1b7b7edbbb1c5f4bab30b9e0364bf9bf3`, and both Deployments
completed rollout successfully. Each service's 226 packaged Python/JSON files
match tested source. All 28 rendered image pins across 16 resources use the
verified image; later repository reads confirm these desired pins remain.
The three budget tables exist, all 21 reservation histories have the same hash
as before migration, and counts remain 401 proposals, 558 business writes,
21 reservations, and one OAuth token row. The authenticated Desk and read-only
budget projection verify $133.74 observed cash, a $100 reserve, no current
holds, and $33.74 cash headroom. Monthly consumed reservations total $4.20.
DistroJeff retains its allocation, and Darkbit remains at zero.

Isolated deployed-code replays pass shared-cash concurrency, persistence of
unknown commitments across restart, and refusal to reuse funds after an
interrupted failed write. Networking was disabled, providers were fake, and
only temporary databases were used. No production financial action or
allocation change was needed for acceptance.

Initial pipeline `2898994410` passed repository tests, the flake check,
YAML/schema, formatting, secret scan, and registry authentication. The
registry-lock gate observed the previous application image before convergence;
the drift check also reported an existing Quartz tag/lock mismatch. This
rollout does not change Quartz desired state or tags.

Follow-up verification completed October 1, 2026 at 01:38 UTC (September 30
in America/Chicago). Both services still run the approved image; all 226
installed Python/JSON files in each match tested source. Counts and historical
reservations are unchanged. Both read-only budget commands and the
authenticated Desk pass. Cash, reserve, headroom, and allocations are
unchanged. October's daily and monthly consumed counters are zero;
September's 420 cents remain in the ledger.

`registry/observed-images.json` was refreshed from actual Kubernetes state
at 01:36:37 UTC and `registry/images.inventory.json` regenerated from it.
Temporary publisher and source-authentication copies on nas-01 and their
scratch directory were removed, with absence verified. The local temporary
publisher credential was also removed.

Documentation and formatting checks pass. The full offline registry gate is
blocked by an existing Quartz lock defect in source revision
`0924a0536708573f0b7a67bfe9bbc3109040058b`: digest `940a511f` is paired with
retention tag `retention-deployed-1e507dd7a5534736`, where
`retention-deployed-940a511fbdd2b6eb` is required. The refreshed snapshot
records Quartz's actual running image, but this follow-up changes no Quartz
desired pins, lock entries, or tags.

## Listing-fee reconciliation release, September 30

Application revision `9c32d45` adds Etsy fee evidence and cash commitments that
remain reserved until platform funding is established. Revision `86ca155`
makes the CLI test credentials self-contained without changing the application
package. Owner approval covers source publication and this deployment. GitHub
Actions run `36807830342` passed 2,959 tests (three skipped), Pyright, and image
publication. The verified CI index is
`sha256:6d5db73fe93345ef2ff2892812831553dbb1fd4b9bda0b733fd007c94f1506b9`.
Its amd64 child is
`sha256:dbfe375306767c79a05eb383c79e276b5efdfb36ef0043612b282f96e3fa2a35`.
Promotion tag: `0.0.1790823694`. Verify both the numeric and retention tags
retain the exact child manifest before updating consumer pins.

Pre-rollout backup job `pod-agent-pre-charge-9c32d45` accepted encrypted
snapshot `/data/backups/state-20261001T024908678299Z.db.age`, including
decryption, integrity, and content-hash checks. The previous application
manifest remains available under `retention-deployed-6e960719f584b8f1`.

After Flux applies this release, verify both Deployments, all 28 container and
init image pins across the 16 POD resources, all 227 installed package files,
and the additive `etsy_charge_entries`, `etsy_charge_observations`, and
`exposure_charge_matches` tables. Exercise the existing observation service
with notifications disabled and provider requests restricted to GET, then
verify fee matching, cash headroom, and the authenticated Desk. Compare
proposal, business-write, reservation, and token-row counts with the
pre-rollout evidence. Original reservation fields, LLC limits, and shop
allocations must remain unchanged.

The pre-rollout provider replay found 21 exact listing-fee matches totaling
420 cents and a payment-account balance of -420 cents. Against the observed
13,374-cent bank balance and 10,000-cent reserve, headroom was 2,954 cents.
Record fresh production results separately; publication of the image does
not establish that the observer or new cash calculation ran successfully.

This release does not add customer messaging, refunds, supplier purchases,
advertising authority, or changes to veto windows. Roll back image pins while
retaining the production database and its additive financial evidence. Never
restore a stale snapshot over newer orders or rotated OAuth credentials.

Verified at October 1, 03:09:55 UTC (September 30 in America/Chicago): Flux
applied rollout commit `2dcd5e7`; both Deployments were ready and all 28 image
pins matched the imported amd64 digest. All 227 installed package files matched
approved source in both services. Observation run 146 exercised the existing
automatic collector with notifications disabled and eight GET requests. Etsy,
Printify, Mercury, Mercury transactions, and fee evidence all reported `ok`.
All 21 listing-fee reservations matched, totaling 420 cents, with zero unmatched
reservations. The payment-account balance was -420 cents, bank cash was
13,374 cents, and shared headroom was 2,954 cents. The authenticated Desk served
the new unsettled-funds figure.

Business counts stayed at 401 proposals, 558 business writes, 21 reservations,
and one OAuth token row. Original reservation fields, LLC limits, and shop
allocations were unchanged. A separate deployed-code replay, with networking
disabled and a temporary database, passed concurrent shared-cash, uncertain
hold persistence, and interrupted-failure retry checks. Discretionary spending
still refused because verified advertising and fixed overhead were unavailable.

The updated homelab base passed 1,246 tests (23 skipped), scoped GitOps,
documentation and registry checks, and formatting. Pipeline `2899560592` passed
repository tests, Flake, YAML/schema, formatting, secret scan, registry auth,
and drift. Its live registry-lock check initially saw the old pods before Flux
finished; retry job `16854152916` passed after rollout. NAS import credentials
and scratch were removed and their absence verified. These observations are
historical release evidence; current cluster state requires a fresh live read.

Closeout verification on October 3, 2026, at 21:53:09 UTC refreshed production
evidence after access was restored. Both services remained healthy, Flux was
ready, and all 28 current consumer pins still used the verified release. Both
installed 227-file packages matched approved source. Scheduled observation
and governor collection had continued through October 3 with successful
provider reads.

Observation run 153 refreshed bank and Etsy ledger evidence together, using
GET-only provider requests with notifications disabled. All 25 reservations
matched fees totaling 500 cents, with zero unmatched reservations. Etsy's
remaining payment-account debt was 80 cents and Mercury available cash was
12,954 cents. Shared headroom was 2,874 cents after the 10,000-cent reserve.
Existing business counts stayed at 413 proposals, 579 business writes, 25
reservations, and one OAuth token row. Original reservation fields, budgets,
allocations, and the authenticated Desk passed verification. Discretionary
spending continued to refuse because advertising and fixed overhead remained
unmeasured.

## Cost accounting release, October 5

Application revision `7efdfb3` adds a private economics inbox and shared operating
cost allocations. GitHub Actions run `37396002685` validated the application
before image publication. The amd64 image digest is
`sha256:c13b422eac2b63e7b2e8dce018b9b14c3f77da1840c115ae1cd7b9dbcf5c135c`.

`ECONOMICS_INBOX_DIR=/data/economics-inbox` enables ingestion before scheduled
observation. Keep the directory mode 0700 and billing evidence mode 0600, owned
by uid 101. Original receipts are private volume data and must never enter Git.
Preview with `observe sync-inputs /data/economics-inbox`; apply verified evidence
with `--apply`, then confirm unchanged imports on retry and `observe cost-status`.

The owner-confirmed inventory assigns current costs to DistroJeff and zero to
inactive Darkbit. Free LLM subscriptions count as zero; Mercury already supplies
Zoho and other bank-paid expenses. The original annual domain receipt verifies
USD 11.48. Inventory coverage expires December 7 and must be renewed with current
evidence. Advertising API costing requires three complete statement canaries
with positive advertising charges. The saved July export is partial, so net
income and discretionary spending remain blocked by incomplete advertising data.

The release preserves reserves, spending limits, and action authority. Migration
withholds legacy statement normalizations until their original CSVs are reimported.
The encrypted pre-deployment snapshot
`/data/backups/state-20261006T005100653483Z.db.age` passed integrity and decryption
verification. The prior image remains available under
`retention-deployed-dbfe375306767c79`. Application release documentation records
live acceptance separately from these desired-state settings.

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
