# LongshotStrategy — Design

**Date:** 2026-06-01
**Author:** rsunderapand@wisc.edu
**Status:** Draft — pending user review

## Overview

Codify the empirical finding from the 2026-06-01 favorite-longshot report (1978 KXHIGH* weather markets, +1413 bps gap between realized and expected YES resolution rates in the 0-5¢ price bucket at T-24h and T-6h horizons) as a live trading strategy. The strategy buys YES on qualifying weather threshold markets currently priced ≤5¢ that close between 1h and 24h from now.

Ships behind the existing `mode: shadow` safety: in shadow mode the strategy records would-be trades to `shadow_intents` without placing orders. Flipping to `mode: demo` (no code change, just `configmap.yaml`) starts placing fake-money orders against Kalshi demo; `mode: prod` (also config-only) trades real money.

The existing `ArbStrategy` is **deleted outright**. It never produced meaningful signal on the markets we watch (YES+NO bid sum on weather markets is well below the $1.00+fees threshold), and keeping a dormant alternative complicates the codebase.

## Goals

1. Translate the research finding into a live-runnable `Strategy` subclass with no degradation in behavior between shadow and live modes.
2. Wire enough market metadata (close_ts) into the strategy layer that the entry rule's time window can be evaluated on each `OrderbookUpdate`.
3. Multi-layer idempotency: the strategy emits each market once per run; the runner builds a deterministic `client_order_id`; Executor's existing dedupe blocks restart-after-crash double-submission.
4. Risk-managed: every intent flows through `RiskManager` exactly as today. No new RiskManager logic; the existing `min_edge_cents` / `max_per_market_dollars` / `max_total_exposure_dollars` / `max_orders_per_minute` / `max_daily_loss_dollars` continue to gate the strategy.
5. Configuration-driven universe: which series to trade is a `configmap.yaml` edit, not a redeploy.

## Non-goals

- Live win-rate learning. `historical_yes_rate=0.14` is a static config constant.
- Backtest harness. The Plan 4 research is the backtest.
- Position-cache wiring. RiskManager's `get_position_dollars=lambda _t: 0` simplification stays; per-market cap won't actually trip in v1. The in-strategy dedupe + deterministic `client_order_id` cover the realistic case for the sizes we trade at.
- Coexistence with `ArbStrategy`. ArbStrategy is removed; only one strategy runs.
- `--strategy` CLI flag. The config file is the strategy selector. Future strategies will need a discriminator + factory, but not now.

## Decisions

| # | Question | Decision | Rationale |
|---|----------|----------|-----------|
| 1 | Market universe | Config-driven allowlist; default = 6 KXHIGH* + 5 KXLOW* = 11 series | KXHIGH* validated by report; KXLOW* extrapolated from symmetric mechanism |
| 2 | Entry rule | Time-window (T-24h < close_ts−now < T-1h) + YES bid ≤ 5¢; one intent per market per run; entry at YES ask | Encodes the regime where the report measured the bias |
| 3 | Sizing | 5 contracts per intent (= 25¢ exposure per market at 5¢ entry; ≈ $7.50/day at ~30 entries) | Conservative enough for early demo; meaningful enough for PnL signal |
| 3 | Idempotency | In-strategy `set[str]` dedupe + deterministic `client_order_id = f"longshot-{ticker}"` | Restart-safe; relies on existing Executor dedupe loop |
| 4a | Multi-strategy | Delete ArbStrategy entirely | Operator confirmed; ArbStrategy never produced signal on watched markets |
| 4b | Market metadata | Inject `get_market_meta` callback into the strategy, backed by a `MarketMetaCache` populated from REST at startup + refreshed each MarketDiscovery tick | Mirrors existing `get_position` callback pattern; doesn't touch `OrderbookUpdate` |

## Architecture

```
src/predmarkbot/
├── events.py           # MODIFY  add MarketMeta dataclass
├── config.py           # MODIFY  replace risk.max_intent_size centric block
│                       #         with new `strategy:` block
├── strategy/
│   ├── base.py         # UNCHANGED  Strategy ABC
│   ├── arb.py          # DELETE
│   └── longshot.py     # NEW   LongshotStrategy class
├── market_meta.py      # NEW   MarketMetaCache helper
├── runner.py           # MODIFY  build LongshotStrategy + MarketMetaCache;
│                       #         drop ArbStrategy import; deterministic
│                       #         client_order_id; new meta_refresh task
└── (other modules unchanged)

tests/unit/
├── test_strategy_arb.py   # DELETE
└── test_longshot.py       # NEW   9 unit tests
```

