# pod-agent on the homelab

pod-agent (DistroJeff LLC storefront ops) runs in k3s under `gitops/pod-agent/`:
two Deployments (dashboard, discord-bot) and thirteen CronJobs mirroring the
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
- `gitops/pod-agent/dashboard.yaml` — owner desk, ClusterIP only.
- `gitops/pod-agent/discord-bot.yaml` — approvals gateway bot.
- `gitops/pod-agent/cronjobs-daily.yaml` — backup, observe, cycle, price,
  sweep. `cronjobs-frequent.yaml` — veto-sweep, oauth-refresh, email-watch,
  watchdog. `cronjobs-weekly.yaml` — privacy-purge, learn, discover,
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

1. Merge the `Dockerfile` + workflow in the pod-agent repo and push to `main`
   so Actions publishes `ghcr.io/jeff7712/pod-agent`. First green build
   2026-09-28: `ghcr.io/jeff7712/pod-agent:sha-f6e3bb2`
   (`sha256:d9aa44056f87c2c961d6f2a3ef09b75939f9123ab3ad9f3213d848bd60d7efe9`).
2. From the NAS runner, import it to `apps/pod-agent` per
   `docs/runbooks/local-registry.md`, add the lock entry, and create the
   `publisher-pod-agent` htpasswd + CI credential.
3. Nothing to pin by hand: manifests already carry the pinned digest and
   the promote job verifies it once the producer has published.

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
accumulate on the volume afterwards.

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
- Dashboard has no Ingress: ClusterIP only, reach it with
  `kubectl -n pod-agent port-forward svc/pod-agent-dashboard 3010:80` plus
  `?token=$POD_AGENT_DASHBOARD_TOKEN`. Do not expose it without the token.
- `pod-agent-canary` and `pod-agent-design` timers stay disabled, matching
  the laptop.
