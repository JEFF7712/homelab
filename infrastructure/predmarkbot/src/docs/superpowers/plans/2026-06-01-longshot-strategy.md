# LongshotStrategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `ArbStrategy` with `LongshotStrategy` — a new `Strategy` subclass that buys YES on allowlisted weather-threshold markets when YES bid ≤ 5¢ and the market closes between 1h and 24h from now. Plus the supporting `MarketMetaCache` for close_ts lookups and the runner-side wiring (deterministic `client_order_id`, new meta-refresh task).

**Architecture:** New `predmarkbot.strategy.longshot.LongshotStrategy` + new `predmarkbot.market_meta.MarketMetaCache`. Runner builds the cache from Kalshi REST at startup, refreshes per `discovery` tick, injects a sync `get_market_meta` callback into the strategy. The strategy filter chain runs cheap-first; in-memory dedupe + deterministic `client_order_id="longshot-{ticker}"` together cover restart safety. `ArbStrategy` is deleted outright.

**Tech Stack:** Python 3.12, existing deps (`aiosqlite`, `httpx`, `pydantic`). No new packages.

---

## File structure

```
src/predmarkbot/
├── events.py              # MODIFY  add MarketMeta dataclass
├── config.py              # MODIFY  add StrategyConfig; Config gets `strategy` field
├── market_meta.py         # NEW     MarketMetaCache class
├── runner.py              # MODIFY  build LongshotStrategy + MarketMetaCache;
│                          #         deterministic client_order_id; meta_refresh task
├── strategy/
│   ├── base.py            # UNCHANGED
│   ├── arb.py             # DELETE
│   └── longshot.py        # NEW
└── (other modules unchanged)

tests/unit/
├── test_strategy_arb.py   # DELETE
├── test_events.py         # MODIFY  append MarketMeta tests
├── test_config.py         # MODIFY  append StrategyConfig tests
├── test_market_meta.py    # NEW
└── test_longshot.py       # NEW

config.example.yaml        # MODIFY  replace old strategy-shaped block with new `strategy:` block

~/homelab/infrastructure/predmarkbot/
└── configmap.yaml         # MODIFY  same `strategy:` block (deployment-side)
```

**Conventions used throughout this plan:**
- All commands run inside the nix dev shell. Prefix with `nix develop --command` when invoking from outside an active shell.
- TDD per task: write failing test → run → fail → implement → run → pass → lint → commit.
- Tests use `pytest` + `pytest-asyncio` + `respx` (for HTTP mocks). Already in deps.

---

## Task 1: `MarketMeta` dataclass in `events.py`

**Files:**
- Modify: `src/predmarkbot/events.py` (append `MarketMeta`)
- Modify: `tests/unit/test_events.py` (append one test)

- [ ] **Step 1: Append failing test**

`tests/unit/test_events.py`:

```python
def test_market_meta_carries_close_ts_and_strike() -> None:
    from predmarkbot.events import MarketMeta
    m = MarketMeta(
        ticker="KXHIGHNY-26JUN10-T75",
        series_ticker="KXHIGHNY",
        close_ts=datetime(2026, 6, 11, 4, 59, tzinfo=UTC),
        yes_strike=75.0,
    )
    assert m.ticker == "KXHIGHNY-26JUN10-T75"
    assert m.series_ticker == "KXHIGHNY"
    assert m.yes_strike == 75.0
    # frozen
    with pytest.raises(Exception):
        m.ticker = "other"  # type: ignore[misc]
```

- [ ] **Step 2: Confirm failure**

```bash
nix develop --command uv run pytest tests/unit/test_events.py::test_market_meta_carries_close_ts_and_strike -v
```

Expected: ImportError on `MarketMeta`.

- [ ] **Step 3: Append the dataclass to `src/predmarkbot/events.py`**

```python
@dataclass(frozen=True)
class MarketMeta:
    ticker: str
    series_ticker: str
    close_ts: datetime
    yes_strike: float | None
```

- [ ] **Step 4: Verify passing + lint clean**

```bash
nix develop --command uv run pytest tests/unit/test_events.py -v
nix develop --command uv run ruff check src tests
nix develop --command uv run mypy src
```

