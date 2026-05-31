# Kalshi Prediction-Market Bot — Design

**Date:** 2026-05-30
**Author:** rsunderapand@wisc.edu
**Status:** Draft — pending user review

## Overview

`predmarkbot` is an automated trading bot for [Kalshi](https://kalshi.com) prediction markets. It runs as a long-lived single-replica Python service on the user's homelab Kubernetes cluster, ingests market data via WebSocket (with periodic REST reconciliation), evaluates configurable strategies, and places orders against Kalshi's authenticated REST API.

Version 1 ships with **one strategy**: a YES+NO cross-side arbitrage detector that fires when `best_yes_bid + best_no_bid > $1.00 + fees + min_edge`. This strategy was chosen because it requires no forecasting view and exercises the entire stack end-to-end safely. Additional strategies are expected to be added; the design treats `Strategy` as a pluggable interface.

The entire v1 runs against **Kalshi's demo (sandbox) environment**. The transition to production trading is a one-line URL change in config and is deliberately deferred until the operator has confidence in the bot's behavior.

## Goals

1. **Safely** execute a defined strategy against Kalshi markets without manual intervention.
2. Provide a foundation that supports adding more strategies (forecasting, market-making, etc.) without redesign.
3. Provide observability sufficient to answer "what is the bot doing right now?" and "what did it do yesterday?" without parsing raw logs.
4. Be deployable through the operator's existing GitOps workflow (`~/homelab`, ArgoCD).
5. Make catastrophic loss structurally hard — limits, kill switch, and manual restart after kill-switch trip.

## Non-goals (v1)

- Real-money trading. Demo only until operator opts in.
- Forecasting strategies, market-making, or multi-venue arbitrage.
- A web dashboard, Slack/email/Prometheus integration. ntfy + logs + `status` CLI only.
- Multi-tenant operation; this is a single-operator bot.
- Sub-100ms latency optimization.
- Backtesting (deferred — see Future Work).

## Decisions

These are the outcomes of the brainstorming clarifying-questions phase:

| # | Question | Decision | Rationale |
|---|----------|----------|-----------|
| 1 | Bot type | Exploratory/learning project (D) + simple arb strategy (C) | Build rails first; ship an end-to-end loop with a mechanically-verifiable signal that needs no forecasting view |
| 2 | Market scope | Configurable file (D) + open-market discovery (C) | One config file lists series; bot enumerates open markets in each |
| 3 | Data ingest | WebSocket + REST reconciliation (D) | Operator chose the heavier option deliberately for learning value |
| 4a | Paper-trading mode | Kalshi demo/sandbox API (iii) | Real API surface, fake money, true integration |
| 4b | Risk limits | All five (min-edge, per-market position, total exposure, daily-loss kill, order-rate) | Defense in depth |
| 5a | Runtime | Long-running daemon on homelab k8s (i) | Fits WS choice; operator has 24/7 cluster |
| 5b | Persistence | SQLite on a Longhorn PVC | No extra service to run; queryable; backups via Longhorn snapshots |
| 5c | Observability | Structured logs + ntfy notifications + `status` CLI | Reuses existing `ntfy.rupan.dev` |
| 5d | Secrets | Move RSA key out of plaintext `bot.txt`; sealed-secrets in homelab repo | Current key is plaintext on disk |
| Deployment | Image build | GitLab CI via existing gitlab-runner (A) | Matches operator's existing `infrastructure/automation/images/` pattern |

## Architecture

```
┌─────────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│ MarketDiscovery │──▶│   DataFeed   │──▶│   Strategy   │──▶│ RiskManager  │──▶│   Executor   │
│  (REST, ~5min)  │   │ (WS + REST   │   │  (ArbStrategy│   │ (limits,     │   │ (signed REST │
│                 │   │  reconcile)  │   │   v1)        │   │  kill switch)│   │  orders only)│
└─────────────────┘   └──────────────┘   └──────────────┘   └──────────────┘   └──────────────┘
        │                    │                    │                    │                    │
        └────────────────────┴────────────────────┴────────────────────┴────────────────────┘
                                                  │
                                                  ▼
                            ┌─────────────────────────────────────────┐
                            │       StateStore (SQLite, single)       │
                            │ markets · book snapshots · orders · │
                            │ fills · positions · daily_pnl           │
                            └─────────────────────────────────────────┘
                                                  │
                                                  ▼
                                ┌──────────────────────────────┐
                                │   Notifier (ntfy adapter)    │
                                │ startup · errors · fills · EOD│
                                └──────────────────────────────┘
```

Single Python process, single `asyncio` event loop, single replica. No multiprocessing or shared-memory concerns.

**Design property:** the `Executor` is the **only** component that calls Kalshi's order endpoints. Every other component is read-only against Kalshi or interacts purely with internal state. This makes "did the bot place a real order?" a single-file question.

## Components

Each component is a self-contained module with a narrow interface.

### `MarketDiscovery`
- **Purpose:** Translate the configured series list into a concrete watched-tickers set.
- **Behavior:** Every ~5 min, hits `GET /series/{ticker}` and `GET /markets?series_ticker=...&status=open`. Writes results to `markets` table. Emits a "watch-set changed" event to `DataFeed` on change.
- **Interface:** `async def run() -> None` (loops forever); publishes `WatchSetUpdate(tickers: set[str])`.
- **Depends on:** REST client, StateStore, config.

### `DataFeed`
- **Purpose:** Provide an in-memory orderbook for every watched ticker, kept current.
- **Behavior:**
  - Maintains a WS connection to Kalshi's orderbook channel for watched tickers.
  - Applies `snapshot` and `delta` messages to per-ticker in-memory books.
  - On sequence-number gaps: force-resync (unsubscribe → REST snapshot → resubscribe).
  - Every 60s: fetch a REST orderbook snapshot per ticker, compare to in-memory book; on drift, log + notify + force-resync.
  - Emits `OrderbookUpdate(ticker, yes_book, no_book, ts)` to downstream subscribers whenever a book changes.
- **Interface:** `async def run() -> None`; publishes `OrderbookUpdate`.
- **Depends on:** WS client, REST client, StateStore, Notifier.

### `Strategy` (v1: `ArbStrategy`)
- **Purpose:** Translate orderbook state into trade intents.
- **Behavior:**
  - Subscribes to `OrderbookUpdate`.
  - For each update, checks: `best_yes_bid + best_no_bid > 1.00 + fees.estimate(size) + min_edge`.
  - If true, computes size as `min(top_yes_qty, top_no_qty, max_size_from_config)`.
  - Emits `TradeIntent(ticker, side=BUY_YES, price=best_yes_bid, size=N, expected_edge=…)` and an equal-and-opposite `TradeIntent(side=BUY_NO, …)`.
  - Checks current positions before emitting — does not stack into a market it already holds.
- **Interface:** `class Strategy(abc.ABC)` with `async def on_update(book) -> list[TradeIntent]`. v1 implements `ArbStrategy(Strategy)`.
- **Depends on:** `fees` module (see below), StateStore (for current positions).

### `fees`
- **Purpose:** Single source of truth for fee math, isolated from strategy logic.
- **Behavior:** Pure functions. `estimate(side, price_cents, size) -> total_fee_cents` and `round_trip(price_cents, size) -> total_fee_cents`. Implements Kalshi's published fee formula (currently roughly `ceil(0.07 × size × price × (1 − price))` per side, but the exact form must be re-verified against current Kalshi docs in the implementation plan). For v1 the strategy uses a **conservative over-estimate** (round up) so misestimating fees can only block trades, never enter losing ones.
- **Interface:** stateless module-level functions; trivially unit-tested.

### `RiskManager`
- **Purpose:** Veto unsafe trade intents; trip the kill switch on aggregate harm.
- **Behavior:** For each incoming `TradeIntent`, evaluates in order:
  1. Min-edge floor (`expected_edge >= min_edge_cents`).
  2. Per-market max position (`current_position_dollars + new_dollars <= max_per_market`).
  3. Global open exposure (`sum(open_positions) + new_dollars <= max_total_exposure`).
  4. Order-rate limit (token bucket, default 30/min).
  5. Daily-loss kill switch (`today_realized_pnl > -max_daily_loss`).
  - On pass: forwards as `TradeOrder` to Executor.
  - On block: logs reason, drops intent.
  - On (5) trip: emits `KillSwitch(reason)` that the main loop catches.
- **Defaults (configurable):** `min_edge_cents=1`, `max_per_market=$50`, `max_total_exposure=$200`, `max_orders_per_min=30`, `max_daily_loss=$25`.
- **Depends on:** StateStore (positions, today's realized P&L), Notifier.

### `Executor`
- **Purpose:** The single point of contact for Kalshi's order endpoints.
- **Behavior:**
  - Receives `TradeOrder`. Generates `client_order_id` (UUIDv4).
  - Writes row to `orders` table with status `pending` *before* sending request (state-first).
  - Signs request with RSA private key (Kalshi auth scheme: `KALSHI-ACCESS-KEY` + `KALSHI-ACCESS-SIGNATURE` + `KALSHI-ACCESS-TIMESTAMP` headers; payload = `timestamp + method + path`).
  - `POST /portfolio/orders`. On 2xx → `orders.status = 'submitted'`. On 5xx/network → exponential backoff retry (max 3, same `client_order_id`). On 4xx → `orders.status = 'rejected'`, log + notify.
  - **Fill detection (v1):** REST polling of `GET /portfolio/fills` every 5s while any order is `submitted`. On new fills: write `fills` row, update `positions`, `Notifier.notify_fill()`. Upgrading to a WS fill subscription is listed under Future Work.
- **Interface:** `async def submit(order: TradeOrder) -> SubmitResult`.
- **Depends on:** signed REST client, StateStore, Notifier.

### `StateStore`
- **Purpose:** Sole source of truth across restarts. Thin wrapper around `aiosqlite`.
- **Schema (sketch):**
  - `markets(ticker, series_ticker, title, status, last_seen_ts)`
  - `orderbook_snapshots(ticker, ts, side, levels_json)` — sampled at ~1/min for future backtest replay
  - `orders(client_order_id, ticker, side, price, size, status, submitted_at, kalshi_order_id, error)`
  - `fills(fill_id, client_order_id, ticker, side, price, size, fee_cents, filled_at)`
  - `positions(ticker, side, size, avg_price, updated_at)`
  - `daily_pnl(date, realized_cents, unrealized_cents, order_count, fill_count)`
  - `shadow_intents(intent_id, ts, ticker, side, price, size, expected_edge, reasoning_json)` — only in `--shadow` mode
- **Migrations:** sqlite versioning in a `_schema_version` table; migrations applied at startup.

### `Notifier`
- **Purpose:** Out-of-band alerting for events the operator should see without reading logs.
- **Interface:**
  ```
  class Notifier(abc.ABC):
      async def notify_startup(version, mode, n_markets)
      async def notify_shutdown(reason)
      async def notify_fill(fill)
      async def notify_error(exc, context)
      async def notify_kill_switch(reason, snapshot)
      async def notify_daily_pnl(date, realized, unrealized, counts)
  ```
- **v1 implementation:** `NtfyNotifier` POSTs to `https://ntfy.rupan.dev/<topic>` with bearer token. Topic configurable.
- **Pluggable** — adding a Slack backend is one new class.

### `config.yaml`
Loaded once at startup. Schema sketch:

```yaml
mode: shadow  # shadow | demo | prod  (prod requires extra confirmation step)
kalshi:
  api_base_url: https://demo-api.kalshi.co/trade-api/v2
  ws_base_url: wss://demo-api.kalshi.co/trade-api/ws/v2
  key_id_env: KALSHI_KEY_ID
  private_key_path: /var/run/secrets/kalshi/private_key.pem
discovery:
  series: [KXHIGHNY, KXHIGHCHI]  # operator-managed list
  poll_interval_seconds: 300
feed:
  reconcile_interval_seconds: 60
  ws_reconnect_max_backoff_seconds: 60
risk:
  min_edge_cents: 1
  max_per_market_dollars: 50
  max_total_exposure_dollars: 200
  max_orders_per_minute: 30
  max_daily_loss_dollars: 25
state:
  db_path: /var/lib/predmarkbot/state.db
notify:
  ntfy_url: https://ntfy.rupan.dev
  ntfy_topic: predmarkbot
  ntfy_token_env: NTFY_TOKEN
```

Editing `config.yaml` commits to GitOps → reloader restarts the pod → bot picks up new config.

## Data flow & runtime behavior

### Startup (T+0s)
1. Process boots; reads `config.yaml`, opens (or creates) `state.db`.
2. Loads RSA private key from `private_key_path`.
3. Clock-skew self-check against `time.cloudflare.com`; skew > 5s → notify + exit.
4. Reconciliation: query Kalshi for status of any `pending`/`submitted` orders in `orders` table, update.
5. `Notifier.notify_startup(version, mode, n_markets)` → ntfy push.
6. `MarketDiscovery` runs once synchronously to populate watched-tickers set.
7. `DataFeed` opens WS, subscribes, seeds in-memory books from REST snapshots.
8. Strategy + RiskManager + Executor begin their event loops.

### Steady state — orderbook update
1. WS delivers `orderbook_delta` for `KXHIGHNY-26MAY30-T75`.
2. `DataFeed` applies delta, validates seq, emits `OrderbookUpdate`.
3. `ArbStrategy`: best YES bid 52¢, best NO bid 51¢, sum 103¢. Fees + min_edge threshold = 103¢. Emit two `TradeIntent`s, size 5.
4. `RiskManager`: all five checks pass. Forward as `TradeOrder`s.
5. `Executor`: generate `client_order_id`s, write `orders` rows (status `pending`), sign + send both POSTs in parallel. On 2xx: status → `submitted`.
6. WS delivers fill events. `Executor`: write `fills`, update `positions`. `Notifier.notify_fill()`.

### Reconciliation tick (every 60s)
1. For each watched ticker: fetch fresh REST orderbook snapshot.
2. Compare top-5 levels to in-memory book.
3. Match → debug log. Drift → warn log + `Notifier.notify_error(DriftDetected)` + force-resync.
4. If a ticker drifts >3x in 10 min, mark as "unreliable for session" and stop strategy on it (others continue).

### End of day (00:00 local)
1. `Notifier.notify_daily_pnl(date, realized, unrealized, order_count, fill_count)`.
2. Daily counters reset.

### Shutdown (SIGTERM from k8s)
1. Main loop sets `shutting_down`; new intents dropped (logged).
2. Close WS; await in-flight HTTP up to 10s budget.
3. `Notifier.notify_shutdown(reason)`.
4. SQLite commits; process exits 0.

### Kill-switch trip
1. Cancel any open Kalshi orders (best-effort).
2. Close WS cleanly.
3. Write sentinel file `state.db.killed` alongside `state.db`.
4. `Notifier.notify_kill_switch(reason, snapshot)`.
5. Exit with code 42.
6. Pod restarts; bot checks sentinel on startup, **refuses to start** while sentinel exists → manual recovery required (operator SSHes in or commits config change to delete sentinel).

## Error handling & failure modes

| Failure | Detection | Response |
|---------|-----------|----------|
| WS disconnect | heartbeat timeout / socket error | Exponential backoff reconnect (1s→60s cap); re-subscribe; reseed from REST; notify if >60s |
| WS sequence gap | non-contiguous seq number | Force-resync that ticker; warn log |
| REST/WS drift | 60s reconcile mismatch | Warn + notify + force-resync; >3x in 10min → ticker disabled for session |
| Kalshi 5xx / network | HTTP error | Retry w/ exponential backoff + `client_order_id` idempotency, max 3; then mark failed + notify |
| Kalshi 4xx | HTTP error | No retry. `orders.status = 'rejected'`, log + notify |
| Auth failure | Signed request rejected | Immediate notify + exit; not recoverable without operator action |
| SQLite write failure | Exception on write | Notify, halt new trading, drain in-flight, exit; k8s restart may recover |
| Process crash | k8s noticed | Pod restart → startup-time order reconciliation re-syncs state with Kalshi |
| Clock skew | Startup NTP check | Skew > 5s → notify + exit |
| Kill-switch trip | Daily P&L < threshold (or unrecoverable error) | Cancel open orders, sentinel file, exit 42, refuse to restart |

## Testing strategy

### Layer 1 — Unit tests (`pytest`, fast, offline)
- `Strategy` math against handcrafted orderbook fixtures (arb / no-arb / threshold / fee edge cases).
- `RiskManager` rules: each limit in isolation and combined.
- `Executor` signing: assert signatures match a known-good fixture for a known key + payload + timestamp.
- `StateStore` round-trips and migration application.
- WS message parser: feed captured Kalshi fixtures, assert `OrderbookUpdate` shape.
- Target ~60-80% line coverage on `strategy/arb.py`, `risk.py`, `state.py`, `kalshi/auth.py`, `fees.py`.

### Layer 2 — Integration tests against Kalshi demo (`@pytest.mark.integration`)
- Place-then-cancel a small order on demo; verify via `GET /portfolio/orders`.
- Open WS, subscribe to a known active demo market, receive ≥1 message in 30s, validate shape.
- REST orderbook fetch returns expected schema for a known ticker.
- Opt-in via `pytest -m integration`; run before any deployment.

### Layer 3 — Shadow mode (the most important layer)
- `--shadow` flag: everything runs except `Executor.place_order()`. Intents go to `shadow_intents` table.
- Run for ≥1 week before going live demo.
- Review: fire frequency, expected edge distribution, which risk limits were binding, WS disconnect count, drift events.
- Transition `--shadow` → demo: drop the flag. Transition demo → prod: change `api_base_url` in config.

### Layer 4 — Manual smoke test before each prod transition
A one-page checklist:
- Clock skew check
- Sign-a-request check (HMAC fixture)
- Place + cancel a tiny demo order
- Receive a WS message within 30s
- ntfy delivers to phone
After first manual run, encode as a `predmarkbot smoke` subcommand that runs on startup and reports via ntfy.

## Deployment

### Image build
- **Choice: A — GitLab CI via existing `gitlab-runner`.**
- Dockerfile lives in `~/predmarkbot/`. Multi-stage, slim Python base. Non-root user, read-only root filesystem where possible.
- GitLab CI builds + pushes on tag (registry TBD in implementation plan; reuse whatever the existing `infrastructure/automation/images/` setup uses).
- Renovate (already present in homelab) updates the image tag in the k8s manifest via PR.

### Homelab repo additions (`~/homelab/`)

```
apps/predmarkbot.yaml                              # ArgoCD Application
infrastructure/predmarkbot/
  namespace.yaml                                   # ns: predmarkbot
  deployment.yaml                                  # replicas: 1, strategy: Recreate, reloader annotation
  pvc.yaml                                         # Longhorn RWO ~2Gi
  configmap.yaml                                   # config.yaml
  networkpolicy.yaml                               # egress: kalshi + ntfy + DNS only
secrets/predmarkbot.yaml                           # sealed-secret: KALSHI_KEY_ID, KALSHI_PRIVATE_KEY, NTFY_TOKEN
```

### Deployment manifest specifics
- **Namespace:** dedicated `predmarkbot` (clear blast radius, isolated NetworkPolicy).
- **Deployment:** `replicas: 1`, `strategy: Recreate`, `restartPolicy: Always`, `reloader.stakater.com/auto: "true"`.
- **PVC:** Longhorn `ReadWriteOnce`, ~2 GiB, mounted at `/var/lib/predmarkbot/`. Labels `recurring-job.longhorn.io/source: enabled` and `recurring-job-group.longhorn.io/app-configs: enabled` for daily backups.
- **Secret (sealed):** `KALSHI_KEY_ID`, `KALSHI_PRIVATE_KEY` (PEM), `NTFY_TOKEN`. Private key projected as a file at `/var/run/secrets/kalshi/private_key.pem`; others as env vars.
- **ConfigMap:** `config.yaml` projected at `/etc/predmarkbot/config.yaml`. Edits go through GitOps → reloader → pod restart.
- **NetworkPolicy:** egress allowed only to `api.elections.kalshi.com:443`, `demo-api.kalshi.co:443`, `ntfy.rupan.dev:443`, and DNS. No ingress required.
- **Resources:** `requests: cpu=100m, memory=128Mi`; `limits: cpu=500m, memory=256Mi`.
- **ArgoCD Application:** standard pattern in `apps/predmarkbot.yaml`.

### Source repo (`~/predmarkbot`) layout

```
~/predmarkbot/
├── flake.nix                                      # already exists
├── pyproject.toml                                 # uv (preferred) or poetry — TBD in plan
├── Dockerfile
├── config.example.yaml                            # template; real config lives in homelab repo
├── src/predmarkbot/
│   ├── __main__.py                                # entry: python -m predmarkbot {run|status|smoke}
│   ├── config.py
│   ├── kalshi/
│   │   ├── auth.py                                # RSA signing (KALSHI-ACCESS-* headers)
│   │   ├── rest.py                                # signed REST client (httpx.AsyncClient)
│   │   └── ws.py                                  # WS client (websockets lib)
│   ├── discovery.py
│   ├── feed.py
│   ├── strategy/
│   │   ├── base.py                                # abstract Strategy + TradeIntent
│   │   └── arb.py                                 # ArbStrategy v1
│   ├── risk.py
│   ├── executor.py
│   ├── state.py
│   ├── notify.py
│   └── cli.py                                     # status subcommand
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── .gitignore                                     # state.db, *.log, *.pem, bot.txt, .direnv/
└── README.md
```

### Secret cleanup (immediate prerequisite)
- Add `.gitignore` to `~/predmarkbot` covering `*.pem`, `bot.txt`, `state.db`, `*.log`, `.direnv/` **before any `git init` / first commit.**
- Rename `bot.txt` to a `.pem` file with the correct extension; keep it out of git. Or rotate the key with Kalshi if leaking is suspected.
- Production: key sealed into `~/homelab/secrets/predmarkbot.yaml`.
- Local dev: path referenced via `.envrc` (e.g. `export KALSHI_PRIVATE_KEY_PATH=~/.config/kalshi/dev.pem`).

## Future work (explicitly out of v1)

- **Additional strategies.** The `Strategy` interface is designed for this; expected next strategies include:
  - Calendar/correlated-market arb (e.g. monthly highs implied by daily highs)
  - News-driven entry (external signal + Kalshi)
  - Light market-making in low-volume markets
- **Backtesting.** Operator plans to add forecasting strategies for which historical replay is essential. The `orderbook_snapshots` table is sized at 1/min sampling specifically so we can replay against it later. A future `predmarkbot backtest --strategy=X --from=… --to=…` subcommand would feed snapshots into `Strategy.on_update` and tally fills against a simulated executor. (Note: snapshot sampling rate may need to increase for fine-grained backtests; consider keeping a separate higher-frequency table or upgrading the sample interval to 10s once disk usage is understood.)
- **Web dashboard.** Out of scope while ntfy + logs + `status` CLI suffice.
- **WebSocket fill subscription** (vs polling) — optimization, defer until needed.
- **Multi-venue arbitrage** (Kalshi vs Polymarket vs PredictIt) — substantially larger project.
- **Backtesting infrastructure** decision points to revisit later: snapshot retention policy, snapshot compression, separate read-replica DB for backtest queries so they don't lock the live writer.
- **Auto-strategy disable on poor performance.** RiskManager could auto-disable a strategy whose 7-day P&L is negative.

## Open items (resolve in implementation plan)

1. **Image registry choice.** The existing `infrastructure/automation/images/` setup uses GitLab CI; need to confirm the destination registry (GitLab registry, Docker Hub, or other) and copy that pattern.
2. **Python packaging tool.** `uv` (lighter, faster) vs `poetry` (more mature). Recommendation: `uv`, matching modern Python defaults.
3. **Kalshi demo API base URL.** Confirm the exact URL — believed to be `https://demo-api.kalshi.co/trade-api/v2` but should be verified against current Kalshi docs in the plan phase.
4. **Kalshi auth scheme details.** Signature payload format and required headers should be re-verified against current docs (Kalshi has changed this at least once historically).
5. **WS reconnect on auth-protected channels.** v1 only subscribes to public orderbook channels, but if future strategies need private channels (fill events via WS rather than polling), auth on WS needs design.
6. **ntfy topic naming + access.** Pick a topic name (`predmarkbot`?), confirm the token has access, decide whether to use ntfy priority levels (high for kill-switch, default for fills).
