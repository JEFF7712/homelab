# Favorite-Longshot Research — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a research pipeline under `src/predmarkbot/research/` that pulls 6 months of resolved Kalshi market data + hourly candlesticks, snaps each market's price to four horizons (T-7d, T-24h, T-6h, T-1h), buckets by price, computes realized win-rate vs expected win-rate with Wilson confidence intervals, and emits a deterministic markdown+PNG report identifying any favorite-longshot bias.

**Architecture:** New subpackage `predmarkbot.research`, separate SQLite warehouse at `~/.local/share/predmarkbot/research.db`, four CLI subcommands grouped under `predmarkbot research`, isolated `[dependency-groups] research` so pandas/matplotlib/jupyterlab don't bloat the runtime container image. Runtime bot is **not modified**.

**Tech Stack:** Python 3.12, `aiosqlite` (already in deps), `httpx` (already in deps), `pandas` + `matplotlib` + `seaborn` + `scipy` (new research-group deps), `jupyterlab` + `nbstripout` (new research-group deps), `respx` for mocked HTTP tests.

---

## File structure

```
src/predmarkbot/research/
├── __init__.py            # version + package docstring
├── store.py               # SQLite schema + CRUD wrapper (mirrors state.py pattern)
├── ratelimit.py           # Token-bucket async limiter
├── fetch.py               # Paginated market fetcher + candlestick fetcher
├── horizons.py            # snap_to_horizon: pure function over candles
├── stats.py               # Wilson CI, bias_bps, two-tailed binomial p-value
├── analyze.py             # rebuild horizon_prices + bucket_stats from source tables
├── report.py              # markdown + matplotlib + strategy-sketch generator
└── cli.py                 # `predmarkbot research {pull,analyze,report,run}` group

src/predmarkbot/cli.py     # MODIFY: register the research subgroup
tests/unit/
├── test_research_store.py
├── test_ratelimit.py
├── test_research_fetch.py
├── test_horizons.py
├── test_stats.py
├── test_analyze.py
└── test_report.py
tests/integration/
└── test_research_e2e.py   # opt-in -m integration; hits Kalshi demo

notebooks/                 # new dir at repo root
├── README.md
├── 01_favorite_longshot_explore.ipynb
├── 02_category_drilldown.ipynb
└── 03_market_inspector.ipynb

pyproject.toml             # MODIFY: add [dependency-groups] research; add nbstripout pre-commit
.gitignore                 # MODIFY: gitignore notebook outputs + research data dir
```

---

## Phase 0 — Bootstrap

### Task 0.1: Research dependency group

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Append `research` to `[dependency-groups]`**

Open `pyproject.toml` and add this block immediately after the existing `dev` group inside `[dependency-groups]`:

```toml
research = [
    "pandas>=2.2",
    "matplotlib>=3.9",
    "seaborn>=0.13",
    "scipy>=1.13",
    "jupyterlab>=4.2",
    "nbstripout>=0.7",
    "pyarrow>=17",
]
```

(`pyarrow` is for pandas-to-parquet interop in notebooks; not strictly required for the pipeline but tiny cost and common in research workflows.)

- [ ] **Step 2: Sync the new group**

Run: `uv sync --group research`
Expected: ~30-60s install. Confirms versions are resolvable.

- [ ] **Step 3: Verify imports**

Run:
```bash
uv run --group research python -c "
import pandas, matplotlib, seaborn, scipy.stats, jupyterlab
import nbstripout
print('research deps OK')
"
```
Expected: prints `research deps OK`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add research dep-group (pandas, matplotlib, scipy, jupyter, nbstripout)"
```

---

### Task 0.2: Empty research subpackage + gitignore data dir

**Files:**
- Create: `src/predmarkbot/research/__init__.py`
- Modify: `.gitignore`

- [ ] **Step 1: Create the subpackage**

```bash
mkdir -p src/predmarkbot/research
```

Write `src/predmarkbot/research/__init__.py`:

```python
"""predmarkbot.research — offline analysis of Kalshi historical data.

This subpackage is intentionally NOT imported by the runtime bot
(predmarkbot.runner, predmarkbot.cli's run/status/smoke subcommands).
Its dependencies live in the `research` group and are excluded from the
production container image.
"""
```

- [ ] **Step 2: Extend `.gitignore`**

Add these lines (group them under a new `# Research` section):

```
# Research
research.db
research.db-journal
research.db-wal
research.db-shm
docs/research/
notebooks/.ipynb_checkpoints/
```

- [ ] **Step 3: Verify CLI still works**