The strategy interface (`async on_update(OrderbookUpdate) -> list[TradeIntent]`) is **unchanged**. The bot's pipeline (`feed → strategy → risk → executor → notifier`) is **unchanged**. The only structural addition is the `MarketMetaCache` + an additional asyncio task that refreshes it.

## Components

### `events.py` — `MarketMeta` dataclass

Frozen dataclass added next to the existing event types:

```python
@dataclass(frozen=True)
class MarketMeta:
    ticker: str
    series_ticker: str
    close_ts: datetime
    yes_strike: float | None
```

### `market_meta.py` — `MarketMetaCache`

```python
class MarketMetaCache:
    def __init__(self, *, rest: KalshiRestClient) -> None: ...

    async def refresh(self, tickers: Iterable[str]) -> None:
        """Fetch each ticker's metadata, populate the cache. Idempotent —
        only NEW tickers actually hit the API."""

    def get(self, ticker: str) -> MarketMeta | None:
        """Sync lookup — no network. Returns None for unknown tickers."""
```

- Holds a `dict[str, MarketMeta]` keyed by ticker.
- Network calls happen ONLY in `refresh()`. The strategy uses `.get()` synchronously per `OrderbookUpdate`.
- Per-ticker fetch reads Kalshi's `/markets/{ticker}` response; extracts `close_time` (the actual Kalshi field, normalized to UTC ISO), `series_ticker` (with the same fallback-to-prefix-of-ticker as the research fetcher), `floor_strike` (Kalshi's actual strike field, not `yes_strike`).
- On per-ticker fetch failure: logs WARN, leaves the cache entry absent. Strategy's filter step 1 (meta is None) short-circuits silently.

### `strategy/longshot.py` — `LongshotStrategy`

Constructor:

```python
LongshotStrategy(
    *,
    series_allowlist: set[str],
    size_contracts: int,
    max_price_cents: int,           # only emit when YES bid <= this
    min_seconds_to_close: int,
    max_seconds_to_close: int,
    historical_yes_rate: float,     # for expected_edge calculation
    get_market_meta: Callable[[str], MarketMeta | None],
    now: Callable[[], datetime],
)
```

Method `on_update(update)` executes these filters in order (cheap → expensive):

1. `meta = self._get_market_meta(update.ticker)` — None → return `[]`
2. `meta.series_ticker not in self._series_allowlist` → `[]`
3. `update.ticker in self._already_emitted` (in-memory `set[str]`) → `[]`
4. `update.yes.top_bid()` is None OR price > `max_price_cents` → `[]`
5. `seconds_to_close = (meta.close_ts − now()).total_seconds()` — outside `[min, max]` → `[]`
6. **Build intent.** Entry price:
   - `yes_top_ask` is computed from the NO side: `yes_ask_price = 100 − no_top_bid_price` if a NO bid exists, else `yes_bid_price + 1`.
   - Capped at `max_price_cents`.
   - `expected_edge_cents = round(historical_yes_rate * (100 − enter_price) − (1 − historical_yes_rate) * enter_price)`
   - Add to `self._already_emitted`, return list of one `TradeIntent`.