Expected: all 7 events tests pass; ruff + mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/predmarkbot/events.py tests/unit/test_events.py
git commit -m "feat(events): MarketMeta dataclass (ticker, series, close_ts, yes_strike)"
```

---

## Task 2: `StrategyConfig` in `config.py`

**Files:**
- Modify: `src/predmarkbot/config.py`
- Modify: `tests/unit/test_config.py`
- Modify: `config.example.yaml`

- [ ] **Step 1: Append failing tests to `tests/unit/test_config.py`**

```python
def test_strategy_config_defaults(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(dedent("""
        mode: shadow
        kalshi:
          api_base_url: https://demo-api.kalshi.co/trade-api/v2
          ws_base_url: wss://demo-api.kalshi.co/trade-api/ws/v2
          key_id_env: KALSHI_KEY_ID
          private_key_path: /tmp/key.pem
        discovery:
          series: [KXHIGHNY]
        notify:
          ntfy_url: https://ntfy.rupan.dev
          ntfy_topic: predmarkbot
          ntfy_token_env: NTFY_TOKEN
        state:
          db_path: /tmp/state.db
    """))
    cfg = load_config(cfg_path)
    assert cfg.strategy.type == "longshot"
    assert cfg.strategy.size_contracts == 5
    assert cfg.strategy.max_price_cents == 5
    assert cfg.strategy.min_seconds_to_close == 3600
    assert cfg.strategy.max_seconds_to_close == 86400
    assert cfg.strategy.historical_yes_rate == 0.14
    assert "KXHIGHNY" in cfg.strategy.series_allowlist
    assert "KXLOWNY" in cfg.strategy.series_allowlist


def test_strategy_config_explicit_override(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(dedent("""
        mode: shadow
        kalshi:
          api_base_url: https://demo-api.kalshi.co/trade-api/v2
          ws_base_url: wss://demo-api.kalshi.co/trade-api/ws/v2
          key_id_env: KALSHI_KEY_ID
          private_key_path: /tmp/key.pem
        discovery:
          series: [KXHIGHNY]
        strategy:
          size_contracts: 20
          max_price_cents: 3
          series_allowlist: [KXHIGHNY, KXHIGHCHI]
        notify:
          ntfy_url: https://ntfy.rupan.dev
          ntfy_topic: predmarkbot
          ntfy_token_env: NTFY_TOKEN
        state:
          db_path: /tmp/state.db
    """))
    cfg = load_config(cfg_path)
    assert cfg.strategy.size_contracts == 20
    assert cfg.strategy.max_price_cents == 3
    assert cfg.strategy.series_allowlist == ["KXHIGHNY", "KXHIGHCHI"]
```

- [ ] **Step 2: Confirm failure**

```bash
nix develop --command uv run pytest tests/unit/test_config.py -v -k strategy
```

Expected: 2 failed — `Config` has no `strategy` attribute.

- [ ] **Step 3: Add `StrategyConfig` to `src/predmarkbot/config.py`**

Above the existing `Config` class:

```python
class StrategyConfig(BaseModel):
    type: Literal["longshot"] = "longshot"
    size_contracts: int = 5
    max_price_cents: int = 5
    min_seconds_to_close: int = 3600
    max_seconds_to_close: int = 86400
    historical_yes_rate: float = 0.14
    series_allowlist: list[str] = Field(default_factory=lambda: [
        "KXHIGHNY", "KXHIGHCHI", "KXHIGHLAX", "KXHIGHMIA", "KXHIGHDEN", "KXHIGHHOU",
        "KXLOWNY", "KXLOWCHI", "KXLOWLAX", "KXLOWMIA", "KXLOWDEN",
    ])
```

Add `from typing import Literal` to the imports if not already present.

Add a field to the `Config` model:

```python
class Config(BaseModel):
    mode: Mode
    kalshi: KalshiConfig
    discovery: DiscoveryConfig
    feed: FeedConfig = Field(default_factory=FeedConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)   # NEW
    state: StateConfig
    notify: NotifyConfig
    prod_confirmed: bool = False
    # ... existing validator ...
```

- [ ] **Step 4: Update `config.example.yaml`**

Add this block immediately after the existing `risk:` block:

```yaml
strategy:
  type: longshot
  size_contracts: 5
  max_price_cents: 5                  # enter when YES bid <= this
  min_seconds_to_close: 3600          # 1 hour
  max_seconds_to_close: 86400         # 24 hours
  historical_yes_rate: 0.14           # from the 2026-06-01 report
  series_allowlist:
    - KXHIGHNY
    - KXHIGHCHI
    - KXHIGHLAX
    - KXHIGHMIA
    - KXHIGHDEN
    - KXHIGHHOU
    - KXLOWNY
    - KXLOWCHI
    - KXLOWLAX
    - KXLOWMIA
    - KXLOWDEN
```

- [ ] **Step 5: Verify**

```bash
nix develop --command uv run pytest tests/unit/test_config.py -v
nix develop --command uv run ruff check src tests
nix develop --command uv run mypy src
```

Expected: 5 config tests pass (3 existing + 2 new); ruff/mypy clean.

- [ ] **Step 6: Commit**

```bash
git add src/predmarkbot/config.py tests/unit/test_config.py config.example.yaml
git commit -m "feat(config): StrategyConfig with longshot defaults + 11-series allowlist"
```

---

## Task 3: `MarketMetaCache` in `market_meta.py`

**Files:**
- Create: `src/predmarkbot/market_meta.py`
- Create: `tests/unit/test_market_meta.py`

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_market_meta.py`

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest
import respx
from httpx import Response

from predmarkbot.kalshi.rest import KalshiRestClient
from predmarkbot.market_meta import MarketMetaCache


@pytest.mark.asyncio
@respx.mock
async def test_refresh_populates_cache() -> None:
    base = "https://demo-api.kalshi.co/trade-api/v2"
    respx.get(f"{base}/markets/KXHIGHNY-26JUN10-T75").respond(json={
        "market": {
            "ticker": "KXHIGHNY-26JUN10-T75",
            "series_ticker": "KXHIGHNY",
            "close_time": "2026-06-11T04:59:00Z",
            "floor_strike": 75,
        }
    })
    async with KalshiRestClient(base_url=base, signer=None) as rest:
        cache = MarketMetaCache(rest=rest)
        await cache.refresh(["KXHIGHNY-26JUN10-T75"])
        meta = cache.get("KXHIGHNY-26JUN10-T75")
    assert meta is not None
    assert meta.ticker == "KXHIGHNY-26JUN10-T75"
    assert meta.series_ticker == "KXHIGHNY"
    assert meta.close_ts == datetime(2026, 6, 11, 4, 59, tzinfo=UTC)
    assert meta.yes_strike == 75.0


@pytest.mark.asyncio
@respx.mock
async def test_refresh_idempotent_skips_existing() -> None:
    base = "https://demo-api.kalshi.co/trade-api/v2"
    route = respx.get(f"{base}/markets/KXHIGHNY-26JUN10-T75").respond(json={
        "market": {
            "ticker": "KXHIGHNY-26JUN10-T75",
            "series_ticker": "KXHIGHNY",
            "close_time": "2026-06-11T04:59:00Z",
            "floor_strike": 75,
        }
    })
    async with KalshiRestClient(base_url=base, signer=None) as rest:
        cache = MarketMetaCache(rest=rest)
        await cache.refresh(["KXHIGHNY-26JUN10-T75"])
        await cache.refresh(["KXHIGHNY-26JUN10-T75"])  # second call should skip
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_refresh_handles_failures_quietly() -> None:
    base = "https://demo-api.kalshi.co/trade-api/v2"
    respx.get(f"{base}/markets/X-BAD").respond(404, json={"error": "no"})
    respx.get(f"{base}/markets/X-GOOD").respond(json={
        "market": {
            "ticker": "X-GOOD",
            "series_ticker": "KXHIGHNY",
            "close_time": "2026-06-11T04:59:00Z",
            "floor_strike": 80,
        }
    })
    async with KalshiRestClient(base_url=base, signer=None) as rest:
        cache = MarketMetaCache(rest=rest)
        await cache.refresh(["X-BAD", "X-GOOD"])
    assert cache.get("X-BAD") is None
    assert cache.get("X-GOOD") is not None


def test_get_returns_none_for_unknown_ticker() -> None:
    from unittest.mock import MagicMock
    cache = MarketMetaCache(rest=MagicMock())
    assert cache.get("never-seen") is None


@pytest.mark.asyncio
@respx.mock
async def test_refresh_falls_back_to_ticker_prefix_for_series() -> None:
    base = "https://demo-api.kalshi.co/trade-api/v2"
    respx.get(f"{base}/markets/KXHIGHNY-X").respond(json={
        "market": {
            "ticker": "KXHIGHNY-X",
            "series_ticker": None,
            "close_time": "2026-06-11T04:59:00Z",
            "floor_strike": 80,
        }
    })
    async with KalshiRestClient(base_url=base, signer=None) as rest:
        cache = MarketMetaCache(rest=rest)
        await cache.refresh(["KXHIGHNY-X"])
        meta = cache.get("KXHIGHNY-X")
    assert meta is not None
    assert meta.series_ticker == "KXHIGHNY"
```

- [ ] **Step 2: Confirm failure**

```bash
nix develop --command uv run pytest tests/unit/test_market_meta.py -v
```

Expected: ImportError on `predmarkbot.market_meta`.

- [ ] **Step 3: Implement `src/predmarkbot/market_meta.py`**

```python
"""In-memory cache of market metadata (ticker, series, close_ts, strike).

Populated by the runner at startup + on each MarketDiscovery poll tick.
The strategy reads from it synchronously per OrderbookUpdate.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from predmarkbot.events import MarketMeta
from predmarkbot.kalshi.rest import KalshiApiError, KalshiRestClient

_log = logging.getLogger(__name__)


def _parse_ts(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class MarketMetaCache:
    """Synchronous read interface, async refresh.

    `refresh()` is idempotent — already-cached tickers are skipped.
    Per-ticker fetch failures are logged at WARN; the strategy will see
    `get()` return None and short-circuit.
    """

    def __init__(self, *, rest: KalshiRestClient) -> None:
        self._rest = rest
        self._cache: dict[str, MarketMeta] = {}

    def get(self, ticker: str) -> MarketMeta | None:
        return self._cache.get(ticker)

    async def refresh(self, tickers: Iterable[str]) -> None:
        for ticker in tickers:
            if ticker in self._cache:
                continue
            try:
                data = await self._rest.get(f"/markets/{ticker}")
            except KalshiApiError as exc:
                _log.warning("market meta fetch failed for %s: %s", ticker, exc)
                continue
            m = data.get("market", data)
            raw_series = m.get("series_ticker")
            series_ticker = (
                str(raw_series) if raw_series else ticker.split("-", 1)[0]
            )
            close_raw = m.get("close_time") or m.get("expected_expiration_time")
            if not close_raw:
                _log.warning("market %s has no close_time; skipping", ticker)
                continue
            self._cache[ticker] = MarketMeta(
                ticker=ticker,
                series_ticker=series_ticker,
                close_ts=_parse_ts(str(close_raw)),
                yes_strike=_safe_float(
                    m.get("yes_strike") or m.get("floor_strike")
                ),
            )
```

- [ ] **Step 4: Verify**

```bash
nix develop --command uv run pytest tests/unit/test_market_meta.py -v
nix develop --command uv run ruff check src tests
nix develop --command uv run mypy src
```

Expected: 5 tests pass; ruff/mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/predmarkbot/market_meta.py tests/unit/test_market_meta.py
git commit -m "feat(market_meta): MarketMetaCache — sync read, async refresh, idempotent"
```

---

## Task 4: `LongshotStrategy` in `strategy/longshot.py`

**Files:**
- Create: `src/predmarkbot/strategy/longshot.py`
- Create: `tests/unit/test_longshot.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/test_longshot.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from predmarkbot.events import (
    MarketMeta,
    OrderbookSide,
    OrderbookUpdate,
    Side,
)
from predmarkbot.strategy.longshot import LongshotStrategy


_NOW = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)


def _book(
    *,
    ticker: str = "KXHIGHNY-26JUN10-T75",
    yes_bid: tuple[int, int] | None = (3, 100),
    no_bid: tuple[int, int] | None = (95, 100),
) -> OrderbookUpdate:
    return OrderbookUpdate(
        ticker=ticker,
        yes=OrderbookSide(bids=[yes_bid] if yes_bid else [], asks=[]),
        no=OrderbookSide(bids=[no_bid] if no_bid else [], asks=[]),
        ts=_NOW,
        seq=1,
    )


def _meta(
    *,
    ticker: str = "KXHIGHNY-26JUN10-T75",
    series: str = "KXHIGHNY",
    hours_to_close: float = 12,
) -> MarketMeta:
    return MarketMeta(
        ticker=ticker,
        series_ticker=series,
        close_ts=_NOW + timedelta(hours=hours_to_close),
        yes_strike=75.0,
    )


def _make_strategy(
    *,
    meta: MarketMeta | None,
    allowlist: set[str] | None = None,
    max_price_cents: int = 5,
) -> LongshotStrategy:
    return LongshotStrategy(
        series_allowlist=allowlist or {"KXHIGHNY"},
        size_contracts=5,
        max_price_cents=max_price_cents,
        min_seconds_to_close=3600,
        max_seconds_to_close=86400,
        historical_yes_rate=0.14,
        get_market_meta=lambda _t: meta,
        now=lambda: _NOW,
    )


@pytest.mark.asyncio
async def test_emits_intent_on_qualifying_market() -> None:
    s = _make_strategy(meta=_meta())
    intents = await s.on_update(_book())
    assert len(intents) == 1
    intent = intents[0]
    assert intent.side == Side.BUY_YES
    assert intent.size == 5
    assert intent.price_cents == 5  # YES ask = 100 - 95 = 5
    assert intent.expected_edge_cents == 9  # round(100*0.14 - 5) = 9


@pytest.mark.asyncio
async def test_skips_market_not_in_allowlist() -> None:
    s = _make_strategy(
        meta=_meta(series="KXBTC"),
        allowlist={"KXHIGHNY"},
    )
    intents = await s.on_update(_book())
    assert intents == []


@pytest.mark.asyncio
async def test_skips_meta_unknown() -> None:
    s = _make_strategy(meta=None)
    intents = await s.on_update(_book())
    assert intents == []


@pytest.mark.asyncio
async def test_skips_price_above_threshold() -> None:
    s = _make_strategy(meta=_meta())
    intents = await s.on_update(_book(yes_bid=(6, 100)))
    assert intents == []


@pytest.mark.asyncio
async def test_skips_too_close_to_expiry() -> None:
    s = _make_strategy(meta=_meta(hours_to_close=0.5))  # 30 min
    intents = await s.on_update(_book())
    assert intents == []


@pytest.mark.asyncio
async def test_skips_too_far_from_expiry() -> None:
    s = _make_strategy(meta=_meta(hours_to_close=48))  # 48h
    intents = await s.on_update(_book())
    assert intents == []


@pytest.mark.asyncio
async def test_dedupes_per_run() -> None:
    s = _make_strategy(meta=_meta())
    first = await s.on_update(_book())
    second = await s.on_update(_book())
    assert len(first) == 1
    assert second == []


@pytest.mark.asyncio
async def test_uses_yes_ask_as_entry_price() -> None:
    # YES bid 2, NO bid 96 -> YES ask = 100 - 96 = 4
    s = _make_strategy(meta=_meta())
    intents = await s.on_update(_book(yes_bid=(2, 100), no_bid=(96, 100)))
    assert len(intents) == 1
    assert intents[0].price_cents == 4


@pytest.mark.asyncio
async def test_expected_edge_math_at_one_cent_entry() -> None:
    s = _make_strategy(meta=_meta())
    # YES bid 1, NO bid 98 -> YES ask = 2
    intents = await s.on_update(_book(yes_bid=(1, 100), no_bid=(98, 100)))
    assert len(intents) == 1
    assert intents[0].price_cents == 2
    # edge = round(100 * 0.14 - 2) = round(12) = 12
    assert intents[0].expected_edge_cents == 12
```

- [ ] **Step 2: Confirm failure**

```bash
nix develop --command uv run pytest tests/unit/test_longshot.py -v
```

Expected: ImportError on `predmarkbot.strategy.longshot`.

- [ ] **Step 3: Implement `src/predmarkbot/strategy/longshot.py`**

```python
"""LongshotStrategy — buy YES on out-of-the-money weather threshold markets.

Codifies the 2026-06-01 favorite-longshot research finding (+1413 bps
realized-vs-expected gap on 1978 KXHIGH* markets in the 0-5¢ bucket).
Emits one intent per market per run on the cheap-first filter chain.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from predmarkbot.events import MarketMeta, OrderbookUpdate, Side, TradeIntent
from predmarkbot.strategy.base import Strategy


class LongshotStrategy(Strategy):
    """Buy YES when:
    - market's series is in `series_allowlist`
    - YES top bid <= `max_price_cents`
    - close_ts is between `min_seconds_to_close` and `max_seconds_to_close` from now
    - we haven't already emitted for this ticker in this run

    Entry price = YES top ask (derived as 100 - NO top bid), capped at
    `max_price_cents`. Falls back to `yes_bid_price + 1` if NO has no bid.
    """

    def __init__(
        self,
        *,
        series_allowlist: set[str],
        size_contracts: int,
        max_price_cents: int,
        min_seconds_to_close: int,
        max_seconds_to_close: int,
        historical_yes_rate: float,
        get_market_meta: Callable[[str], MarketMeta | None],
        now: Callable[[], datetime],
    ) -> None:
        self._allowlist = series_allowlist
        self._size = size_contracts
        self._max_price = max_price_cents
        self._min_secs = min_seconds_to_close
        self._max_secs = max_seconds_to_close
        self._yes_rate = historical_yes_rate
        self._get_meta = get_market_meta
        self._now = now
        self._already_emitted: set[str] = set()

    async def on_update(self, update: OrderbookUpdate) -> list[TradeIntent]:
        # 1. Need market metadata
        meta = self._get_meta(update.ticker)
        if meta is None:
            return []

        # 2. Series allowlist
        if meta.series_ticker not in self._allowlist:
            return []

        # 3. Already-emitted dedupe (in-memory, per-run)
        if update.ticker in self._already_emitted:
            return []

        # 4. Price filter
        yes_top = update.yes.top_bid()
        if yes_top is None:
            return []
        yes_bid_price, _yes_qty = yes_top
        if yes_bid_price > self._max_price:
            return []

        # 5. Time-window filter
        seconds_to_close = (meta.close_ts - self._now()).total_seconds()
        if seconds_to_close < self._min_secs:
            return []
        if seconds_to_close > self._max_secs:
            return []

        # 6. Build the intent.
        # YES ask is derived from NO bid: yes_ask = 100 - no_bid.
        no_top = update.no.top_bid()
        if no_top is not None:
            no_bid_price, _ = no_top
            enter_price = 100 - no_bid_price
        else:
            enter_price = yes_bid_price + 1
        enter_price = min(enter_price, self._max_price)
        # Clamp to valid price range [1, 99] per TradeIntent validator
        enter_price = max(1, min(enter_price, 99))

        edge = round(100 * self._yes_rate - enter_price)
        intent = TradeIntent(
            ticker=update.ticker,
            side=Side.BUY_YES,
            price_cents=enter_price,
            size=self._size,
            expected_edge_cents=edge,
            reasoning=(
                f"longshot @ {enter_price}¢, "
                f"{int(seconds_to_close)}s to close, "
                f"hist_yes_rate={self._yes_rate:.3f}"
            ),
        )
        self._already_emitted.add(update.ticker)
        return [intent]
```

- [ ] **Step 4: Verify**

```bash
nix develop --command uv run pytest tests/unit/test_longshot.py -v
nix develop --command uv run ruff check src tests
nix develop --command uv run mypy src
```

Expected: 9 tests pass; ruff/mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/predmarkbot/strategy/longshot.py tests/unit/test_longshot.py
git commit -m "feat(strategy): LongshotStrategy — YES buys on weather longshots near close"
```

---

## Task 5: Wire `LongshotStrategy` + `MarketMetaCache` into the runner

**Files:**
- Modify: `src/predmarkbot/runner.py`

This task DOES NOT delete `ArbStrategy` yet — that's Task 6. After this task, both classes exist in the codebase but only `LongshotStrategy` is instantiated by the runner.

- [ ] **Step 1: Read the current `src/predmarkbot/runner.py`** to find:
- The existing `from predmarkbot.strategy.arb import ArbStrategy` import
- The existing `strategy = ArbStrategy(...)` block
- The existing `_drain_updates` body that uses `str(uuid.uuid4())` for `client_order_id`
- The existing `tasks = [...]` list inside `run()`

- [ ] **Step 2: Replace the imports + add new ones**

At the top of `runner.py`, change:

```python
# OLD:
from predmarkbot.strategy.arb import ArbStrategy

# NEW:
from predmarkbot.market_meta import MarketMetaCache
from predmarkbot.strategy.longshot import LongshotStrategy
```

(Keep all other imports.)

- [ ] **Step 3: Replace the strategy construction block**

Inside `run()`, find the block that creates `discovery`, calls `discover_once()`, then builds the `ArbStrategy`. Replace it with:

```python
        discovery = MarketDiscovery(
            rest=rest,
            series=config.discovery.series,
            poll_interval_seconds=config.discovery.poll_interval_seconds,
        )
        watched = await discovery.discover_once()

        meta_cache = MarketMetaCache(rest=rest)
        await meta_cache.refresh(watched)

        await notifier.notify_startup(
            version=__version__, mode=config.mode.value, n_markets=len(watched),
        )

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

- [ ] **Step 4: Add `_meta_refresh_loop` function in `runner.py`**

Add this near the existing `_daily_pnl_loop` definition:

```python
async def _meta_refresh_loop(
    *,
    discovery: MarketDiscovery,
    meta_cache: MarketMetaCache,
    interval_seconds: int,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            watched = await discovery.discover_once()
            await meta_cache.refresh(watched)
        except Exception as exc:  # noqa: BLE001
            _log.warning("meta refresh failed: %s", exc)
```

The bare `except Exception` is intentional and noqa'd: a single fetch failure mid-refresh should not crash the entire bot. The next interval will retry.

- [ ] **Step 5: Add the meta-refresh task to `asyncio.gather`**

Find the `tasks = [...]` block in `run()` and append:

```python
            tasks: list[asyncio.Task[None]] = [
                asyncio.create_task(feed.consume(ws.messages()), name="feed"),
                asyncio.create_task(
                    _drain_updates(
                        updates=updates, strategy=strategy, risk=risk,
                        executor=executor, state=state, notifier=notifier,
                        mode=config.mode, meta_cache=meta_cache,  # NEW arg
                    ),
                    name="strategy_loop",
                ),
                asyncio.create_task(
                    _daily_pnl_loop(state=state, notifier=notifier),
                    name="daily_pnl",
                ),
                asyncio.create_task(                                # NEW task
                    _meta_refresh_loop(
                        discovery=discovery, meta_cache=meta_cache,
                        interval_seconds=config.discovery.poll_interval_seconds,
                    ),
                    name="meta_refresh",
                ),
            ]
```

- [ ] **Step 6: Update `_drain_updates` to use deterministic `client_order_id`**

Find the existing `_drain_updates` function. Modify its signature to accept `meta_cache`:

```python
async def _drain_updates(
    *,
    updates: "asyncio.Queue[OrderbookUpdate]",
    strategy: LongshotStrategy,
    risk: RiskManager,
    executor: Executor,
    state: StateStore,
    notifier: Notifier,
    mode: Mode,
    meta_cache: MarketMetaCache,
) -> None:
```

(Annotation `strategy: LongshotStrategy` — strict type since we only have one strategy now.)

Inside the function body, find the section where a `TradeOrder` is constructed (currently uses `str(uuid.uuid4())`). Replace it with:

```python
            if mode is Mode.SHADOW:
                await state.record_shadow_intent(
                    ts=upd.ts, ticker=intent.ticker, side=intent.side,
                    price_cents=intent.price_cents, size=intent.size,
                    expected_edge_cents=intent.expected_edge_cents,
                    reasoning=intent.reasoning,
                )
            else:
                order = TradeOrder(
                    client_order_id=f"longshot-{intent.ticker}",
                    ticker=intent.ticker, side=intent.side,
                    price_cents=intent.price_cents, size=intent.size,
                )
                await executor.submit(order)
```

Remove the `import uuid` at the top if it's no longer used elsewhere in the file.

- [ ] **Step 7: Run the full test suite**

```bash
nix develop --command uv run pytest tests/unit -v --tb=short
nix develop --command uv run ruff check src tests
nix develop --command uv run mypy src
```

Expected: all unit tests still pass (notably the existing `test_strategy_arb.py` tests — we haven't deleted ArbStrategy yet); ruff/mypy clean.

Note: the existing `tests/integration/test_e2e_shadow.py` will still work — the runner shadow-mode invariant ("0 real orders") is unchanged.

- [ ] **Step 8: Commit**

```bash
git add src/predmarkbot/runner.py
git commit -m "feat(runner): wire LongshotStrategy + MarketMetaCache + deterministic client_order_id"
```

---

## Task 6: Delete `ArbStrategy`

**Files:**
- Delete: `src/predmarkbot/strategy/arb.py`
- Delete: `tests/unit/test_strategy_arb.py`

This task is purely subtractive. The runner stopped importing `ArbStrategy` in Task 5; this task removes the module and its tests.

- [ ] **Step 1: Verify no current code imports `ArbStrategy`**

```bash
nix develop --command grep -r "from predmarkbot.strategy.arb\|ArbStrategy" /home/rupan/predmarkbot/src /home/rupan/predmarkbot/tests
```

Expected: only matches inside `src/predmarkbot/strategy/arb.py` and `tests/unit/test_strategy_arb.py`. If anything else, STOP and resolve.

- [ ] **Step 2: Delete the files**

```bash
cd /home/rupan/predmarkbot
git rm src/predmarkbot/strategy/arb.py tests/unit/test_strategy_arb.py
```

- [ ] **Step 3: Update any docstrings or design docs that reference `ArbStrategy`**

The `LongshotStrategy` spec (`docs/superpowers/specs/2026-06-01-longshot-strategy-design.md`) already notes the deletion. Older design specs at `docs/superpowers/specs/2026-05-30-kalshi-bot-design.md` may mention ArbStrategy — leave those as historical record. The runtime `README.md` and any in-source docstrings ARE worth updating:

```bash
nix develop --command grep -rn "ArbStrategy\|arb_strategy\|arbitrage strategy" /home/rupan/predmarkbot/src /home/rupan/predmarkbot/README.md 2>/dev/null
```

For any hits, update the prose to say `LongshotStrategy` where it makes sense, or remove the reference. In particular, the runner module docstring or any comment block that says "wires ArbStrategy" should say "wires LongshotStrategy."

- [ ] **Step 4: Run the test suite + lint + typecheck**

```bash
nix develop --command uv run pytest tests/unit -v
nix develop --command uv run ruff check src tests
nix develop --command uv run mypy src
```

Expected: all tests pass (the arb tests are gone, the longshot tests are new); ruff/mypy clean.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(strategy): delete ArbStrategy — superseded by LongshotStrategy"
```

---

## Task 7: Deploy via homelab configmap

This task is deployment-side — touches `~/homelab/`. The image build/push is automatic via the existing GitLab CI once a new image tag exists.

**Files:**
- Modify: `~/homelab/infrastructure/predmarkbot/configmap.yaml`

- [ ] **Step 1: Pull latest predmarkbot source into the homelab repo**

```bash
cd /home/rupan/homelab
git pull --no-rebase --no-edit origin main
git subtree pull --prefix=infrastructure/predmarkbot/src \
  /home/rupan/predmarkbot main --squash
```

This brings the new LongshotStrategy + MarketMetaCache + deleted ArbStrategy into `infrastructure/predmarkbot/src/`.

- [ ] **Step 2: Edit `~/homelab/infrastructure/predmarkbot/configmap.yaml`**

Find the existing config block and add the new `strategy:` block. Also remove any leftover `risk.max_intent_size`-style overrides if they were duplicated — the default in `StrategyConfig` is what's used.

The configmap should look like this for the `strategy:` section (insert after the `risk:` block, before `state:`):

```yaml
    strategy:
      type: longshot
      size_contracts: 5
      max_price_cents: 5
      min_seconds_to_close: 3600
      max_seconds_to_close: 86400
      historical_yes_rate: 0.14
      series_allowlist:
        - KXHIGHNY
        - KXHIGHCHI
        - KXHIGHLAX
        - KXHIGHMIA
        - KXHIGHDEN
        - KXHIGHHOU
        - KXLOWNY
        - KXLOWCHI
        - KXLOWLAX
        - KXLOWMIA
        - KXLOWDEN
```

Also extend `discovery.series` so the bot subscribes to all the allowlisted series:

```yaml
    discovery:
      series:
        - KXHIGHNY
        - KXHIGHCHI
        - KXHIGHLAX
        - KXHIGHMIA
        - KXHIGHDEN
        - KXHIGHHOU
        - KXLOWNY
        - KXLOWCHI
        - KXLOWLAX
        - KXLOWMIA
        - KXLOWDEN
      poll_interval_seconds: 300
```

Keep `mode: shadow` for safe rollout. Once the bot has run in shadow for a few days and the `shadow_intents` table shows expected fire frequency, flip to `mode: demo` with a follow-up configmap edit.

- [ ] **Step 3: Validate the configmap parses as YAML**

```bash
cd /home/rupan/homelab
python3 -c "import yaml; list(yaml.safe_load_all(open('infrastructure/predmarkbot/configmap.yaml'))); print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: yamllint (if available in the homelab dev shell)**

```bash
cd /home/rupan/homelab
direnv exec . yamllint infrastructure/predmarkbot/configmap.yaml || echo "yamllint not in path; CI will lint"
```

- [ ] **Step 5: Commit + push**

```bash
cd /home/rupan/homelab
git add infrastructure/predmarkbot/src/ infrastructure/predmarkbot/configmap.yaml
git commit -m "$(cat <<'EOF'
feat(predmarkbot): swap to LongshotStrategy + expand watched series

Vendors the new LongshotStrategy / MarketMetaCache code from
~/predmarkbot and adds the corresponding `strategy:` block to the
ConfigMap. Watch list expanded from KXHIGHNY-only to all 11 KXHIGH*
+ KXLOW* daily weather series.

Mode stays at shadow — first the operator confirms ~30 intents/day
in the shadow_intents table over a few days; then a separate
configmap edit flips to mode: demo.
EOF
)"
git push
```

- [ ] **Step 6: Watch the CI build the new image**

Watch `https://gitlab.com/JEFF7712/homelab/-/pipelines` for the new pipeline. The `build_predmarkbot_image` job will publish a new tag (`0.0.<pipeline_iid>`). When it's done, bump the image tag in `infrastructure/predmarkbot/deployment.yaml`:

```bash
NEW_TAG=$(curl -s "https://hub.docker.com/v2/repositories/jeff7712/predmarkbot/tags/?page_size=1" | python3 -c "import json,sys; print(json.load(sys.stdin)['results'][0]['name'])")
sed -i "s|image: jeff7712/predmarkbot:.*|image: jeff7712/predmarkbot:${NEW_TAG}|" \
  /home/rupan/homelab/infrastructure/predmarkbot/deployment.yaml
cd /home/rupan/homelab
git add infrastructure/predmarkbot/deployment.yaml
git -c user.name="rupan" -c user.email="rsunderapand@wisc.edu" commit -m "feat(predmarkbot): bump image to ${NEW_TAG} (LongshotStrategy)"
git push
```

ArgoCD picks up the deployment.yaml change on its next sync. Stakater Reloader restarts the pod with the new image + new ConfigMap. The pod boots in shadow mode against Kalshi prod (the existing deployment is already pointed at prod), subscribes to 11 series, and starts recording shadow_intents.

- [ ] **Step 7: Confirm the new pod is healthy**

```bash
cd /home/rupan/homelab
direnv exec . sh -c '
  kubectl -n predmarkbot get pods
  echo "---"
  kubectl -n predmarkbot logs deployment/predmarkbot --tail=30
  echo "---"
  kubectl -n predmarkbot exec deployment/predmarkbot -- \
    sqlite3 /var/lib/predmarkbot/state.db \
    "SELECT (SELECT count(*) FROM orders) AS orders, \
            (SELECT count(*) FROM shadow_intents) AS shadow_intents, \
            (SELECT count(*) FROM markets) AS markets;"
'
```

Expected: pod Running 1/1; logs show "startup," "discovered N tickers across 11 series," and (within minutes) at least one ntfy notification fired; SQLite shows orders=0 and (within hours) shadow_intents > 0.

If `shadow_intents > 0` within a day, the strategy is working. If still 0 after 24h, debug: probably the time-window filter (markets may close at unexpected times) or the meta_cache may be missing entries.

---

## Self-review

**Spec coverage:**
- `MarketMeta` dataclass → Task 1.
- `StrategyConfig` + default allowlist → Task 2.
- `MarketMetaCache` → Task 3.
- `LongshotStrategy` (all 9 unit tests from spec) → Task 4.
- Runner: build LongshotStrategy + meta_cache + meta_refresh task + deterministic client_order_id → Task 5.
- Delete `ArbStrategy` → Task 6.
- Homelab configmap update + image bump → Task 7.

All spec sections map to tasks.

**Placeholder scan:** no TBDs / TODOs. Every code step has runnable code.

**Type consistency:**
- `MarketMeta` defined in Task 1 with `(ticker, series_ticker, close_ts, yes_strike)` — used identically in Tasks 3 + 4.
- `MarketMetaCache.get(ticker) -> MarketMeta | None` signature defined in Task 3, used in Task 4's `get_market_meta` callback type and in Task 5's runner closure.
- `LongshotStrategy` constructor signature defined in Task 4 — used identically in Task 5.
- `series_allowlist: set[str]` in the strategy vs `list[str]` in config — the runner converts via `set(config.strategy.series_allowlist)` (Task 5 Step 3).
- `client_order_id` format is consistently `f"longshot-{ticker}"` in Tasks 4-5.

**Known v1 simplifications carried forward** (from the spec):
- `RiskManager.get_position_dollars=lambda _t: 0` is not touched. The per-market cap won't actually trip in v1; in-strategy dedupe + deterministic `client_order_id` provide the realistic safety.
- The runner takes ~30s extra at startup waiting on the synchronous meta_cache.refresh. Acceptable. A future optimization can refresh in the background.

---

## What's next (post-merge)

1. **Watch shadow run for ~3 days.** Confirm `shadow_intents` grows at expected rate (~30/day across 11 series). If much fewer, investigate — likely the time-window filter and/or meta_cache coverage.
2. **Flip to `mode: demo`.** Configmap edit only; Reloader restarts the pod. Demo trades start placing real orders against Kalshi demo funds.
3. **Soak for ~1 week in demo.** Check the `predmarkbot status` output (per-day P&L, position counts). If realized win rate is in the 12-17% range (the report's 14% ± noise), the strategy is matching reality.
4. **Plan 6: extend research to multi-day markets.** Per the operator's previously stated preference, this is the next research project after the strategy is in shadow.