Run: `uv run python -m predmarkbot --help`
Expected: still shows `run`, `status`, `smoke`. (No `research` yet — that's Task 5.1.)

- [ ] **Step 4: Commit**

```bash
git add src/predmarkbot/research/__init__.py .gitignore
git commit -m "feat(research): empty subpackage skeleton + gitignore data dir"
```

---

## Phase 1 — Storage layer

### Task 1.1: ResearchStore schema + migrations

**Files:**
- Create: `src/predmarkbot/research/store.py`
- Create: `tests/unit/test_research_store.py`

This mirrors the pattern of `src/predmarkbot/state.py` (read it first to keep conventions consistent).

- [ ] **Step 1: Write the failing test**

`tests/unit/test_research_store.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from predmarkbot.research.store import ResearchStore


@pytest.mark.asyncio
async def test_store_creates_schema_on_first_open(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    async with ResearchStore(db) as store:
        version = await store.schema_version()
    assert version == 1


@pytest.mark.asyncio
async def test_store_reopen_does_not_reset(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    async with ResearchStore(db) as store:
        await store.upsert_market(
            ticker="X-1", event_ticker="E", series_ticker="S",
            category="weather", title="t",
            open_ts="2026-01-01T00:00:00+00:00",
            close_ts="2026-01-02T00:00:00+00:00",
            settled_ts="2026-01-02T01:00:00+00:00",
            result="yes", yes_strike=None,
        )
    async with ResearchStore(db) as store:
        rows = await store.list_market_tickers()
    assert rows == ["X-1"]


@pytest.mark.asyncio
async def test_store_tables_exist(tmp_path: Path) -> None:
    async with ResearchStore(tmp_path / "r.db") as store:
        async with store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ) as cur:
            rows = await cur.fetchall()
        names = sorted(r[0] for r in rows if not r[0].startswith("sqlite_"))
    assert names == [
        "_fetch_failures",
        "_schema_version",
        "bucket_stats",
        "candlesticks",
        "horizon_prices",
        "markets",
    ]
```

- [ ] **Step 2: Confirm failure**

Run: `uv run pytest tests/unit/test_research_store.py -v`
Expected: `ModuleNotFoundError: No module named 'predmarkbot.research.store'`.

- [ ] **Step 3: Implement `src/predmarkbot/research/store.py`**

```python
"""SQLite-backed research data warehouse."""
from __future__ import annotations

from pathlib import Path
from typing import Self

import aiosqlite

_SCHEMA: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS _schema_version (
        version INTEGER PRIMARY KEY
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS markets (
        ticker          TEXT PRIMARY KEY,
        event_ticker    TEXT NOT NULL,
        series_ticker   TEXT NOT NULL,
        category        TEXT NOT NULL,
        title           TEXT NOT NULL,
        open_ts         TEXT NOT NULL,
        close_ts        TEXT NOT NULL,
        settled_ts      TEXT,
        result          TEXT NOT NULL,
        yes_strike      REAL,
        fetched_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_markets_category_close
        ON markets(category, close_ts);
    """,
    """
    CREATE TABLE IF NOT EXISTS candlesticks (
        ticker          TEXT NOT NULL,
        ts              TEXT NOT NULL,
        open_yes_cents  INTEGER NOT NULL,
        high_yes_cents  INTEGER NOT NULL,
        low_yes_cents   INTEGER NOT NULL,
        close_yes_cents INTEGER NOT NULL,
        volume          INTEGER NOT NULL,
        PRIMARY KEY (ticker, ts)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_candles_ticker
        ON candlesticks(ticker);
    """,
    """
    CREATE TABLE IF NOT EXISTS _fetch_failures (
        ticker          TEXT PRIMARY KEY,
        endpoint        TEXT NOT NULL,
        last_error      TEXT NOT NULL,
        attempts        INTEGER NOT NULL,
        last_attempt_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS horizon_prices (
        ticker          TEXT NOT NULL,
        horizon         TEXT NOT NULL,
        price_yes_cents INTEGER,
        PRIMARY KEY (ticker, horizon)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS bucket_stats (
        horizon         TEXT NOT NULL,
        category        TEXT NOT NULL,
        bucket_lo       INTEGER NOT NULL,
        bucket_hi       INTEGER NOT NULL,
        n_markets       INTEGER NOT NULL,
        n_yes           INTEGER NOT NULL,
        realized_rate   REAL NOT NULL,
        expected_rate   REAL NOT NULL,
        bias_bps        INTEGER NOT NULL,
        ci_lo           REAL NOT NULL,
        ci_hi           REAL NOT NULL,
        p_value         REAL NOT NULL,
        PRIMARY KEY (horizon, category, bucket_lo)
    );
    """,
]


class ResearchStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def __aenter__(self) -> Self:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._migrate()
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._conn is not None:
            await self._conn.close()

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None
        return self._conn

    async def _migrate(self) -> None:
        for stmt in _SCHEMA:
            await self.conn.execute(stmt)
        await self.conn.execute(
            "INSERT OR IGNORE INTO _schema_version(version) VALUES (1)"
        )
        await self.conn.commit()

    async def schema_version(self) -> int:
        async with self.conn.execute(
            "SELECT max(version) FROM _schema_version"
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    # ----- markets -----
    async def upsert_market(
        self,
        *,
        ticker: str,
        event_ticker: str,
        series_ticker: str,
        category: str,
        title: str,
        open_ts: str,
        close_ts: str,
        settled_ts: str | None,
        result: str,
        yes_strike: float | None,
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO markets
                (ticker, event_ticker, series_ticker, category, title,
                 open_ts, close_ts, settled_ts, result, yes_strike)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                event_ticker=excluded.event_ticker,
                series_ticker=excluded.series_ticker,
                category=excluded.category,
                title=excluded.title,
                open_ts=excluded.open_ts,
                close_ts=excluded.close_ts,
                settled_ts=excluded.settled_ts,
                result=excluded.result,
                yes_strike=excluded.yes_strike,
                fetched_at=datetime('now')
            """,
            (ticker, event_ticker, series_ticker, category, title,
             open_ts, close_ts, settled_ts, result, yes_strike),
        )
        await self.conn.commit()

    async def list_market_tickers(self) -> list[str]:
        async with self.conn.execute(
            "SELECT ticker FROM markets ORDER BY ticker"
        ) as cur:
            rows = await cur.fetchall()
        return [r["ticker"] for r in rows]
```

- [ ] **Step 4: Run, confirm passing**

Run: `uv run pytest tests/unit/test_research_store.py -v`
Expected: 3 passed.

- [ ] **Step 5: Lint + typecheck + commit**

```bash
uv run ruff check src tests && uv run mypy src
git add src/predmarkbot/research/store.py tests/unit/test_research_store.py
git commit -m "feat(research): ResearchStore schema + markets table CRUD"
```

---

### Task 1.2: Candlestick + failure-queue CRUD

**Files:**
- Modify: `src/predmarkbot/research/store.py` (append methods)
- Modify: `tests/unit/test_research_store.py` (append tests)

- [ ] **Step 1: Append the failing tests**

Append to `tests/unit/test_research_store.py`:

```python
@pytest.mark.asyncio
async def test_insert_candlesticks_and_fetch(tmp_path: Path) -> None:
    async with ResearchStore(tmp_path / "r.db") as store:
        await store.insert_candlesticks(
            ticker="X-1",
            rows=[
                ("2026-01-01T00:00:00+00:00", 40, 42, 39, 41, 1000),
                ("2026-01-01T01:00:00+00:00", 41, 45, 41, 44, 1500),
            ],
        )
        rows = await store.get_candlesticks("X-1")
    assert len(rows) == 2
    assert rows[0]["close_yes_cents"] == 41


@pytest.mark.asyncio
async def test_insert_candlesticks_idempotent(tmp_path: Path) -> None:
    async with ResearchStore(tmp_path / "r.db") as store:
        row = ("2026-01-01T00:00:00+00:00", 40, 42, 39, 41, 1000)
        await store.insert_candlesticks(ticker="X-1", rows=[row])
        await store.insert_candlesticks(ticker="X-1", rows=[row])  # dupe
        rows = await store.get_candlesticks("X-1")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_tickers_with_candles(tmp_path: Path) -> None:
    async with ResearchStore(tmp_path / "r.db") as store:
        await store.insert_candlesticks(
            ticker="X-1",
            rows=[("2026-01-01T00:00:00+00:00", 40, 42, 39, 41, 1000)],
        )
        tickers = await store.tickers_with_candles()
    assert tickers == {"X-1"}


@pytest.mark.asyncio
async def test_record_and_list_fetch_failures(tmp_path: Path) -> None:
    async with ResearchStore(tmp_path / "r.db") as store:
        await store.record_fetch_failure(
            ticker="X-2", endpoint="candlesticks", error="429",
        )
        await store.record_fetch_failure(
            ticker="X-2", endpoint="candlesticks", error="429",
        )
        failures = await store.list_fetch_failures()
    assert len(failures) == 1
    assert failures[0]["attempts"] == 2
```

- [ ] **Step 2: Confirm failure**

- [ ] **Step 3: Append methods to `store.py`**

```python
    # ----- candlesticks -----
    async def insert_candlesticks(
        self,
        *,
        ticker: str,
        rows: list[tuple[str, int, int, int, int, int]],
    ) -> None:
        """Each row: (ts, open, high, low, close, volume). Idempotent."""
        await self.conn.executemany(
            """
            INSERT OR IGNORE INTO candlesticks
                (ticker, ts, open_yes_cents, high_yes_cents,
                 low_yes_cents, close_yes_cents, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [(ticker, *row) for row in rows],
        )
        await self.conn.commit()

    async def get_candlesticks(self, ticker: str) -> list[dict[str, object]]:
        async with self.conn.execute(
            "SELECT * FROM candlesticks WHERE ticker=? ORDER BY ts",
            (ticker,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def tickers_with_candles(self) -> set[str]:
        async with self.conn.execute(
            "SELECT DISTINCT ticker FROM candlesticks"
        ) as cur:
            rows = await cur.fetchall()
        return {r["ticker"] for r in rows}

    # ----- fetch failures -----
    async def record_fetch_failure(
        self, *, ticker: str, endpoint: str, error: str
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO _fetch_failures
                (ticker, endpoint, last_error, attempts)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(ticker) DO UPDATE SET
                endpoint=excluded.endpoint,
                last_error=excluded.last_error,
                attempts=_fetch_failures.attempts + 1,
                last_attempt_at=datetime('now')
            """,
            (ticker, endpoint, error),
        )
        await self.conn.commit()

    async def list_fetch_failures(self) -> list[dict[str, object]]:
        async with self.conn.execute(
            "SELECT * FROM _fetch_failures ORDER BY ticker"
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 4: Run, confirm passing**

Run: `uv run pytest tests/unit/test_research_store.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/predmarkbot/research/store.py tests/unit/test_research_store.py
git commit -m "feat(research): candlestick + fetch-failure CRUD"
```

---

### Task 1.3: Derived-tables writers (horizon_prices + bucket_stats)

**Files:**
- Modify: `src/predmarkbot/research/store.py`
- Modify: `tests/unit/test_research_store.py`

- [ ] **Step 1: Append failing tests**

```python
@pytest.mark.asyncio
async def test_rebuild_horizon_prices_clears_then_inserts(tmp_path: Path) -> None:
    async with ResearchStore(tmp_path / "r.db") as store:
        await store.replace_horizon_prices([
            ("X-1", "T-7d", 40),
            ("X-1", "T-24h", 50),
            ("X-1", "T-6h", 60),
            ("X-1", "T-1h", 90),
            ("X-2", "T-7d", None),
        ])
        await store.replace_horizon_prices([("X-1", "T-7d", 41)])
        async with store.conn.execute(
            "SELECT count(*), sum(price_yes_cents) FROM horizon_prices"
        ) as cur:
            count, total = (await cur.fetchone()) or (0, 0)
    assert count == 1
    assert total == 41


@pytest.mark.asyncio
async def test_replace_bucket_stats_clears_then_inserts(tmp_path: Path) -> None:
    async with ResearchStore(tmp_path / "r.db") as store:
        await store.replace_bucket_stats([
            {
                "horizon": "T-6h", "category": "ALL",
                "bucket_lo": 0, "bucket_hi": 5,
                "n_markets": 100, "n_yes": 3,
                "realized_rate": 0.03, "expected_rate": 0.025,
                "bias_bps": 50, "ci_lo": 0.01, "ci_hi": 0.08,
                "p_value": 0.5,
            }
        ])
        async with store.conn.execute(
            "SELECT count(*) FROM bucket_stats"
        ) as cur:
            row = await cur.fetchone()
    assert row[0] == 1
```

- [ ] **Step 2: Confirm failure**

- [ ] **Step 3: Append methods**

```python
    # ----- derived tables -----
    async def replace_horizon_prices(
        self, rows: list[tuple[str, str, int | None]]
    ) -> None:
        """Each row: (ticker, horizon, price_or_None). Replaces entire table."""
        await self.conn.execute("DELETE FROM horizon_prices")
        await self.conn.executemany(
            """
            INSERT INTO horizon_prices(ticker, horizon, price_yes_cents)
            VALUES (?, ?, ?)
            """,
            rows,
        )
        await self.conn.commit()

    async def replace_bucket_stats(
        self, rows: list[dict[str, object]]
    ) -> None:
        """Replaces entire bucket_stats table."""
        await self.conn.execute("DELETE FROM bucket_stats")
        if rows:
            keys = list(rows[0].keys())
            placeholders = ", ".join("?" for _ in keys)
            cols = ", ".join(keys)
            await self.conn.executemany(
                f"INSERT INTO bucket_stats ({cols}) VALUES ({placeholders})",
                [tuple(r[k] for k in keys) for r in rows],
            )
        await self.conn.commit()
```

- [ ] **Step 4: Pass + commit**

```bash
uv run pytest tests/unit/test_research_store.py -v
uv run ruff check src tests && uv run mypy src
git add src/predmarkbot/research/store.py tests/unit/test_research_store.py
git commit -m "feat(research): replace-style writers for derived tables"
```

---

## Phase 2 — Fetcher

### Task 2.1: Token-bucket rate limiter

**Files:**
- Create: `src/predmarkbot/research/ratelimit.py`
- Create: `tests/unit/test_ratelimit.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

import asyncio
import time

import pytest

from predmarkbot.research.ratelimit import TokenBucket


@pytest.mark.asyncio
async def test_first_n_acquires_are_instant() -> None:
    bucket = TokenBucket(rate_per_sec=5.0, burst=5)
    start = time.monotonic()
    for _ in range(5):
        await bucket.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.05


@pytest.mark.asyncio
async def test_over_burst_waits_for_refill() -> None:
    bucket = TokenBucket(rate_per_sec=10.0, burst=2)
    for _ in range(2):
        await bucket.acquire()
    start = time.monotonic()
    await bucket.acquire()  # must wait ~0.1s for one token to refill
    elapsed = time.monotonic() - start
    assert 0.08 <= elapsed <= 0.2


@pytest.mark.asyncio
async def test_concurrent_callers_serialize_correctly() -> None:
    bucket = TokenBucket(rate_per_sec=10.0, burst=1)
    async def call(i: int) -> float:
        await bucket.acquire()
        return time.monotonic()
    start = time.monotonic()
    times = await asyncio.gather(*[call(i) for i in range(4)])
    rel = [t - start for t in sorted(times)]
    # Token bucket replenishes at 1/0.1s; allow tolerance
    assert rel[0] < 0.02
    assert rel[1] >= 0.08
    assert rel[2] >= 0.18
    assert rel[3] >= 0.28
```

- [ ] **Step 2: Confirm failure**

- [ ] **Step 3: Implement**

`src/predmarkbot/research/ratelimit.py`:

```python
"""Asyncio token-bucket rate limiter."""
from __future__ import annotations

import asyncio
import time


class TokenBucket:
    """Classic token bucket: `rate_per_sec` tokens added, capped at `burst`.

    Each `acquire()` consumes one token, awaiting if none available.
    Thread-safe within a single asyncio loop (uses an asyncio.Lock).
    """

    def __init__(self, *, rate_per_sec: float, burst: int) -> None:
        self._rate = rate_per_sec
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(
                    self._capacity,
                    self._tokens + elapsed * self._rate,
                )
                self._last_refill = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
                await asyncio.sleep(wait)
```

- [ ] **Step 4: Pass + commit**

```bash
uv run pytest tests/unit/test_ratelimit.py -v
uv run ruff check src tests && uv run mypy src
git add src/predmarkbot/research/ratelimit.py tests/unit/test_ratelimit.py
git commit -m "feat(research): token-bucket rate limiter"
```

---

### Task 2.2: Market metadata fetcher

**Files:**
- Create: `src/predmarkbot/research/fetch.py`
- Create: `tests/unit/test_research_fetch.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from pathlib import Path

import pytest
import respx
from httpx import Response

from predmarkbot.kalshi.rest import KalshiRestClient
from predmarkbot.research.fetch import fetch_resolved_markets
from predmarkbot.research.ratelimit import TokenBucket
from predmarkbot.research.store import ResearchStore


@pytest.mark.asyncio
@respx.mock
async def test_fetch_paginates_and_upserts(tmp_path: Path) -> None:
    base = "https://demo-api.kalshi.co/trade-api/v2"
    respx.get(f"{base}/markets").mock(
        side_effect=[
            Response(200, json={
                "markets": [
                    {
                        "ticker": "X-1", "event_ticker": "E1",
                        "series_ticker": "S", "category": "weather",
                        "title": "t1",
                        "open_time": "2026-01-01T00:00:00Z",
                        "close_time": "2026-01-02T00:00:00Z",
                        "settle_time": "2026-01-02T01:00:00Z",
                        "result": "yes",
                    },
                ],
                "cursor": "PAGE2",
            }),
            Response(200, json={
                "markets": [
                    {
                        "ticker": "X-2", "event_ticker": "E2",
                        "series_ticker": "S", "category": "weather",
                        "title": "t2",
                        "open_time": "2026-01-02T00:00:00Z",
                        "close_time": "2026-01-03T00:00:00Z",
                        "settle_time": "2026-01-03T01:00:00Z",
                        "result": "no",
                    },
                ],
                "cursor": "",
            }),
        ]
    )
    async with (
        KalshiRestClient(base_url=base, signer=None) as rest,
        ResearchStore(tmp_path / "r.db") as store,
    ):
        bucket = TokenBucket(rate_per_sec=100.0, burst=10)
        n = await fetch_resolved_markets(
            rest=rest, store=store, bucket=bucket,
            from_close="2026-01-01T00:00:00Z",
            to_close="2026-01-03T00:00:00Z",
        )
        tickers = await store.list_market_tickers()
    assert n == 2
    assert tickers == ["X-1", "X-2"]


@pytest.mark.asyncio
@respx.mock
async def test_fetch_idempotent_on_rerun(tmp_path: Path) -> None:
    base = "https://demo-api.kalshi.co/trade-api/v2"
    respx.get(f"{base}/markets").respond(json={
        "markets": [{
            "ticker": "X-1", "event_ticker": "E", "series_ticker": "S",
            "category": "weather", "title": "t",
            "open_time": "2026-01-01T00:00:00Z",
            "close_time": "2026-01-02T00:00:00Z",
            "settle_time": "2026-01-02T01:00:00Z",
            "result": "yes",
        }],
        "cursor": "",
    })
    async with (
        KalshiRestClient(base_url=base, signer=None) as rest,
        ResearchStore(tmp_path / "r.db") as store,
    ):
        bucket = TokenBucket(rate_per_sec=100.0, burst=10)
        await fetch_resolved_markets(
            rest=rest, store=store, bucket=bucket,
            from_close="2026-01-01T00:00:00Z",
            to_close="2026-01-03T00:00:00Z",
        )
        await fetch_resolved_markets(
            rest=rest, store=store, bucket=bucket,
            from_close="2026-01-01T00:00:00Z",
            to_close="2026-01-03T00:00:00Z",
        )
        tickers = await store.list_market_tickers()
    assert tickers == ["X-1"]
```

- [ ] **Step 2: Confirm failure**

- [ ] **Step 3: Implement fetch.py (initial — just market metadata)**

```python
"""Kalshi historical-data fetcher: resolved markets + candlesticks."""
from __future__ import annotations

import logging
from typing import Any

from predmarkbot.kalshi.rest import KalshiRestClient
from predmarkbot.research.ratelimit import TokenBucket
from predmarkbot.research.store import ResearchStore

_log = logging.getLogger(__name__)


def _to_iso(ts: object) -> str:
    """Normalize Kalshi's 'Z' timestamps to '+00:00' offset form."""
    s = str(ts)
    if s.endswith("Z"):
        return s[:-1] + "+00:00"
    return s


async def fetch_resolved_markets(
    *,
    rest: KalshiRestClient,
    store: ResearchStore,
    bucket: TokenBucket,
    from_close: str,
    to_close: str,
    categories: set[str] | None = None,
) -> int:
    """Paginate resolved-market metadata from Kalshi and upsert into store.

    Returns total markets upserted.
    """
    count = 0
    cursor = ""
    while True:
        await bucket.acquire()
        params = (
            f"?status=settled&limit=200"
            f"&min_close_ts={from_close}&max_close_ts={to_close}"
        )
        if cursor:
            params += f"&cursor={cursor}"
        data = await rest.get(f"/markets{params}")
        markets = data.get("markets", [])
        for m in markets:
            cat = str(m.get("category", "unknown"))
            if categories and cat not in categories:
                continue
            await store.upsert_market(
                ticker=str(m["ticker"]),
                event_ticker=str(m.get("event_ticker", "")),
                series_ticker=str(m.get("series_ticker", "")),
                category=cat,
                title=str(m.get("title", "")),
                open_ts=_to_iso(m.get("open_time", "")),
                close_ts=_to_iso(m.get("close_time", "")),
                settled_ts=_to_iso(m["settle_time"]) if m.get("settle_time") else None,
                result=str(m.get("result", "")),
                yes_strike=_safe_float(m.get("yes_strike")),
            )
            count += 1
        cursor = str(data.get("cursor", "") or "")
        if not cursor:
            break
        if count % 100 == 0:
            _log.info("fetched %d markets so far", count)
    _log.info("fetched %d resolved markets in window", count)
    return count


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 4: Pass + commit**

```bash
uv run pytest tests/unit/test_research_fetch.py -v
uv run ruff check src tests && uv run mypy src
git add src/predmarkbot/research/fetch.py tests/unit/test_research_fetch.py
git commit -m "feat(research): paginated resolved-markets fetcher with idempotent upsert"
```

---

### Task 2.3: Candlestick fetcher with retry/backoff

**Files:**
- Modify: `src/predmarkbot/research/fetch.py`
- Modify: `tests/unit/test_research_fetch.py`

- [ ] **Step 1: Append failing tests**

```python
@pytest.mark.asyncio
@respx.mock
async def test_candles_fetch_writes_rows(tmp_path: Path) -> None:
    from predmarkbot.research.fetch import fetch_candlesticks
    base = "https://demo-api.kalshi.co/trade-api/v2"
    respx.get(f"{base}/markets/X-1/candlesticks").respond(json={
        "candlesticks": [
            {"end_period_ts": 1735689600, "yes_bid": {
                "open": 40, "high": 42, "low": 39, "close": 41
            }, "volume": 1000},
        ]
    })
    async with (
        KalshiRestClient(base_url=base, signer=None) as rest,
        ResearchStore(tmp_path / "r.db") as store,
    ):
        bucket = TokenBucket(rate_per_sec=100.0, burst=10)
        await fetch_candlesticks(
            rest=rest, store=store, bucket=bucket,
            ticker="X-1",
            start_ts="2026-01-01T00:00:00Z",
            end_ts="2026-01-02T00:00:00Z",
        )
        rows = await store.get_candlesticks("X-1")
    assert len(rows) == 1
    assert rows[0]["close_yes_cents"] == 41


@pytest.mark.asyncio
@respx.mock
async def test_candles_records_failure_on_persistent_4xx(tmp_path: Path) -> None:
    from predmarkbot.research.fetch import fetch_candlesticks
    base = "https://demo-api.kalshi.co/trade-api/v2"
    respx.get(f"{base}/markets/X-1/candlesticks").respond(404, json={"error": "no"})
    async with (
        KalshiRestClient(base_url=base, signer=None) as rest,
        ResearchStore(tmp_path / "r.db") as store,
    ):
        bucket = TokenBucket(rate_per_sec=100.0, burst=10)
        await fetch_candlesticks(
            rest=rest, store=store, bucket=bucket,
            ticker="X-1",
            start_ts="2026-01-01T00:00:00Z",
            end_ts="2026-01-02T00:00:00Z",
        )
        failures = await store.list_fetch_failures()
    assert len(failures) == 1
    assert failures[0]["ticker"] == "X-1"
```

- [ ] **Step 2: Confirm failure**

- [ ] **Step 3: Append fetch_candlesticks**

```python
# Append to src/predmarkbot/research/fetch.py:

from datetime import UTC, datetime

from predmarkbot.kalshi.rest import KalshiApiError


def _iso_to_unix(ts: str) -> int:
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return int(datetime.fromisoformat(ts).timestamp())


def _unix_to_iso(unix_s: int) -> str:
    return datetime.fromtimestamp(unix_s, tz=UTC).isoformat()


async def fetch_candlesticks(
    *,
    rest: KalshiRestClient,
    store: ResearchStore,
    bucket: TokenBucket,
    ticker: str,
    start_ts: str,
    end_ts: str,
    period_minutes: int = 60,
) -> None:
    """Fetch hourly candlesticks for one ticker; idempotent upsert."""
    start_unix = _iso_to_unix(start_ts)
    end_unix = _iso_to_unix(end_ts)
    await bucket.acquire()
    try:
        data = await rest.get(
            f"/markets/{ticker}/candlesticks"
            f"?period_interval={period_minutes}"
            f"&start_ts={start_unix}&end_ts={end_unix}"
        )
    except KalshiApiError as exc:
        await store.record_fetch_failure(
            ticker=ticker, endpoint="candlesticks", error=str(exc),
        )
        _log.warning("candlestick fetch failed for %s: %s", ticker, exc)
        return

    rows: list[tuple[str, int, int, int, int, int]] = []
    for c in data.get("candlesticks", []):
        ts = _unix_to_iso(int(c["end_period_ts"]))
        yes = c.get("yes_bid", {}) or c.get("price", {})
        rows.append((
            ts,
            int(yes.get("open", 0)),
            int(yes.get("high", 0)),
            int(yes.get("low", 0)),
            int(yes.get("close", 0)),
            int(c.get("volume", 0)),
        ))
    if rows:
        await store.insert_candlesticks(ticker=ticker, rows=rows)
```

- [ ] **Step 4: Pass + commit**

```bash
uv run pytest tests/unit/test_research_fetch.py -v
uv run ruff check src tests && uv run mypy src
git add src/predmarkbot/research/fetch.py tests/unit/test_research_fetch.py
git commit -m "feat(research): candlestick fetcher with failure recording"
```

---

### Task 2.4: Top-level orchestrator (`pull_all`)

**Files:**
- Modify: `src/predmarkbot/research/fetch.py`
- Modify: `tests/unit/test_research_fetch.py`

- [ ] **Step 1: Append failing test**

```python
@pytest.mark.asyncio
@respx.mock
async def test_pull_all_fetches_markets_then_candles(tmp_path: Path) -> None:
    from predmarkbot.research.fetch import pull_all
    base = "https://demo-api.kalshi.co/trade-api/v2"
    respx.get(f"{base}/markets").respond(json={
        "markets": [{
            "ticker": "X-1", "event_ticker": "E", "series_ticker": "S",
            "category": "weather", "title": "t",
            "open_time": "2026-01-01T00:00:00Z",
            "close_time": "2026-01-02T00:00:00Z",
            "settle_time": "2026-01-02T01:00:00Z",
            "result": "yes",
        }],
        "cursor": "",
    })
    respx.get(f"{base}/markets/X-1/candlesticks").respond(json={
        "candlesticks": [
            {"end_period_ts": 1735776000, "yes_bid": {
                "open": 40, "high": 42, "low": 39, "close": 41
            }, "volume": 1000},
        ]
    })
    async with (
        KalshiRestClient(base_url=base, signer=None) as rest,
        ResearchStore(tmp_path / "r.db") as store,
    ):
        await pull_all(
            rest=rest, store=store,
            from_close="2026-01-01T00:00:00Z",
            to_close="2026-01-03T00:00:00Z",
            rate_per_sec=100.0,
        )
        tickers = await store.list_market_tickers()
        candles = await store.tickers_with_candles()
    assert tickers == ["X-1"]
    assert candles == {"X-1"}
```

- [ ] **Step 2: Confirm failure**

- [ ] **Step 3: Append `pull_all`**

```python
# Append to src/predmarkbot/research/fetch.py:

async def pull_all(
    *,
    rest: KalshiRestClient,
    store: ResearchStore,
    from_close: str,
    to_close: str,
    categories: set[str] | None = None,
    rate_per_sec: float = 5.0,
    refetch: bool = False,
) -> tuple[int, int]:
    """Orchestrate full pull: markets, then candlesticks for any market
    not already fully covered.

    Returns (n_markets, n_candle_tickers_fetched).
    """
    bucket = TokenBucket(rate_per_sec=rate_per_sec, burst=int(rate_per_sec))
    n_markets = await fetch_resolved_markets(
        rest=rest, store=store, bucket=bucket,
        from_close=from_close, to_close=to_close,
        categories=categories,
    )

    have_candles = set() if refetch else await store.tickers_with_candles()
    all_tickers = set(await store.list_market_tickers())
    todo = sorted(all_tickers - have_candles)

    n_done = 0
    for ticker in todo:
        async with store.conn.execute(
            "SELECT open_ts, close_ts FROM markets WHERE ticker=?",
            (ticker,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            continue
        await fetch_candlesticks(
            rest=rest, store=store, bucket=bucket,
            ticker=ticker,
            start_ts=str(row["open_ts"]),
            end_ts=str(row["close_ts"]),
        )
        n_done += 1
        if n_done % 50 == 0:
            _log.info("fetched candlesticks for %d / %d markets", n_done, len(todo))
    _log.info("pull complete: %d markets, %d candle-fetches", n_markets, n_done)
    return n_markets, n_done
```

- [ ] **Step 4: Pass + commit**

```bash
uv run pytest tests/unit/test_research_fetch.py -v
uv run ruff check src tests && uv run mypy src
git add src/predmarkbot/research/fetch.py tests/unit/test_research_fetch.py
git commit -m "feat(research): pull_all orchestrator (markets + candles)"
```

---

## Phase 3 — Analysis

### Task 3.1: Horizon snapping

**Files:**
- Create: `src/predmarkbot/research/horizons.py`
- Create: `tests/unit/test_horizons.py`

- [ ] **Step 1: Write failing test**

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from predmarkbot.research.horizons import (
    HORIZON_OFFSETS,
    horizon_label,
    snap_to_horizon,
)


def _candle(close_ts: datetime, close_yes: int) -> dict:
    return {
        "ts": close_ts.isoformat(),
        "open_yes_cents": close_yes,
        "high_yes_cents": close_yes,
        "low_yes_cents": close_yes,
        "close_yes_cents": close_yes,
        "volume": 1,
    }


def test_horizon_offsets_are_correct() -> None:
    assert HORIZON_OFFSETS == {
        "T-7d": timedelta(days=7),
        "T-24h": timedelta(hours=24),
        "T-6h": timedelta(hours=6),
        "T-1h": timedelta(hours=1),
    }


def test_horizon_label_lists_in_order() -> None:
    labels = list(HORIZON_OFFSETS.keys())
    assert labels == ["T-7d", "T-24h", "T-6h", "T-1h"]
    assert all(horizon_label(o) in HORIZON_OFFSETS for o in HORIZON_OFFSETS.values())


def test_snap_to_exact_hour_returns_that_candle() -> None:
    close = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    candles = [
        _candle(close - timedelta(hours=h), 50 + h)
        for h in range(0, 24)
    ]
    price = snap_to_horizon(close_ts=close, candles=candles, horizon="T-6h")
    assert price == 56  # 6 hours back -> 50 + 6


def test_snap_walks_backward_through_gap() -> None:
    close = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    # Candle at T-8h (50), but no candle at T-6h
    candles = [_candle(close - timedelta(hours=8), 50)]
    price = snap_to_horizon(close_ts=close, candles=candles, horizon="T-6h")
    assert price == 50


def test_snap_returns_none_when_no_candle_in_24h_window() -> None:
    close = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    # Candle 48h before T-1h -> outside the 24h walk-back window
    candles = [_candle(close - timedelta(hours=49), 50)]
    price = snap_to_horizon(close_ts=close, candles=candles, horizon="T-1h")
    assert price is None


def test_snap_returns_none_for_empty_candles() -> None:
    close = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    assert snap_to_horizon(close_ts=close, candles=[], horizon="T-7d") is None


def test_invalid_horizon_label_raises() -> None:
    close = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    with pytest.raises(KeyError):
        snap_to_horizon(close_ts=close, candles=[], horizon="T-99d")
```

- [ ] **Step 2: Confirm failure**

- [ ] **Step 3: Implement `src/predmarkbot/research/horizons.py`**

```python
"""Snap market candlestick history to fixed pre-close horizons."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

HORIZON_OFFSETS: dict[str, timedelta] = {
    "T-7d":  timedelta(days=7),
    "T-24h": timedelta(hours=24),
    "T-6h":  timedelta(hours=6),
    "T-1h":  timedelta(hours=1),
}

_BACKWARD_SEARCH_WINDOW = timedelta(hours=24)


def horizon_label(offset: timedelta) -> str:
    """Reverse lookup: timedelta -> label. Raises KeyError if not registered."""
    for label, off in HORIZON_OFFSETS.items():
        if off == offset:
            return label
    raise KeyError(offset)


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def snap_to_horizon(
    *, close_ts: datetime, candles: Iterable[dict[str, object]], horizon: str
) -> int | None:
    """Return the close_yes_cents of the candle covering close_ts - offset[horizon].

    If no candle covers that exact hour, walk backward up to 24h for the most
    recent. If still none, return None.
    """
    target = close_ts - HORIZON_OFFSETS[horizon]
    earliest = target - _BACKWARD_SEARCH_WINDOW

    # Filter to candles within the search window, ts <= target
    in_window: list[tuple[datetime, int]] = []
    for c in candles:
        ts = _parse_ts(str(c["ts"]))
        if earliest <= ts <= target:
            in_window.append((ts, int(c["close_yes_cents"])))
    if not in_window:
        return None
    in_window.sort(key=lambda x: x[0])
    return in_window[-1][1]
```

- [ ] **Step 4: Pass + commit**

```bash
uv run pytest tests/unit/test_horizons.py -v
uv run ruff check src tests && uv run mypy src
git add src/predmarkbot/research/horizons.py tests/unit/test_horizons.py
git commit -m "feat(research): snap_to_horizon over hourly candle series"
```

---

### Task 3.2: Bucketing, Wilson CI, bias bps, binomial p-value

**Files:**
- Create: `src/predmarkbot/research/stats.py`
- Create: `tests/unit/test_stats.py`

- [ ] **Step 1: Write failing test**

```python
from __future__ import annotations

import math

import pytest

from predmarkbot.research.stats import (
    BUCKET_WIDTH,
    NUM_BUCKETS,
    bias_bps,
    binomial_p_value,
    bucket_for,
    wilson_ci,
)


def test_bucket_constants() -> None:
    assert BUCKET_WIDTH == 5
    assert NUM_BUCKETS == 20


@pytest.mark.parametrize(
    "price,expected_lo",
    [
        (0, 0), (1, 0), (4, 0),
        (5, 5), (9, 5),
        (50, 50),
        (94, 90),
        (95, 95), (99, 95),
    ],
)
def test_bucket_for_boundaries(price: int, expected_lo: int) -> None:
    assert bucket_for(price) == expected_lo


def test_bucket_for_out_of_range() -> None:
    with pytest.raises(ValueError):
        bucket_for(-1)
    with pytest.raises(ValueError):
        bucket_for(100)


def test_bias_bps_zero_when_realized_equals_expected() -> None:
    assert bias_bps(realized=0.5, expected=0.5) == 0


def test_bias_bps_positive_when_realized_above_expected() -> None:
    assert bias_bps(realized=0.10, expected=0.05) == 500


def test_bias_bps_negative_when_realized_below_expected() -> None:
    assert bias_bps(realized=0.30, expected=0.50) == -2000


def test_wilson_ci_known_value() -> None:
    # Wilson CI for 7/10 at 95% should be roughly (0.397, 0.892)
    lo, hi = wilson_ci(n_success=7, n_total=10, confidence=0.95)
    assert 0.38 <= lo <= 0.42
    assert 0.87 <= hi <= 0.92


def test_wilson_ci_zero_total_returns_full_range() -> None:
    lo, hi = wilson_ci(n_success=0, n_total=0, confidence=0.95)
    assert lo == 0.0
    assert hi == 1.0


def test_binomial_two_tailed_at_null() -> None:
    p = binomial_p_value(n_success=50, n_total=100, expected=0.5)
    assert math.isclose(p, 1.0, abs_tol=0.01)


def test_binomial_low_p_when_far_from_null() -> None:
    p = binomial_p_value(n_success=80, n_total=100, expected=0.5)
    assert p < 1e-8
```

- [ ] **Step 2: Confirm failure**

- [ ] **Step 3: Implement `src/predmarkbot/research/stats.py`**

```python
"""Pure statistics for favorite-longshot analysis."""
from __future__ import annotations

import math

from scipy import stats

BUCKET_WIDTH = 5
NUM_BUCKETS = 100 // BUCKET_WIDTH  # = 20


def bucket_for(price_cents: int) -> int:
    """Map a 0..99¢ price to its 5¢-wide bucket lower bound."""
    if not (0 <= price_cents <= 99):
        raise ValueError(f"price_cents must be 0..99, got {price_cents}")
    return (price_cents // BUCKET_WIDTH) * BUCKET_WIDTH


def bias_bps(*, realized: float, expected: float) -> int:
    """(realized - expected) in basis points, rounded to int.

    Positive = realized > expected (longshots winning more often than priced).
    """
    return round((realized - expected) * 10000)


def wilson_ci(
    *, n_success: int, n_total: int, confidence: float = 0.95
) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    More accurate than the normal-approximation CI for small n or
    extreme proportions. Returns (lo, hi). For n_total=0 returns (0, 1).
    """
    if n_total == 0:
        return (0.0, 1.0)
    alpha = 1.0 - confidence
    z = stats.norm.ppf(1.0 - alpha / 2.0)
    p_hat = n_success / n_total
    denom = 1.0 + (z * z) / n_total
    center = (p_hat + (z * z) / (2.0 * n_total)) / denom
    half = (
        z * math.sqrt((p_hat * (1.0 - p_hat) + (z * z) / (4.0 * n_total)) / n_total)
    ) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def binomial_p_value(
    *, n_success: int, n_total: int, expected: float
) -> float:
    """Two-tailed binomial test p-value for H0: p = expected."""
    if n_total == 0:
        return 1.0
    result = stats.binomtest(k=n_success, n=n_total, p=expected, alternative="two-sided")
    return float(result.pvalue)
```

- [ ] **Step 4: Pass + commit**

```bash
uv run pytest tests/unit/test_stats.py -v
uv run ruff check src tests && uv run mypy src
git add src/predmarkbot/research/stats.py tests/unit/test_stats.py
git commit -m "feat(research): bucket math + Wilson CI + binomial p-value"
```

---

### Task 3.3: Rebuild horizon_prices

**Files:**
- Create: `src/predmarkbot/research/analyze.py`
- Create: `tests/unit/test_analyze.py`

- [ ] **Step 1: Write failing test**

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from predmarkbot.research.analyze import rebuild_horizon_prices
from predmarkbot.research.store import ResearchStore


@pytest.mark.asyncio
async def test_rebuild_horizon_prices_populates_all_four(tmp_path: Path) -> None:
    async with ResearchStore(tmp_path / "r.db") as store:
        close = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        open_ts = close - timedelta(days=14)
        await store.upsert_market(
            ticker="X-1", event_ticker="E", series_ticker="S",
            category="weather", title="t",
            open_ts=open_ts.isoformat(),
            close_ts=close.isoformat(),
            settled_ts=close.isoformat(),
            result="yes", yes_strike=None,
        )
        # Candles at T-7d, T-24h, T-6h, T-1h
        candles = []
        for hours_back, price in [(7 * 24, 30), (24, 45), (6, 55), (1, 75)]:
            ts = (close - timedelta(hours=hours_back)).isoformat()
            candles.append((ts, price, price, price, price, 100))
        await store.insert_candlesticks(ticker="X-1", rows=candles)

        await rebuild_horizon_prices(store=store)

        async with store.conn.execute(
            "SELECT horizon, price_yes_cents FROM horizon_prices "
            "WHERE ticker='X-1' ORDER BY horizon"
        ) as cur:
            rows = await cur.fetchall()
    snaps = {r["horizon"]: r["price_yes_cents"] for r in rows}
    assert snaps == {"T-1h": 75, "T-24h": 45, "T-6h": 55, "T-7d": 30}


@pytest.mark.asyncio
async def test_rebuild_horizon_prices_inserts_null_when_no_candle(
    tmp_path: Path,
) -> None:
    async with ResearchStore(tmp_path / "r.db") as store:
        close = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        await store.upsert_market(
            ticker="X-2", event_ticker="E", series_ticker="S",
            category="weather", title="t",
            open_ts=(close - timedelta(hours=2)).isoformat(),
            close_ts=close.isoformat(),
            settled_ts=close.isoformat(),
            result="no", yes_strike=None,
        )
        # No candles inserted -- snaps should all be NULL

        await rebuild_horizon_prices(store=store)

        async with store.conn.execute(
            "SELECT count(*) FROM horizon_prices "
            "WHERE ticker='X-2' AND price_yes_cents IS NULL"
        ) as cur:
            row = await cur.fetchone()
    assert row[0] == 4
```

- [ ] **Step 2: Confirm failure**

- [ ] **Step 3: Implement `rebuild_horizon_prices`**

`src/predmarkbot/research/analyze.py`:

```python
"""Rebuild derived research tables from source data."""
from __future__ import annotations

import logging

from predmarkbot.research.horizons import HORIZON_OFFSETS, snap_to_horizon
from predmarkbot.research.stats import (
    NUM_BUCKETS,
    bias_bps,
    binomial_p_value,
    bucket_for,
    wilson_ci,
)
from predmarkbot.research.store import ResearchStore
from datetime import datetime

_log = logging.getLogger(__name__)


async def rebuild_horizon_prices(*, store: ResearchStore) -> int:
    """Drop+recreate horizon_prices for every (ticker, horizon) pair.

    Returns number of (ticker, horizon) rows written.
    """
    async with store.conn.execute(
        "SELECT ticker, close_ts FROM markets"
    ) as cur:
        markets = await cur.fetchall()

    out: list[tuple[str, str, int | None]] = []
    for m in markets:
        ticker = str(m["ticker"])
        close = datetime.fromisoformat(str(m["close_ts"]))
        candles = await store.get_candlesticks(ticker)
        for horizon in HORIZON_OFFSETS:
            price = snap_to_horizon(
                close_ts=close, candles=candles, horizon=horizon,
            )
            out.append((ticker, horizon, price))

    await store.replace_horizon_prices(out)
    _log.info(
        "rebuilt horizon_prices: %d markets * %d horizons = %d rows",
        len(markets), len(HORIZON_OFFSETS), len(out),
    )
    return len(out)
```

- [ ] **Step 4: Pass + commit**

```bash
uv run pytest tests/unit/test_analyze.py -v
uv run ruff check src tests && uv run mypy src
git add src/predmarkbot/research/analyze.py tests/unit/test_analyze.py
git commit -m "feat(research): rebuild_horizon_prices over markets x horizons"
```

---

### Task 3.4: Rebuild bucket_stats

**Files:**
- Modify: `src/predmarkbot/research/analyze.py`
- Modify: `tests/unit/test_analyze.py`

- [ ] **Step 1: Append failing test**

```python
@pytest.mark.asyncio
async def test_rebuild_bucket_stats_handles_known_distribution(
    tmp_path: Path,
) -> None:
    from predmarkbot.research.analyze import rebuild_bucket_stats
    async with ResearchStore(tmp_path / "r.db") as store:
        # Build 100 markets: 50 priced at 50¢ (resolve 50% yes), 50 at 10¢ (resolve 20% yes)
        for i in range(50):
            result = "yes" if i < 25 else "no"
            await store.upsert_market(
                ticker=f"A-{i}", event_ticker="E", series_ticker="S",
                category="weather", title="t",
                open_ts="2026-01-01T00:00:00+00:00",
                close_ts="2026-01-02T00:00:00+00:00",
                settled_ts="2026-01-02T01:00:00+00:00",
                result=result, yes_strike=None,
            )
            await store.replace_horizon_prices([(f"A-{i}", "T-6h", 50)])
        # Don't actually use replace here -- accumulate
        await store.conn.execute("DELETE FROM horizon_prices")
        rows: list[tuple[str, str, int | None]] = []
        for i in range(50):
            rows.append((f"A-{i}", "T-6h", 50))
        for i in range(50, 100):
            result = "yes" if i < 60 else "no"
            await store.upsert_market(
                ticker=f"B-{i}", event_ticker="E", series_ticker="S",
                category="sports", title="t",
                open_ts="2026-01-01T00:00:00+00:00",
                close_ts="2026-01-02T00:00:00+00:00",
                settled_ts="2026-01-02T01:00:00+00:00",
                result=result, yes_strike=None,
            )
            rows.append((f"B-{i}", "T-6h", 10))
        await store.replace_horizon_prices(rows)

        await rebuild_bucket_stats(store=store)

        async with store.conn.execute(
            "SELECT bucket_lo, n_markets, n_yes FROM bucket_stats "
            "WHERE horizon='T-6h' AND category='ALL' ORDER BY bucket_lo"
        ) as cur:
            agg = {r["bucket_lo"]: (r["n_markets"], r["n_yes"]) for r in await cur.fetchall()}
    # 50 markets at 50¢ with 25 yes
    assert agg[50] == (50, 25)
    # 50 markets at 10¢ with 10 yes
    assert agg[10] == (50, 10)


@pytest.mark.asyncio
async def test_bucket_stats_excludes_void_results(tmp_path: Path) -> None:
    from predmarkbot.research.analyze import rebuild_bucket_stats
    async with ResearchStore(tmp_path / "r.db") as store:
        await store.upsert_market(
            ticker="V-1", event_ticker="E", series_ticker="S",
            category="weather", title="t",
            open_ts="2026-01-01T00:00:00+00:00",
            close_ts="2026-01-02T00:00:00+00:00",
            settled_ts="2026-01-02T01:00:00+00:00",
            result="void", yes_strike=None,
        )
        await store.replace_horizon_prices([("V-1", "T-6h", 50)])
        await rebuild_bucket_stats(store=store)
        async with store.conn.execute("SELECT count(*) FROM bucket_stats") as cur:
            row = await cur.fetchone()
    assert row[0] == 0
```

- [ ] **Step 2: Confirm failure**

- [ ] **Step 3: Append `rebuild_bucket_stats`**

```python
# Append to src/predmarkbot/research/analyze.py:

async def rebuild_bucket_stats(*, store: ResearchStore) -> int:
    """Aggregate horizon_prices x markets into bucket_stats.

    Drops then refills bucket_stats. Returns number of rows written.
    """
    async with store.conn.execute(
        """
        SELECT m.ticker, m.category, m.result,
               hp.horizon, hp.price_yes_cents
        FROM horizon_prices hp
        JOIN markets m ON m.ticker = hp.ticker
        WHERE hp.price_yes_cents IS NOT NULL
          AND m.result IN ('yes', 'no')
        """
    ) as cur:
        rows = await cur.fetchall()

    # bucket_counts[(horizon, category, bucket_lo)] = [n_total, n_yes]
    counts: dict[tuple[str, str, int], list[int]] = {}
    for r in rows:
        horizon = str(r["horizon"])
        category = str(r["category"])
        price = int(r["price_yes_cents"])
        is_yes = r["result"] == "yes"
        lo = bucket_for(price)
        for cat_key in (category, "ALL"):
            key = (horizon, cat_key, lo)
            tally = counts.setdefault(key, [0, 0])
            tally[0] += 1
            tally[1] += int(is_yes)

    out: list[dict[str, object]] = []
    for (horizon, category, lo), (n_total, n_yes) in sorted(counts.items()):
        hi = lo + 5
        midpoint = (lo + hi) / 2
        expected = midpoint / 100.0
        realized = n_yes / n_total
        ci_lo, ci_hi = wilson_ci(n_success=n_yes, n_total=n_total)
        p = binomial_p_value(n_success=n_yes, n_total=n_total, expected=expected)
        out.append({
            "horizon": horizon, "category": category,
            "bucket_lo": lo, "bucket_hi": hi,
            "n_markets": n_total, "n_yes": n_yes,
            "realized_rate": realized, "expected_rate": expected,
            "bias_bps": bias_bps(realized=realized, expected=expected),
            "ci_lo": ci_lo, "ci_hi": ci_hi, "p_value": p,
        })
    await store.replace_bucket_stats(out)
    _log.info("rebuilt bucket_stats: %d rows", len(out))
    return len(out)
```

- [ ] **Step 4: Pass + commit**

```bash
uv run pytest tests/unit/test_analyze.py -v
uv run ruff check src tests && uv run mypy src
git add src/predmarkbot/research/analyze.py tests/unit/test_analyze.py
git commit -m "feat(research): rebuild_bucket_stats with per-category and ALL rollup"
```

---

## Phase 4 — Report

### Task 4.1: Markdown report generation

**Files:**
- Create: `src/predmarkbot/research/report.py`
- Create: `tests/unit/test_report.py`

- [ ] **Step 1: Write failing test**

```python
from __future__ import annotations

from pathlib import Path

import pytest

from predmarkbot.research.report import write_report
from predmarkbot.research.store import ResearchStore


@pytest.mark.asyncio
async def test_write_report_creates_markdown(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    async with ResearchStore(db) as store:
        # one trivial bucket_stats row to make the report non-empty
        await store.replace_bucket_stats([{
            "horizon": "T-6h", "category": "ALL",
            "bucket_lo": 50, "bucket_hi": 55,
            "n_markets": 1000, "n_yes": 500,
            "realized_rate": 0.5, "expected_rate": 0.525,
            "bias_bps": -250, "ci_lo": 0.47, "ci_hi": 0.53,
            "p_value": 0.1,
        }])
        out_dir = tmp_path / "out"
        await write_report(store=store, out_dir=out_dir)
    assert (out_dir / "report.md").exists()
    body = (out_dir / "report.md").read_text()
    assert "Favorite-Longshot Bias" in body
    assert "T-6h" in body


@pytest.mark.asyncio
async def test_write_report_handles_empty_stats(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    async with ResearchStore(db) as store:
        out_dir = tmp_path / "out"
        await write_report(store=store, out_dir=out_dir)
    body = (out_dir / "report.md").read_text()
    assert "no data" in body.lower()
```

- [ ] **Step 2: Confirm failure**

- [ ] **Step 3: Implement `src/predmarkbot/research/report.py`** (markdown only; plots come in 4.2)

```python
"""Generate the favorite-longshot research report."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from predmarkbot.research.horizons import HORIZON_OFFSETS
from predmarkbot.research.store import ResearchStore

# Strategy-suggestion threshold: only suggest if bias persists at T-6h+
# with magnitude >= 200 bps in a category with >= 1000 markets.
MIN_BIAS_BPS_FOR_STRATEGY = 200
MIN_MARKETS_FOR_STRATEGY = 1000


async def write_report(*, store: ResearchStore, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "plots").mkdir(exist_ok=True)

    async with store.conn.execute(
        "SELECT * FROM bucket_stats ORDER BY horizon, category, bucket_lo"
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]

    if not rows:
        (out_dir / "report.md").write_text(
            "# Favorite-Longshot Bias — Report\n\n"
            "no data; run `predmarkbot research pull` and "
            "`predmarkbot research analyze` first.\n"
        )
        return

    today = datetime.now(UTC).date().isoformat()
    md: list[str] = []
    md.append(f"# Favorite-Longshot Bias — Report ({today})\n")
    md.append("## Summary\n")

    # Dataset stats
    async with store.conn.execute(
        "SELECT category, count(*) AS n FROM markets "
        "WHERE result IN ('yes','no') GROUP BY category ORDER BY n DESC"
    ) as cur:
        by_cat = [(str(r["category"]), int(r["n"])) for r in await cur.fetchall()]
    total = sum(n for _, n in by_cat)
    md.append(f"- Total resolved markets analyzed: **{total}**")
    md.append("- Breakdown by category:\n")
    for cat, n in by_cat:
        md.append(f"  - `{cat}`: {n}")
    md.append("")

    # Cross-horizon table for ALL category
    md.append("## Cross-horizon bias table (all categories combined)\n")
    horizon_order = list(HORIZON_OFFSETS.keys())
    md.append(
        "| Bucket | " + " | ".join(horizon_order) + " |"
    )
    md.append("|" + "---|" * (1 + len(horizon_order)))
    all_rows = [r for r in rows if r["category"] == "ALL"]
    by_bucket: dict[int, dict[str, dict]] = {}
    for r in all_rows:
        by_bucket.setdefault(int(r["bucket_lo"]), {})[str(r["horizon"])] = r
    for lo in sorted(by_bucket):
        cells = [f"{lo:2d}-{lo+5:2d}¢"]
        for h in horizon_order:
            r = by_bucket[lo].get(h)
            if r is None:
                cells.append("—")
            else:
                bias = int(r["bias_bps"])
                p = float(r["p_value"])
                n = int(r["n_markets"])
                if n < 30:
                    cells.append(f"(n={n})")
                else:
                    marker = "**" if p < 0.01 else ""
                    cells.append(f"{marker}{bias:+d} bps{marker} (n={n})")
        md.append("| " + " | ".join(cells) + " |")
    md.append("")

    # Strategy suggestions
    md.append("## Suggested strategies\n")
    sug = _suggest_strategies(rows)
    if not sug:
        md.append(
            "No bias pattern met the strategy threshold "
            f"(≥{MIN_BIAS_BPS_FOR_STRATEGY} bps, persistent at T-6h+, "
            f"≥{MIN_MARKETS_FOR_STRATEGY} markets in category).\n"
        )
    else:
        for s in sug:
            md.append(s)
            md.append("")

    (out_dir / "report.md").write_text("\n".join(md) + "\n")


def _suggest_strategies(rows: list[dict]) -> list[str]:
    out: list[str] = []
    actionable_horizons = {"T-6h", "T-24h", "T-7d"}
    # group by (category, bucket_lo) -> {horizon: row}
    grouped: dict[tuple[str, int], dict[str, dict]] = {}
    for r in rows:
        grouped.setdefault(
            (str(r["category"]), int(r["bucket_lo"])), {},
        )[str(r["horizon"])] = r
    for (category, lo), per_h in grouped.items():
        if category == "ALL":
            continue
        ah = [h for h in actionable_horizons if h in per_h]
        if not ah:
            continue
        biases = [int(per_h[h]["bias_bps"]) for h in ah]
        ns = [int(per_h[h]["n_markets"]) for h in ah]
        if all(abs(b) >= MIN_BIAS_BPS_FOR_STRATEGY for b in biases) and all(
            (b > 0) == (biases[0] > 0) for b in biases
        ) and min(ns) >= MIN_MARKETS_FOR_STRATEGY:
            side = "NO" if biases[0] < 0 else "YES"
            out.append(
                f"### `{category}` bucket {lo}-{lo+5}¢\n"
                f"Buy **{side}** when price is in this bucket at any of "
                f"{sorted(ah)}. Persistent bias of "
                f"{biases[0]:+d} bps across horizons with n≥{min(ns)} markets."
            )
    return out
```

- [ ] **Step 4: Pass + commit**

```bash
uv run pytest tests/unit/test_report.py -v
uv run ruff check src tests && uv run mypy src
git add src/predmarkbot/research/report.py tests/unit/test_report.py
git commit -m "feat(research): markdown report with cross-horizon table + strategy sketch"
```

---

### Task 4.2: Plot generation

**Files:**
- Modify: `src/predmarkbot/research/report.py`
- Modify: `tests/unit/test_report.py`

- [ ] **Step 1: Append failing test**

```python
@pytest.mark.asyncio
async def test_write_report_creates_plots(tmp_path: Path) -> None:
    async with ResearchStore(tmp_path / "r.db") as store:
        rows = []
        for lo in range(0, 100, 5):
            mid = (lo + lo + 5) / 2
            rows.append({
                "horizon": "T-6h", "category": "ALL",
                "bucket_lo": lo, "bucket_hi": lo + 5,
                "n_markets": 200, "n_yes": int(2 * mid),
                "realized_rate": (2 * mid) / 200,
                "expected_rate": mid / 100,
                "bias_bps": 0,
                "ci_lo": 0.0, "ci_hi": 1.0, "p_value": 1.0,
            })
        await store.replace_bucket_stats(rows)
        out_dir = tmp_path / "out"
        await write_report(store=store, out_dir=out_dir)
    plots = list((out_dir / "plots").glob("*.png"))
    # One PNG per horizon present in stats (here: just T-6h)
    assert any(p.name == "bias_T-6h_ALL.png" for p in plots)
```

- [ ] **Step 2: Confirm failure**

- [ ] **Step 3: Add plot generation inside `write_report`**

Modify `report.py` — at the top, add the import and a helper, then call it inside `write_report` after the markdown is built:

```python
# Add at top of report.py (alongside existing imports):
import matplotlib
matplotlib.use("Agg")  # headless backend; required before pyplot import
import matplotlib.pyplot as plt


def _write_plot(
    *, rows: list[dict], horizon: str, category: str, out_dir: Path
) -> None:
    filtered = [
        r for r in rows
        if r["horizon"] == horizon and r["category"] == category
        and int(r["n_markets"]) >= 30
    ]
    if not filtered:
        return
    filtered.sort(key=lambda r: int(r["bucket_lo"]))
    xs = [(int(r["bucket_lo"]) + int(r["bucket_hi"])) / 2 for r in filtered]
    bias = [int(r["bias_bps"]) / 100.0 for r in filtered]  # bps -> cents
    lo_band = [(float(r["ci_lo"]) - float(r["expected_rate"])) * 100 for r in filtered]
    hi_band = [(float(r["ci_hi"]) - float(r["expected_rate"])) * 100 for r in filtered]
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.fill_between(xs, lo_band, hi_band, alpha=0.25, label="95% Wilson CI")
    ax.plot(xs, bias, "o-", label="realized − expected (¢)")
    ax.axhline(0, linestyle="--", linewidth=0.8)
    ax.set_xlabel("Bucket midpoint price (¢)")
    ax.set_ylabel("Bias (realized − expected, ¢)")
    ax.set_title(f"Bias curve · horizon={horizon} · category={category}")
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(out_dir / "plots" / f"bias_{horizon}_{category}.png", dpi=120)
    plt.close(fig)
```

And inside `write_report`, just before `(out_dir / "report.md").write_text(...)`, add:

```python
    horizons = sorted({str(r["horizon"]) for r in rows})
    categories = sorted({str(r["category"]) for r in rows})
    for h in horizons:
        for c in categories:
            _write_plot(rows=rows, horizon=h, category=c, out_dir=out_dir)
    if horizons and categories:
        md.append("## Bias curves\n")
        for h in horizons:
            md.append(f"### Horizon: {h}\n")
            md.append(f"![bias {h}](plots/bias_{h}_ALL.png)\n")
```

- [ ] **Step 4: Pass + commit**

```bash
uv run pytest tests/unit/test_report.py -v
uv run ruff check src tests && uv run mypy src
git add src/predmarkbot/research/report.py tests/unit/test_report.py
git commit -m "feat(research): matplotlib bias-curve plots (one PNG per horizon x category)"
```

---

## Phase 5 — CLI

### Task 5.1: research subcommand group + `pull`

**Files:**
- Create: `src/predmarkbot/research/cli.py`
- Modify: `src/predmarkbot/cli.py`

- [ ] **Step 1: Implement `src/predmarkbot/research/cli.py`**

```python
"""Click subcommands for `predmarkbot research`."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import click

from predmarkbot.kalshi.rest import KalshiRestClient
from predmarkbot.research.fetch import pull_all
from predmarkbot.research.store import ResearchStore

_log = logging.getLogger(__name__)


def _default_db() -> Path:
    override = os.environ.get("PREDMARKBOT_RESEARCH_DB")
    if override:
        return Path(override)
    base = Path(
        os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))
    )
    return base / "predmarkbot" / "research.db"


def _default_demo_base() -> str:
    return "https://demo-api.kalshi.co/trade-api/v2"


@click.group()
def research() -> None:
    """Historical Kalshi data + offline analysis."""


@research.command()
@click.option(
    "--from", "from_date",
    default=None,
    help="ISO date (UTC) to start from. Defaults to today-180d.",
)
@click.option(
    "--to", "to_date",
    default=None,
    help="ISO date (UTC) to end at. Defaults to today.",
)
@click.option(
    "--categories",
    default=None,
    help="Comma-separated category filter (default: all categories).",
)
@click.option("--refetch", is_flag=True, help="Re-fetch even if covered.")
def pull(
    from_date: str | None,
    to_date: str | None,
    categories: str | None,
    refetch: bool,
) -> None:
    """Fetch resolved markets + hourly candlesticks into the research DB."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    now = datetime.now(UTC)
    from_iso = (
        from_date + "T00:00:00Z" if from_date
        else (now - timedelta(days=180)).strftime("%Y-%m-%dT00:00:00Z")
    )
    to_iso = (
        to_date + "T00:00:00Z" if to_date
        else now.strftime("%Y-%m-%dT00:00:00Z")
    )
    cat_set = set(categories.split(",")) if categories else None
    asyncio.run(_run_pull(
        from_iso=from_iso, to_iso=to_iso,
        categories=cat_set, refetch=refetch,
    ))


async def _run_pull(
    *, from_iso: str, to_iso: str,
    categories: set[str] | None, refetch: bool,
) -> None:
    async with (
        KalshiRestClient(base_url=_default_demo_base(), signer=None) as rest,
        ResearchStore(_default_db()) as store,
    ):
        n_m, n_c = await pull_all(
            rest=rest, store=store,
            from_close=from_iso, to_close=to_iso,
            categories=categories, refetch=refetch,
        )
    click.echo(f"pulled {n_m} markets, {n_c} candle-fetches")
```

- [ ] **Step 2: Wire into main CLI**

Modify `src/predmarkbot/cli.py` — add this import near the existing imports:

```python
from predmarkbot.research.cli import research as _research_group
```

And register the group at module load (after the existing `@cli.command()` definitions, BEFORE `if __name__`):

```python
cli.add_command(_research_group, name="research")
```

- [ ] **Step 3: Verify**

```bash
uv run python -m predmarkbot --help
uv run python -m predmarkbot research --help
uv run python -m predmarkbot research pull --help
```

Expected: `research` listed; `pull` subcommand shows `--from`, `--to`, `--categories`, `--refetch` options.

- [ ] **Step 4: Commit**

```bash
uv run ruff check src tests && uv run mypy src
git add src/predmarkbot/research/cli.py src/predmarkbot/cli.py
git commit -m "feat(cli): research subgroup with pull subcommand"
```

---

### Task 5.2: `analyze`, `report`, `run` subcommands

**Files:**
- Modify: `src/predmarkbot/research/cli.py`

- [ ] **Step 1: Append the three subcommands**

```python
# Append to src/predmarkbot/research/cli.py:

from predmarkbot.research.analyze import (
    rebuild_bucket_stats, rebuild_horizon_prices,
)
from predmarkbot.research.report import write_report


@research.command()
def analyze() -> None:
    """Rebuild horizon_prices + bucket_stats from source tables."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    asyncio.run(_run_analyze())


async def _run_analyze() -> None:
    async with ResearchStore(_default_db()) as store:
        n_h = await rebuild_horizon_prices(store=store)
        n_b = await rebuild_bucket_stats(store=store)
    click.echo(f"rebuilt {n_h} horizon_prices, {n_b} bucket_stats")


@research.command()
@click.option(
    "--out", "out_path",
    default=None, type=click.Path(path_type=Path),
    help="Output dir. Defaults to docs/research/YYYY-MM-DD-favorite-longshot/.",
)
def report(out_path: Path | None) -> None:
    """Write report.md + plots PNGs to a dated directory."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    today = datetime.now(UTC).date().isoformat()
    out = out_path or Path("docs/research") / f"{today}-favorite-longshot"
    asyncio.run(_run_report(out_dir=out))
    click.echo(f"report written to {out}")


async def _run_report(*, out_dir: Path) -> None:
    async with ResearchStore(_default_db()) as store:
        await write_report(store=store, out_dir=out_dir)


@research.command(name="run")
@click.option("--from", "from_date", default=None)
@click.option("--to", "to_date", default=None)
@click.option("--categories", default=None)
@click.option("--refetch", is_flag=True)
@click.option("--out", "out_path", default=None, type=click.Path(path_type=Path))
@click.pass_context
def run_all(
    ctx: click.Context,
    from_date: str | None, to_date: str | None,
    categories: str | None, refetch: bool, out_path: Path | None,
) -> None:
    """End-to-end: pull → analyze → report."""
    ctx.invoke(
        pull,
        from_date=from_date, to_date=to_date,
        categories=categories, refetch=refetch,
    )
    ctx.invoke(analyze)
    ctx.invoke(report, out_path=out_path)
```

- [ ] **Step 2: Verify**

```bash
uv run python -m predmarkbot research --help
```

Expected: lists `pull`, `analyze`, `report`, `run`.

```bash
PREDMARKBOT_RESEARCH_DB=/tmp/empty.db uv run python -m predmarkbot research report --out /tmp/out
```

Expected: writes a stub report at `/tmp/out/report.md` containing "no data; run …".

- [ ] **Step 3: Commit**

```bash
uv run ruff check src tests && uv run mypy src
git add src/predmarkbot/research/cli.py
git commit -m "feat(cli): research analyze, report, run subcommands"
```

---

### Task 5.3: End-to-end integration test (opt-in)

**Files:**
- Create: `tests/integration/test_research_e2e.py`

- [ ] **Step 1: Write the test**

```python
from __future__ import annotations

import os
from pathlib import Path

import pytest

from predmarkbot.kalshi.rest import KalshiRestClient
from predmarkbot.research.analyze import (
    rebuild_bucket_stats, rebuild_horizon_prices,
)
from predmarkbot.research.fetch import pull_all
from predmarkbot.research.report import write_report
from predmarkbot.research.store import ResearchStore

pytestmark = pytest.mark.integration

DEMO_BASE = "https://demo-api.kalshi.co/trade-api/v2"


@pytest.mark.asyncio
async def test_full_pipeline_one_week_one_series(tmp_path: Path) -> None:
    """Pull 7 days of KXHIGHNY from demo, analyze, report. Bounded scope."""
    if not os.environ.get("KALSHI_INTEGRATION_OK"):
        pytest.skip("set KALSHI_INTEGRATION_OK=1 to run; hits demo network")

    db = tmp_path / "r.db"
    async with (
        KalshiRestClient(base_url=DEMO_BASE, signer=None) as rest,
        ResearchStore(db) as store,
    ):
        await pull_all(
            rest=rest, store=store,
            from_close="2026-05-24T00:00:00Z",
            to_close="2026-05-31T00:00:00Z",
            categories={"weather"},
            rate_per_sec=3.0,
        )
        n_h = await rebuild_horizon_prices(store=store)
        n_b = await rebuild_bucket_stats(store=store)
        out = tmp_path / "report"
        await write_report(store=store, out_dir=out)
    assert n_h > 0, "expected some horizon_prices rows"
    assert (out / "report.md").exists()
    assert (out / "report.md").stat().st_size > 200
```

- [ ] **Step 2: Run (opt-in)**

```bash
KALSHI_INTEGRATION_OK=1 uv run pytest tests/integration/test_research_e2e.py -v -m integration
```

Expected: passes within ~1-3 min. Skips if `KALSHI_INTEGRATION_OK` not set.

If schema mismatches surface (Kalshi field names different than the fetcher assumes), this is the place to update the fetcher accordingly. See spec Open Items #1-#4.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_research_e2e.py
git commit -m "test(research): 7-day E2E pipeline against Kalshi demo (opt-in)"
```

---

## Phase 6 — Notebooks

### Task 6.1: Notebook templates + README

**Files:**
- Create: `notebooks/README.md`
- Create: `notebooks/01_favorite_longshot_explore.ipynb`
- Create: `notebooks/02_category_drilldown.ipynb`
- Create: `notebooks/03_market_inspector.ipynb`
- Modify: `.gitignore` (notebook checkpoints already added in 0.2)

- [ ] **Step 1: Write `notebooks/README.md`**

```markdown
# notebooks

Exploratory analysis on the research data warehouse. Read-only.

## Launch

```bash
uv sync --group research
uv run jupyter lab notebooks/
```

JupyterLab opens at `http://localhost:8888`. Each notebook opens
`research.db` (path resolved from `$PREDMARKBOT_RESEARCH_DB` or the default
`~/.local/share/predmarkbot/research.db`) **read-only**.

## What's here

| Notebook | Purpose |
|---|---|
| `01_favorite_longshot_explore.ipynb` | Custom buckets, filters, alternate test stats |
| `02_category_drilldown.ipynb` | Per-category time series + comparisons |
| `03_market_inspector.ipynb` | Pick a single ticker; plot its candlestick history + horizon-price markers |

## Output stripping

A `nbstripout` pre-commit hook strips notebook outputs before commit
(installed by `uv sync --group research`). Run it once to register:

```bash
uv run nbstripout --install
```

`.ipynb_checkpoints/` is gitignored.
```

- [ ] **Step 2: Write `notebooks/01_favorite_longshot_explore.ipynb`**

This is a JSON file. Use this exact content (minified for the plan; format-on-save is fine):

```bash
cat > notebooks/01_favorite_longshot_explore.ipynb <<'IPYNB'
{
 "cells": [
  {"cell_type":"markdown","metadata":{},"source":["# Favorite-longshot exploration\n","\n","Read-only view of `research.db`. Mutate via the CLI (`predmarkbot research analyze`)."]},
  {"cell_type":"code","execution_count":null,"metadata":{},"outputs":[],"source":["import os, sqlite3\n","from pathlib import Path\n","import pandas as pd\n","\n","DB = Path(os.environ.get('PREDMARKBOT_RESEARCH_DB',\n","    str(Path.home() / '.local/share/predmarkbot/research.db')))\n","conn = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)\n","print(DB, 'connected')"]},
  {"cell_type":"code","execution_count":null,"metadata":{},"outputs":[],"source":["bucket_stats = pd.read_sql('SELECT * FROM bucket_stats', conn)\n","bucket_stats.head()"]},
  {"cell_type":"code","execution_count":null,"metadata":{},"outputs":[],"source":["# Bias curve for ALL at T-6h\n","subset = bucket_stats[(bucket_stats.horizon=='T-6h') & (bucket_stats.category=='ALL')].sort_values('bucket_lo')\n","subset[['bucket_lo','n_markets','realized_rate','expected_rate','bias_bps','p_value']]"]}
 ],
 "metadata": {"kernelspec":{"display_name":"Python 3","language":"python","name":"python3"}},
 "nbformat": 4,
 "nbformat_minor": 5
}
IPYNB
```

- [ ] **Step 3: Write `notebooks/02_category_drilldown.ipynb`**

```bash
cat > notebooks/02_category_drilldown.ipynb <<'IPYNB'
{
 "cells": [
  {"cell_type":"markdown","metadata":{},"source":["# Category drilldown"]},
  {"cell_type":"code","execution_count":null,"metadata":{},"outputs":[],"source":["import os, sqlite3\n","from pathlib import Path\n","import pandas as pd\n","import seaborn as sns\n","\n","DB = Path(os.environ.get('PREDMARKBOT_RESEARCH_DB',\n","    str(Path.home() / '.local/share/predmarkbot/research.db')))\n","conn = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)"]},
  {"cell_type":"code","execution_count":null,"metadata":{},"outputs":[],"source":["df = pd.read_sql('SELECT * FROM bucket_stats WHERE category != \"ALL\"', conn)\n","g = sns.FacetGrid(df[df.horizon=='T-6h'], col='category', col_wrap=3, height=3, sharey=True)\n","g.map_dataframe(sns.scatterplot, x='bucket_lo', y='bias_bps')\n","g.set_axis_labels('bucket lo (¢)', 'bias (bps)')"]}
 ],
 "metadata": {"kernelspec":{"display_name":"Python 3","language":"python","name":"python3"}},
 "nbformat": 4,
 "nbformat_minor": 5
}
IPYNB
```

- [ ] **Step 4: Write `notebooks/03_market_inspector.ipynb`**

```bash
cat > notebooks/03_market_inspector.ipynb <<'IPYNB'
{
 "cells": [
  {"cell_type":"markdown","metadata":{},"source":["# Market inspector\n\nPick one ticker; plot its candlestick history with horizon markers."]},
  {"cell_type":"code","execution_count":null,"metadata":{},"outputs":[],"source":["import os, sqlite3\n","from pathlib import Path\n","import pandas as pd\n","import matplotlib.pyplot as plt\n","\n","DB = Path(os.environ.get('PREDMARKBOT_RESEARCH_DB',\n","    str(Path.home() / '.local/share/predmarkbot/research.db')))\n","conn = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)"]},
  {"cell_type":"code","execution_count":null,"metadata":{},"outputs":[],"source":["TICKER = ''  # paste a ticker from the markets table\n","candles = pd.read_sql(\n","    'SELECT ts, close_yes_cents FROM candlesticks WHERE ticker=? ORDER BY ts',\n","    conn, params=(TICKER,))\n","candles['ts'] = pd.to_datetime(candles.ts)\n","ax = candles.plot(x='ts', y='close_yes_cents', figsize=(10,4))\n","ax.set_ylabel('YES price (¢)')\n","ax.set_title(f'price history · {TICKER}')"]}
 ],
 "metadata": {"kernelspec":{"display_name":"Python 3","language":"python","name":"python3"}},
 "nbformat": 4,
 "nbformat_minor": 5
}
IPYNB
```

- [ ] **Step 5: Install + register nbstripout, verify notebooks parse**

```bash
uv run --group research nbstripout --install
uv run --group research python -c "
import json
for p in ['01_favorite_longshot_explore', '02_category_drilldown', '03_market_inspector']:
    nb = json.load(open(f'notebooks/{p}.ipynb'))
    assert nb['nbformat'] == 4
print('notebooks ok')
"
```

Expected: prints `notebooks ok`.

- [ ] **Step 6: Commit**

```bash
git add notebooks/
git commit -m "feat(research): notebook templates + README; nbstripout hook"
```

---

## Phase 7 — Wrap-up

### Task 7.1: First real run + sanity check

This is a **manual** runtime task, not a code task — but document it for the engineer.

- [ ] **Step 1: Do a small real pull (1 week, weather only)**

```bash
uv run python -m predmarkbot research pull \
    --from $(date -u -d '7 days ago' +%Y-%m-%d) \
    --to $(date -u +%Y-%m-%d) \
    --categories weather
```

Expected: progress logs every ~100 markets; ~2-5 min for a week of weather.

- [ ] **Step 2: Analyze and report**

```bash
uv run python -m predmarkbot research analyze
uv run python -m predmarkbot research report
```

Expected: `docs/research/YYYY-MM-DD-favorite-longshot/report.md` + `plots/*.png`.

- [ ] **Step 3: Open the report**

Confirm:
- Summary section has nonzero counts.
- Cross-horizon table renders with `bias_bps` values.
- At least one PNG exists.

- [ ] **Step 4: If everything looks good, do the full 6-month pull**

```bash
uv run python -m predmarkbot research run
```

Expected: ~30-60 min. Watch for `_fetch_failures` count via:

```bash
sqlite3 ~/.local/share/predmarkbot/research.db \
    "SELECT count(*) FROM _fetch_failures"
```

If >50, re-run `predmarkbot research pull --refetch` to retry.

- [ ] **Step 5: Commit the resulting report**

```bash
git add docs/research/
git commit -m "docs(research): first favorite-longshot report — $(date -u +%Y-%m-%d)"
```

---

## Self-review

**Spec coverage** — every spec section maps to tasks:
- Section "Architecture" + "File structure" → Task 0.2 (skeleton) + Task 5.1 (cli wiring).
- Section "Components / store.py" → Tasks 1.1 + 1.2 + 1.3.
- Section "Components / fetch.py" → Tasks 2.1 + 2.2 + 2.3 + 2.4.
- Section "Components / horizons.py" → Task 3.1.
- Section "Components / analyze.py" → Tasks 3.3 + 3.4 (plus 3.2 for stats split-out).
- Section "Components / report.py" → Tasks 4.1 + 4.2.
- Section "CLI" → Tasks 5.1 + 5.2.
- Section "Notebooks" → Task 6.1.
- Section "Dependency partitioning" → Task 0.1.
- Section "Testing strategy" → Layer 1 unit tests are embedded in each implementation task; Layer 2 mocked-Kalshi tests are in 2.2 + 2.3 + 2.4; Layer 3 integration is Task 5.3.
- Section "Error handling" → covered by the retry-via-rest-client + `_fetch_failures` queue in 2.3.

**Placeholder scan** — no TBDs, no "implement appropriate error handling," every code step has actual code.

**Type consistency** — `bucket_lo` is consistently int; `horizon` is consistently the literal label string ("T-6h" etc.); `price_yes_cents` is consistently int or `int | None`; CIs are `tuple[float, float]`. The `categories` filter is `set[str] | None` everywhere in fetch.py + cli.py.

**Known v1 simplifications, documented in code:**
- The fetcher does not retry 5xx separately from 4xx — it relies on the existing `KalshiRestClient.retry_max=3` behavior on 5xx, and records 4xx persistent failures. Sufficient for v1; could grow more nuanced.
- `pull_all`'s ordering is metadata-then-candles, not interleaved. A market for which metadata is fetched but candlesticks fail will have `_fetch_failures` recorded, but no orphan-market cleanup. Acceptable.
- Notebooks 02 and 03 are minimal templates; expand inline as research proceeds.

---

## What's next

After this plan ships, the natural follow-ons:

1. **Read the first report.** If favorite-longshot bias exists on Kalshi at any horizon with magnitude ≥200 bps in a category with ≥1000 markets, write a "Plan 4: longshot strategy" that codifies the result as a `Strategy` subclass.
2. **Run plan A → C (low-volume anomaly research).** Same `research.db`; adds `analyze_volume.py` and `report_volume.py`. Reuses the entire data layer.
3. **Backfill incremental refresh.** Currently `pull` queries the full date range each time and relies on existing-ticker dedupe. A small enhancement could persist `last_pulled_max_close_ts` so daily reruns only fetch the new window.