The `_already_emitted` set is in-memory only; cleared on process restart. Restart safety comes from layer 2 (Executor's deterministic `client_order_id` dedupe).

### `config.py` — `StrategyConfig`

New pydantic model. Replaces the existing arb-shaped block.

```python
class StrategyConfig(BaseModel):
    type: Literal["longshot"] = "longshot"
    size_contracts: int = 5
    max_price_cents: int = 5
    min_seconds_to_close: int = 3600        # 1 hour
    max_seconds_to_close: int = 86400       # 24 hours
    historical_yes_rate: float = 0.14
    series_allowlist: list[str] = Field(default_factory=lambda: [
        "KXHIGHNY", "KXHIGHCHI", "KXHIGHLAX", "KXHIGHMIA", "KXHIGHDEN", "KXHIGHHOU",
        "KXLOWNY", "KXLOWCHI", "KXLOWLAX", "KXLOWMIA", "KXLOWDEN",
    ])
```

Top-level `Config` gets a `strategy: StrategyConfig` field. The pre-existing `risk.max_intent_size` field stays — it's a generic per-intent cap that future strategies may use; LongshotStrategy ignores it and uses `size_contracts`.

`config.example.yaml` is updated to show the new block.

### `runner.py` changes

1. Drop `from predmarkbot.strategy.arb import ArbStrategy`. Add `from predmarkbot.strategy.longshot import LongshotStrategy` and `from predmarkbot.market_meta import MarketMetaCache`.
2. After `discovery.discover_once()` returns `watched`:
   ```python
   meta_cache = MarketMetaCache(rest=rest)
   await meta_cache.refresh(watched)
   ```
3. Build strategy:
   ```python
   strategy = LongshotStrategy(
       series_allowlist=set(config.strategy.series_allowlist),
       size_contracts=config.strategy.size_contracts,
       max_price_cents=config.strategy.max_price_cents,
       min_seconds_to_close=config.strategy.min_seconds_to_close,
       max_seconds_to_close=config.strategy.max_seconds_to_close,
       historical_yes_rate=config.strategy.historical_yes_rate,
       get_market_meta=meta_cache.get,
       now=lambda: datetime.now(UTC),
   )
   ```
4. Add a fourth asyncio task that refreshes the cache periodically:
   ```python
   async def _meta_refresh_loop():
       while True:
           await asyncio.sleep(config.discovery.poll_interval_seconds)
           watched_now = await discovery.discover_once()
           await meta_cache.refresh(watched_now)
   ```
5. In `_drain_updates`, replace the `uuid.uuid4()` based `client_order_id` with:
   ```python
   client_order_id = f"longshot-{intent.ticker}"
   ```
   (Each Kalshi market ticker is already unique per (series, date, threshold), so we don't need additional disambiguation.)

### Wiring summary

The four asyncio tasks gathered in `run()`:
1. `feed.consume(ws.messages())` — existing
2. `_drain_updates(...)` — existing (modified to use deterministic `client_order_id`)
3. `_daily_pnl_loop(...)` — existing
4. `_meta_refresh_loop(...)` — NEW

## Data flow

```
Kalshi WS orderbook event arrives
            │
            ▼
        DataFeed emits OrderbookUpdate
            │
            ▼
     LongshotStrategy.on_update(update)
            │
            ├── meta_cache.get(ticker)  ←─── MarketMetaCache (refreshed every 5 min from /markets/{ticker})
            ├── allowlist check
            ├── dedupe set check
            ├── price + time-window filter
            ▼
        TradeIntent (zero or one)
            │
            ▼
     RiskManager.evaluate(intent)
            │
            ├── PASS  ─────────────────────► record_shadow_intent (shadow) OR
            │                                build TradeOrder with
            │                                client_order_id="longshot-{ticker}"
            │                                → Executor.submit
            ├── BLOCK_* ───────────────────► log + drop
            └── KILL_SWITCH ───────────────► raise _KillSwitchSignal
```

## Math

For a YES purchase at price P¢ with historical yes-resolution rate r (= 0.14):

```
Per-contract expected payoff =  r * (100 − P)  −  (1 − r) * P    (cents)
                              =  100r − Pr − P + Pr
                              =  100r − P
```

So `expected_edge_cents = round(100 * r − P)`. At r=0.14 and P=5¢, edge = 9¢. At r=0.14 and P=1¢, edge = 13¢. After ~2¢ Kalshi fees on a fill, net edge is roughly 7-11¢ per contract.

This is well above `RiskManager.min_edge_cents=1` (the default risk gate), so the gate never trips on real LongshotStrategy intents. It does mean a misconfigured `historical_yes_rate < P/100` would correctly produce negative edges and get blocked.

## Error handling

| Failure | Response |
|---|---|
| Market not in `meta_cache` (newly discovered, refresh hasn't run yet) | Strategy returns `[]`. Next refresh tick adds it. |
| `meta.close_ts` is in the past (market is mid-resolution) | Strategy's time-window filter returns `[]` (close_ts − now < min_seconds_to_close). |
| `update.yes.top_bid()` returns None (no current bid at all) | Strategy returns `[]`. |
| `update.no.top_bid()` returns None (can't compute YES ask from NO bid) | Fall back to `enter_price = yes_bid_price + 1` (one tick above bid). |
| `historical_yes_rate` is misconfigured (e.g. 0.0) so expected_edge_cents is negative | RiskManager's `BLOCK_LOW_EDGE` rejects the intent. Logged for operator. |
| MarketMetaCache.refresh() partial failure (some tickers 4xx) | Logged per-ticker; cache stays partially populated; strategy ignores affected tickers. |
| Process crash mid-day | k8s restarts the pod; `_already_emitted` is empty but Executor's persistent dedupe (via `client_order_id`) prevents reorder for tickers already submitted today. |

## Testing

### Layer 1 — Unit tests (`tests/unit/test_longshot.py`)

Nine tests, each handcrafts an `OrderbookUpdate` + stub callbacks. No async DB, no respx, no network.

1. `test_emits_intent_on_qualifying_market` — happy path, allowlist match, YES 3¢, 12h to close → 1 intent at YES ask, size=5.
2. `test_skips_market_not_in_allowlist` — KXBTC ticker → `[]`.
3. `test_skips_meta_unknown` — `get_market_meta` returns None → `[]`.
4. `test_skips_price_above_threshold` — YES bid 6¢ > 5¢ max → `[]`.
5. `test_skips_too_close_to_expiry` — 30 min to close < 1h min → `[]`.
6. `test_skips_too_far_from_expiry` — 48h to close > 24h max → `[]`.
7. `test_dedupes_per_run` — same ticker called twice with qualifying conditions → 1 intent, then `[]`.
8. `test_uses_yes_ask_as_entry_price` — YES bid 2¢, NO bid 96¢ → YES ask = 4¢; intent price = 4¢.
9. `test_expected_edge_math` — checks `expected_edge_cents == round(100*r − P)`.

Target ~85% line coverage on `longshot.py`.

### Layer 2 — Integration / smoke

- `tests/unit/test_market_meta.py` — small respx-based test for `MarketMetaCache.refresh`: mocks `/markets/X-1`, asserts cache has the right entry, asserts repeated call doesn't re-fetch.
- `tests/integration/test_e2e_shadow.py` already exists and asserts `orders == 0` after a shadow run. **Same test continues to validate post-strategy-swap**; rename the comment if needed.

### Deletion verification

After deleting `ArbStrategy`:
- `grep -r "ArbStrategy\|arb_strategy\|strategy.arb" src/ tests/` should return zero matches.
- `pytest tests/` should pass (114 tests minus the deleted `test_strategy_arb.py` ones = ~109 + the 9 new = 118).

## Future work (explicitly out of scope)

- **Position cache wiring.** RiskManager's `get_position_dollars` is still stubbed to 0 — fine for v1, blocks deeper risk-managed strategies later.
- **Multi-strategy support** with a `strategy.type` discriminator + factory in the runner. Today the runner instantiates `LongshotStrategy` directly. Future plans that introduce strategy #2 will refactor this.
- **Live win-rate adaptation.** The strategy's `historical_yes_rate` is static. A future enhancement could compute a rolling realized win rate from `state.db`'s `fills` table and update the strategy's internal estimate. Risky (overfitting bias chasing) and out of scope.
- **Plan 6 — extend research to multi-day markets** where the multi-horizon stratification CAN populate. Operator-confirmed as the next research project after this strategy is in shadow.

## Open items (resolve in implementation plan)

1. **`/markets/{ticker}` rate-limit cost** — the cache populates with one REST call per ticker, sustained at ~5 req/s during refresh. For 30 tickers, that's ~6s per refresh. Acceptable; document.
2. **`close_time` vs `expected_expiration_time` vs `latest_expiration_time`** — the Plan 4 prod dump showed three time-ish fields. We use `close_time` (matches what the research pipeline already trusts). Verify during implementation that markets' close_time is what actually matters for the time-window filter; if not, switch.
3. **Reading `floor_strike`** — already handled in the research fetcher post-Plan 4. MarketMetaCache reuses the same fallback chain (`m.get("yes_strike") or m.get("floor_strike")`).
4. **Should the meta-refresh task block on the first refresh?** Currently the design has runner do `await meta_cache.refresh(watched)` synchronously at startup. If that fetch hits 429s, the bot can't start. Alternative: spawn refresh as a background task, accept that the strategy returns `[]` for the first N seconds. Recommendation: keep the synchronous startup refresh (early failure is better than silent no-trade).
