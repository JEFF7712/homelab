# pod-agent on the homelab

pod-agent (DistroJeff LLC storefront ops) runs in k3s under `gitops/pod-agent/`:
two Deployments (dashboard, discord-bot) and CronJobs declared in the
`cronjobs-*.yaml` manifests, originally migrated from the
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
- `gitops/pod-agent/externalsecret.yaml` — `pod-agent-env` (properties from SOPS-backed
  `homelab-values`) + `pod-agent-age` (age identity key file for backup verify).
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

Application source is `JEFF7712/pod-agent` on Forgejo. The enrolled Woodpecker
release contract is in `config/ci/applications.json`; its `forgejo-pod-agent`
publisher writes `registry.rupan.dev/apps/pod-agent`. GitHub is a mirror;
the former in-cluster GitHub runner is retired. Manifests pin an immutable local
manifest and maintenance promotion adopts verified tags through a protected PR.
See [Application CI](application-ci.md) and [registry operations](local-registry.md).

## One-time setup (owner / privileged CI only)

### Backup encryption key rotation

1. Verify a fresh backup and an isolated restore with the current identity.
   Check that the deployed age executable is executable before changing keys.
2. Retain the old identity in private recovery custody (directory mode 0700,
   file mode 0600). Historical snapshots still need it. Keep the previous
   encrypted secret revision with the recovery source bundle.
3. Generate a new age identity into a mode-0600 file. Update only
   `POD_AGENT_BACKUP_IDENTITY_FILE` and `POD_AGENT_BACKUP_RECIPIENTS` in
   `gitops/secrets/homelab-values.sops.yaml` with `sops set --value-stdin`.
   Pass values through protected stdin, never command arguments or logs.
4. Validate and merge the encrypted change. Wait for Flux and both
   `pod-agent-env` and `pod-agent-age` ExternalSecrets to refresh. Existing
   running containers keep their environment; start a fresh backup Job after
   the two Secret values agree.
5. Verify that the new snapshot decrypts with the new identity, passes SQLite
   integrity checks, and retains the required application state. Confirm a
   historical snapshot still decrypts with the retained old identity.
6. Update the laptop backup identity and its recipient override in
   `~/.config/pod-agent/backup.env` together while its backup service is idle.
   Keep that file private and preserve unrelated settings.

Key rotation protects future snapshots. It does not undo disclosure of an
old identity or change the encryption of existing snapshots.

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

The exact required key set is `gitops/pod-agent/externalsecret.yaml`; it
supersedes the initial import inventory above. A missing property can fail the
ExternalSecret refresh. Follow [secret handling](secrets.md), then verify both
ExternalSecrets without printing their values.

### 2. Registry publication

Use the enrolled application release and its scoped publisher. Pull base images
with the separate read-only node identity; an application publisher's write
grant is not a base-image read grant. For a 401, verify the exact repository's
Woodpecker secret against the intended Zot identity before rotating it. Follow
[registry credential rotation](local-registry.md#rotating-a-publisher-credential)
for recovery; do not restore the retired laptop/GitHub publication workflow.

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

Before recovery smoke tests, suspend every pod-agent CronJob and stop all
laptop timers and services that can write state or refresh OAuth. Confirm no
existing Jobs or other writers remain. Run one manual job at a time for observe,
oauth-refresh and provider smoke; never run the refresher alongside the laptop:

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

## Historical release evidence

Dated release notes and acceptance observations are retained in
[September 30–October 7 release history](../research/2026-10-07-pod-agent-release-history.md).
Use the current manifests and fresh runtime probes to identify the deployed
image and active schedules. Historical application approvals do not authorize
a different release.

## Rollback

1. Suspend the cluster CronJobs and scale both Deployments to 0. Confirm all
   jobs and writers have stopped before starting a replacement.
2. Verify and restore the newest cluster backup to the laptop, including
   current OAuth state. Check SQLite integrity and application compatibility;
   preserve the pre-restore state for recovery. The old laptop DB is stale.
3. Re-enable the laptop timers and services only after restore verification.
   Confirm the laptop is the sole token refresher and state writer.

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
