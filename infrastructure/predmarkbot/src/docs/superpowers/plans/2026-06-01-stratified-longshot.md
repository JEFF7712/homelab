# Stratified Longshot Bias — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an intra-day-stratified analysis layer to the existing research pipeline that computes, per series per day, the market-implied median strike, then slices the favorite-longshot bias by (price bucket × distance-from-median bucket). Plus a one-shot manual "bigger pull" task that runs the existing CLI against 8 KXHIGH* series × 120 days and commits the resulting report.

**Architecture:** New pure-function module `predmarkbot.research.stratify`, new `strat_bucket_stats` derived table (schema v1 → v2), `rebuild_strat_bucket_stats` appended to `analyze.py`, new report section + per-series heatmaps + new strategy-suggester pass appended to `report.py`. No changes to `fetch.py`, `cli.py`, or the live bot. All ops continue through the same `pull` / `analyze` / `report` / `run` CLI commands; new output happens automatically.

**Tech Stack:** Python 3.12, `aiosqlite`, `matplotlib`, `scipy.stats` (already in `[dependency-groups] research`). No new dependencies.

---

## File structure

```
src/predmarkbot/research/
├── stratify.py             # NEW — cohort_key, strike_step_for_series,
│                           #       compute_implied_median, distance_bucket_idx
├── store.py                # MODIFY — schema v2 (strat_bucket_stats table) +
│                           #          replace_strat_bucket_stats writer
├── analyze.py              # MODIFY — append rebuild_strat_bucket_stats
├── report.py               # MODIFY — heatmap helper + section renderer +
│                           #          strategy-suggester v2
└── (cli.py, fetch.py, horizons.py, stats.py, ratelimit.py — unchanged)

tests/unit/
├── test_stratify.py        # NEW
├── test_research_store.py  # MODIFY — append schema + replace tests
├── test_analyze.py         # MODIFY — append rebuild test
└── test_report.py          # MODIFY — append heatmap + strategy-v2 tests

tests/integration/
└── test_research_e2e.py    # MODIFY — assert strat_bucket_stats > 0

notebooks/
├── README.md               # MODIFY — add 04_stratified_explore description
└── 04_stratified_explore.ipynb   # NEW
```

**Conventions used throughout this plan:**
- All commands run inside the nix dev shell. Prefix with `nix develop --command` when invoking from outside an active shell.
- Test files are kept alphabetized; existing imports get merged via ruff's autofix on commit.
- Strict mypy is on. Functions returning `Optional[X]` are spelled `X | None` (Python 3.12 union syntax).

---

## Phase 0 — Schema migration

### Task 0.1: Add `strat_bucket_stats` + schema v2 migration + writer

**Files:**
- Modify: `src/predmarkbot/research/store.py`
- Modify: `tests/unit/test_research_store.py`

- [ ] **Step 1: Append the failing tests to `tests/unit/test_research_store.py`**

```python
@pytest.mark.asyncio
async def test_v2_creates_strat_bucket_stats(tmp_path: Path) -> None:
    async with ResearchStore(tmp_path / "r.db") as store:
        version = await store.schema_version()
        async with store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='strat_bucket_stats'"
        ) as cur:
            row = await cur.fetchone()
    assert version == 2
    assert row is not None


@pytest.mark.asyncio
async def test_replace_strat_bucket_stats_clears_then_inserts(tmp_path: Path) -> None:
    async with ResearchStore(tmp_path / "r.db") as store:
        await store.replace_strat_bucket_stats([
            {
                "horizon": "T-6h", "series_ticker": "KXHIGHNY",
                "price_bucket_lo": 0, "price_bucket_hi": 5,
                "distance_bucket_idx": 2, "strike_step": 1.0,
                "n_markets": 100, "n_yes": 17,
                "realized_rate": 0.17, "expected_rate": 0.025,
                "bias_bps": 1450, "ci_lo": 0.10, "ci_hi": 0.25,
                "p_value": 1e-6,
            }
        ])
        await store.replace_strat_bucket_stats([
            {
                "horizon": "T-6h", "series_ticker": "KXHIGHNY",
                "price_bucket_lo": 0, "price_bucket_hi": 5,
                "distance_bucket_idx": 2, "strike_step": 1.0,
                "n_markets": 200, "n_yes": 34,
                "realized_rate": 0.17, "expected_rate": 0.025,
                "bias_bps": 1450, "ci_lo": 0.12, "ci_hi": 0.23,
                "p_value": 1e-12,
            }
        ])
        async with store.conn.execute(
            "SELECT n_markets FROM strat_bucket_stats"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row[0] == 200
```

- [ ] **Step 2: Confirm failure**

Run: `nix develop --command uv run pytest tests/unit/test_research_store.py -v -k 'strat or v2'`
Expected: 2 failed — `strat_bucket_stats` table doesn't exist; `replace_strat_bucket_stats` not found.

- [ ] **Step 3: Append the schema entry + bump version**

In `src/predmarkbot/research/store.py`, append a new entry to the `_SCHEMA` list (right after the existing tables) and change the INSERT in `_migrate`:

```python
# Append to _SCHEMA after the existing bucket_stats entry:
"""
CREATE TABLE IF NOT EXISTS strat_bucket_stats (
    horizon              TEXT NOT NULL,
    series_ticker        TEXT NOT NULL,
    price_bucket_lo      INTEGER NOT NULL,
    price_bucket_hi      INTEGER NOT NULL,
    distance_bucket_idx  INTEGER NOT NULL,
    strike_step          REAL NOT NULL,
    n_markets            INTEGER NOT NULL,
    n_yes                INTEGER NOT NULL,
    realized_rate        REAL NOT NULL,
    expected_rate        REAL NOT NULL,
    bias_bps             INTEGER NOT NULL,
    ci_lo                REAL NOT NULL,
    ci_hi                REAL NOT NULL,
    p_value              REAL NOT NULL,
    PRIMARY KEY (horizon, series_ticker, price_bucket_lo, distance_bucket_idx)
);
""",
```

Change the version bookkeeping in `_migrate`:

```python
async def _migrate(self) -> None:
    for stmt in _SCHEMA:
        await self.conn.execute(stmt)
    await self.conn.execute(
        "INSERT OR IGNORE INTO _schema_version(version) VALUES (2)"
    )
    await self.conn.commit()
```

- [ ] **Step 4: Append the writer method to the `ResearchStore` class**

```python
async def replace_strat_bucket_stats(
    self, rows: list[dict[str, object]]
) -> None:
    """Replaces entire strat_bucket_stats table."""
    await self.conn.execute("DELETE FROM strat_bucket_stats")
    if rows:
        keys = list(rows[0].keys())
        placeholders = ", ".join("?" for _ in keys)
        cols = ", ".join(keys)
        await self.conn.executemany(
            f"INSERT INTO strat_bucket_stats ({cols}) VALUES ({placeholders})",  # noqa: S608
            [tuple(r[k] for k in keys) for r in rows],
        )
    await self.conn.commit()
```

The `# noqa: S608` is the same pattern as `replace_bucket_stats` — column names come from internal dict keys, not user input.

- [ ] **Step 5: Run, confirm passing**

Run: `nix develop --command uv run pytest tests/unit/test_research_store.py -v`
Expected: 11 passed (9 existing + 2 new).

- [ ] **Step 6: Verify existing v1 DBs still open**

Run:
```bash
nix develop --command uv run python -c "
import asyncio
from pathlib import Path
from predmarkbot.research.store import ResearchStore
async def main():
    async with ResearchStore(Path('/home/rupan/.local/share/predmarkbot/research.db')) as store:
        version = await store.schema_version()
        async with store.conn.execute('SELECT count(*) FROM markets') as cur:
            row = await cur.fetchone()
        async with store.conn.execute('SELECT count(*) FROM strat_bucket_stats') as cur:
            row2 = await cur.fetchone()
    print(f'version={version} markets={row[0]} strat_rows={row2[0]}')
asyncio.run(main())
"
```

Expected: `version=2 markets=180 strat_rows=0` (or similar — markets count carries from the prior pilot pull; strat_bucket_stats is brand new and empty).

- [ ] **Step 7: Lint + typecheck + commit**

```bash
nix develop --command uv run ruff check src tests
nix develop --command uv run mypy src
git add src/predmarkbot/research/store.py tests/unit/test_research_store.py
git commit -m "feat(research): schema v2 — strat_bucket_stats table + writer"
```

---

## Phase 1 — Pure stratify module

### Task 1.1: stratify.py — cohort key, strike step, implied median, distance bucket

**Files:**
- Create: `src/predmarkbot/research/stratify.py`
- Create: `tests/unit/test_stratify.py`

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_stratify.py`

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from predmarkbot.research.stratify import (
    cohort_key,
    compute_implied_median,
    distance_bucket_idx,
    strike_step_for_series,
)


# ----- cohort_key -----

def test_cohort_key_groups_by_utc_date() -> None:
    a = datetime(2026, 5, 30, 23, 59, 0, tzinfo=UTC)
    b = datetime(2026, 5, 31, 0, 1, 0, tzinfo=UTC)
    assert cohort_key(a) != cohort_key(b)
    assert cohort_key(a).isoformat() == "2026-05-30"


# ----- strike_step_for_series -----

def test_strike_step_uniform_spacing() -> None:
    assert strike_step_for_series([70.0, 71.0, 72.0, 73.0]) == 1.0


def test_strike_step_irregular_spacing_returns_median() -> None:
    # diffs: 1, 1, 5, 1, 1 -> median 1.0
    assert strike_step_for_series([70.0, 71.0, 72.0, 77.0, 78.0, 79.0]) == 1.0


def test_strike_step_unsorted_input() -> None:
    assert strike_step_for_series([72.0, 70.0, 71.0]) == 1.0


def test_strike_step_single_strike_returns_none() -> None:
    assert strike_step_for_series([70.0]) is None


def test_strike_step_identical_strikes_returns_none() -> None:
    assert strike_step_for_series([70.0, 70.0, 70.0]) is None


# ----- compute_implied_median -----

def test_implied_median_picks_closest_to_50c() -> None:
    cohort = [(70.0, 5), (72.0, 30), (74.0, 48), (76.0, 75), (78.0, 95)]
    assert compute_implied_median(cohort) == 74.0


def test_implied_median_tiebreaks_to_higher_strike() -> None:
    # two strikes exactly 5¢ from 50¢: 45¢ and 55¢ → pick the higher strike
    cohort = [(70.0, 5), (74.0, 45), (76.0, 55), (80.0, 95)]
    assert compute_implied_median(cohort) == 76.0


def test_implied_median_returns_none_when_all_strikes_extreme() -> None:
    cohort = [(70.0, 1), (72.0, 3), (74.0, 5), (76.0, 95), (78.0, 99)]
    # closest to 50¢ is 5 → |5-50|=45 > 30 → undefined
    assert compute_implied_median(cohort) is None


def test_implied_median_returns_none_for_cohort_below_size_3() -> None:
    assert compute_implied_median([(70.0, 50), (72.0, 50)]) is None


# ----- distance_bucket_idx -----

def test_distance_bucket_idx_zero_at_median() -> None:
    assert distance_bucket_idx(strike=74.0, median=74.0, step=1.0) == 0


def test_distance_bucket_idx_positive_above_median() -> None:
    assert distance_bucket_idx(strike=77.0, median=74.0, step=1.0) == 3


def test_distance_bucket_idx_negative_below_median() -> None:
    assert distance_bucket_idx(strike=71.0, median=74.0, step=1.0) == -3


def test_distance_bucket_idx_non_unit_step() -> None:
    # step=0.5 -> distance of 1.0 is 2 buckets
    assert distance_bucket_idx(strike=75.0, median=74.0, step=0.5) == 2


def test_distance_bucket_idx_fractional_floor() -> None:
    # distance 1.4 / step 1.0 -> floor = 1
    assert distance_bucket_idx(strike=75.4, median=74.0, step=1.0) == 1
    # negative: -1.4 / 1.0 -> floor = -2 (Python's floor division)
    assert distance_bucket_idx(strike=72.6, median=74.0, step=1.0) == -2
```

- [ ] **Step 2: Confirm failure**

Run: `nix develop --command uv run pytest tests/unit/test_stratify.py -v`
Expected: ImportError on `predmarkbot.research.stratify`.

- [ ] **Step 3: Implement `src/predmarkbot/research/stratify.py`**

```python
"""Pure functions for intra-day cohort stratification.

Used by `predmarkbot.research.analyze.rebuild_strat_bucket_stats` to slice
the favorite-longshot bias by distance from each day's market-implied
median strike. No I/O, no global state — all functions are deterministic
on their inputs.
"""
from __future__ import annotations

import math
import statistics
from datetime import date, datetime


def cohort_key(close_ts: datetime) -> date:
    """Map a market's close_ts to its same-day cohort key (UTC date)."""
    return close_ts.astimezone().date() if close_ts.tzinfo is None else close_ts.date()


def strike_step_for_series(strikes: list[float]) -> float | None:
    """Median of |consecutive strike differences|.

    Returns None if fewer than 2 distinct strike values (no spacing
    information). The input may be in any order; we sort + dedupe first.
    """
    distinct = sorted({s for s in strikes})
    if len(distinct) < 2:
        return None
    diffs = [distinct[i + 1] - distinct[i] for i in range(len(distinct) - 1)]
    return float(statistics.median(diffs))


def compute_implied_median(
    cohort: list[tuple[float, int]],
    *,
    max_distance_to_50c: int = 30,
) -> float | None:
    """Return the cohort strike closest to a 50¢ price, or None.

    cohort: [(strike_value, price_yes_cents), ...]

    Tiebreaker when two strikes are equidistant: pick the higher strike.
    Returns None if |cohort| < 3 or no strike is within `max_distance_to_50c`
    cents of 50¢ (every strike is extreme).
    """
    if len(cohort) < 3:
        return None
    # Sort by (distance_to_50, -strike) so the smallest distance wins, and
    # within a tie the higher strike wins (because -strike is more negative).
    best = min(cohort, key=lambda x: (abs(x[1] - 50), -x[0]))
    if abs(best[1] - 50) > max_distance_to_50c:
        return None
    return float(best[0])


def distance_bucket_idx(*, strike: float, median: float, step: float) -> int:
    """floor((strike - median) / step). Negative below median, 0 at median."""
    return math.floor((strike - median) / step)
```

- [ ] **Step 4: Run, confirm passing**

Run: `nix develop --command uv run pytest tests/unit/test_stratify.py -v`
Expected: 15 passed.

- [ ] **Step 5: Lint + typecheck + commit**

```bash
nix develop --command uv run ruff check src tests
nix develop --command uv run mypy src
git add src/predmarkbot/research/stratify.py tests/unit/test_stratify.py
git commit -m "feat(research): stratify — cohort, implied median, distance bucketing"
```

---

## Phase 2 — Aggregation: rebuild_strat_bucket_stats

### Task 2.1: rebuild_strat_bucket_stats in analyze.py

**Files:**
- Modify: `src/predmarkbot/research/analyze.py`
- Modify: `tests/unit/test_analyze.py`

- [ ] **Step 1: Append failing tests to `tests/unit/test_analyze.py`**

```python
@pytest.mark.asyncio
async def test_rebuild_strat_bucket_stats_populates_cells(tmp_path: Path) -> None:
    from predmarkbot.research.analyze import rebuild_strat_bucket_stats
    async with ResearchStore(tmp_path / "r.db") as store:
        close = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        # Cohort of 5 strikes on the same day, with the 74°F strike at 50¢
        # (the implied median). 80 markets in the (0-5¢, +3 bucket) cell — 14
        # of them resolve yes.
        rows: list[tuple[str, str, int | None]] = []
        for i in range(80):
            ticker = f"KXHIGHNY-26JUN01-T77-{i}"
            result = "yes" if i < 14 else "no"
            await store.upsert_market(
                ticker=ticker, event_ticker="E",
                series_ticker="KXHIGHNY", category="Climate and Weather",
                title="t",
                open_ts=(close - timedelta(hours=24)).isoformat(),
                close_ts=close.isoformat(),
                settled_ts=close.isoformat(),
                result=result, yes_strike=77.0,
            )
            rows.append((ticker, "T-6h", 2))  # in 0-5¢ bucket
        # Add the median strike (and two flankers) so the cohort qualifies
        for strike, price, n in [(72.0, 30, 1), (74.0, 50, 1), (76.0, 20, 1)]:
            t = f"KXHIGHNY-26JUN01-T{int(strike)}"
            await store.upsert_market(
                ticker=t, event_ticker="E",
                series_ticker="KXHIGHNY", category="Climate and Weather",
                title="t",
                open_ts=(close - timedelta(hours=24)).isoformat(),
                close_ts=close.isoformat(),
                settled_ts=close.isoformat(),
                result="no", yes_strike=strike,
            )
            rows.append((t, "T-6h", price))
        await store.replace_horizon_prices(rows)

        n = await rebuild_strat_bucket_stats(store=store)
        async with store.conn.execute(
            "SELECT n_markets, n_yes, bias_bps FROM strat_bucket_stats "
            "WHERE horizon='T-6h' AND series_ticker='KXHIGHNY' "
            "AND price_bucket_lo=0 AND distance_bucket_idx=3"
        ) as cur:
            row = await cur.fetchone()
    assert n > 0
    assert row is not None
    assert row["n_markets"] == 80
    assert row["n_yes"] == 14
    # realized 0.175, expected 0.025, bias = 1500 bps
    assert row["bias_bps"] == 1500


@pytest.mark.asyncio
async def test_rebuild_strat_bucket_stats_skips_single_strike_cohort(
    tmp_path: Path,
) -> None:
    from predmarkbot.research.analyze import rebuild_strat_bucket_stats
    async with ResearchStore(tmp_path / "r.db") as store:
        close = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        await store.upsert_market(
            ticker="KXBINARY-26JUN01", event_ticker="E",
            series_ticker="KXBINARY", category="Politics",
            title="t",
            open_ts=(close - timedelta(hours=24)).isoformat(),
            close_ts=close.isoformat(),
            settled_ts=close.isoformat(),
            result="yes", yes_strike=None,
        )
        await store.replace_horizon_prices([("KXBINARY-26JUN01", "T-6h", 50)])
        n = await rebuild_strat_bucket_stats(store=store)
    assert n == 0


@pytest.mark.asyncio
async def test_rebuild_strat_bucket_stats_skips_all_extreme_cohort(
    tmp_path: Path,
) -> None:
    from predmarkbot.research.analyze import rebuild_strat_bucket_stats
    async with ResearchStore(tmp_path / "r.db") as store:
        close = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        rows: list[tuple[str, str, int | None]] = []
        for strike, price in [(70.0, 1), (72.0, 2), (74.0, 4), (76.0, 95), (78.0, 99)]:
            t = f"KXHIGHNY-26JUN01-T{int(strike)}"
            await store.upsert_market(
                ticker=t, event_ticker="E",
                series_ticker="KXHIGHNY", category="Climate and Weather",
                title="t",
                open_ts=(close - timedelta(hours=24)).isoformat(),
                close_ts=close.isoformat(),
                settled_ts=close.isoformat(),
                result="no", yes_strike=strike,
            )
            rows.append((t, "T-6h", price))
        await store.replace_horizon_prices(rows)
        n = await rebuild_strat_bucket_stats(store=store)
    assert n == 0
```

- [ ] **Step 2: Confirm failure**

Run: `nix develop --command uv run pytest tests/unit/test_analyze.py -v -k 'strat'`
Expected: 3 failed — `rebuild_strat_bucket_stats` not importable.

- [ ] **Step 3: Append the implementation to `src/predmarkbot/research/analyze.py`**

Add these imports at the top (alongside existing ones):

```python
from collections import defaultdict
from datetime import datetime

from predmarkbot.research.stratify import (
    cohort_key,
    compute_implied_median,
    distance_bucket_idx,
    strike_step_for_series,
)
```

Append the function:

```python
async def rebuild_strat_bucket_stats(*, store: ResearchStore) -> int:
    """Aggregate horizon_prices x markets into per-series, per-distance bucket stats.

    Drops then refills strat_bucket_stats. Returns number of rows written.
    """
    # 1. Compute strike_step per series (from the markets table — uses every
    # yes_strike value ever seen for the series).
    async with store.conn.execute(
        "SELECT series_ticker, yes_strike FROM markets "
        "WHERE yes_strike IS NOT NULL"
    ) as cur:
        per_series_strikes: dict[str, list[float]] = defaultdict(list)
        for row in await cur.fetchall():
            per_series_strikes[str(row["series_ticker"])].append(
                float(row["yes_strike"])
            )
    series_step: dict[str, float] = {}
    for series_ticker, strikes in per_series_strikes.items():
        step = strike_step_for_series(strikes)
        if step is not None:
            series_step[series_ticker] = step

    # 2. Pull all (market, horizon_price, result) joined rows.
    async with store.conn.execute(
        """
        SELECT m.ticker, m.series_ticker, m.close_ts, m.yes_strike, m.result,
               hp.horizon, hp.price_yes_cents
        FROM horizon_prices hp
        JOIN markets m ON m.ticker = hp.ticker
        WHERE hp.price_yes_cents IS NOT NULL
          AND m.result IN ('yes', 'no')
          AND m.yes_strike IS NOT NULL
        """
    ) as cur:
        all_rows = list(await cur.fetchall())

    # 3. Group into cohorts: (horizon, series, cohort_date) -> [(strike, price, ticker, is_yes), ...]
    Cohort = list[tuple[float, int, str, bool]]
    cohorts: dict[tuple[str, str, str], Cohort] = defaultdict(list)
    for r in all_rows:
        ts = datetime.fromisoformat(str(r["close_ts"]))
        ck = cohort_key(ts).isoformat()
        cohorts[(str(r["horizon"]), str(r["series_ticker"]), ck)].append(
            (
                float(r["yes_strike"]),
                int(r["price_yes_cents"]),
                str(r["ticker"]),
                r["result"] == "yes",
            )
        )

    # 4. For each cohort, compute implied median + distance bucket per market.
    # Group results into (horizon, series, price_bucket, distance_bucket) -> [n_total, n_yes].
    counts: dict[tuple[str, str, int, int], list[int]] = {}
    for (horizon, series, _cohort_date), members in cohorts.items():
        if series not in series_step:
            continue
        step = series_step[series]
        median = compute_implied_median(
            [(strike, price) for strike, price, _t, _y in members]
        )
        if median is None:
            continue
        for strike, price, _ticker, is_yes in members:
            price_bucket = bucket_for(price)
            dist_bucket = distance_bucket_idx(
                strike=strike, median=median, step=step
            )
            key = (horizon, series, price_bucket, dist_bucket)
            tally = counts.setdefault(key, [0, 0])
            tally[0] += 1
            tally[1] += int(is_yes)

    # 5. Compute stats per cell + write.
    out: list[dict[str, object]] = []
    for (horizon, series, price_lo, dist_idx), (n_total, n_yes) in sorted(counts.items()):
        price_hi = price_lo + 5
        midpoint = (price_lo + price_hi) / 2
        expected = midpoint / 100.0
        realized = n_yes / n_total
        ci_lo, ci_hi = wilson_ci(n_success=n_yes, n_total=n_total)
        p = binomial_p_value(n_success=n_yes, n_total=n_total, expected=expected)
        out.append({
            "horizon": horizon, "series_ticker": series,
            "price_bucket_lo": price_lo, "price_bucket_hi": price_hi,
            "distance_bucket_idx": dist_idx,
            "strike_step": series_step[series],
            "n_markets": n_total, "n_yes": n_yes,
            "realized_rate": realized, "expected_rate": expected,
            "bias_bps": bias_bps(realized=realized, expected=expected),
            "ci_lo": ci_lo, "ci_hi": ci_hi, "p_value": p,
        })
    await store.replace_strat_bucket_stats(out)
    _log.info(
        "rebuilt strat_bucket_stats: %d cells across %d series",
        len(out), len(series_step),
    )
    return len(out)
```

- [ ] **Step 4: Wire `analyze` CLI to call the new rebuild**

In `src/predmarkbot/research/cli.py`, modify `_run_analyze`:

```python
async def _run_analyze() -> None:
    async with ResearchStore(_default_db()) as store:
        n_h = await rebuild_horizon_prices(store=store)
        n_b = await rebuild_bucket_stats(store=store)
        n_s = await rebuild_strat_bucket_stats(store=store)
    click.echo(
        f"rebuilt {n_h} horizon_prices, {n_b} bucket_stats, {n_s} strat_bucket_stats"
    )
```

And add the import at the top:

```python
from predmarkbot.research.analyze import (
    rebuild_bucket_stats,
    rebuild_horizon_prices,
    rebuild_strat_bucket_stats,
)
```

- [ ] **Step 5: Run, confirm passing**

```bash
nix develop --command uv run pytest tests/unit/test_analyze.py -v
nix develop --command uv run ruff check src tests
nix develop --command uv run mypy src
```

Expected: 6 passed (3 existing + 3 new); lint/typecheck clean.

- [ ] **Step 6: Verify against the existing pilot DB**

```bash
nix develop --command uv run python -m predmarkbot research analyze
```

Expected output includes "rebuilt N strat_bucket_stats" where N is at least 1 (the pilot has 180 KXHIGHNY markets — they should produce stratified cells).

- [ ] **Step 7: Commit**

```bash
git add src/predmarkbot/research/analyze.py src/predmarkbot/research/cli.py \
        tests/unit/test_analyze.py
git commit -m "feat(research): rebuild_strat_bucket_stats — implied median + per-cell bias"
```

---

## Phase 3 — Report extensions

### Task 3.1: Heatmap plot helper

**Files:**
- Modify: `src/predmarkbot/research/report.py`
- Modify: `tests/unit/test_report.py`

- [ ] **Step 1: Append a failing test**

```python
@pytest.mark.asyncio
async def test_strat_heatmap_writes_png_when_data_present(tmp_path: Path) -> None:
    async with ResearchStore(tmp_path / "r.db") as store:
        # Synthesize a small stratified grid
        rows = []
        for price_lo in (0, 5, 10):
            for dist_idx in (-1, 0, 1):
                rows.append({
                    "horizon": "T-6h", "series_ticker": "KXHIGHNY",
                    "price_bucket_lo": price_lo, "price_bucket_hi": price_lo + 5,
                    "distance_bucket_idx": dist_idx, "strike_step": 1.0,
                    "n_markets": 500, "n_yes": 100,
                    "realized_rate": 0.2, "expected_rate": 0.05,
                    "bias_bps": 1500, "ci_lo": 0.18, "ci_hi": 0.22,
                    "p_value": 1e-12,
                })
        await store.replace_strat_bucket_stats(rows)
        out_dir = tmp_path / "out"
        await write_report(store=store, out_dir=out_dir)
    plots = list((out_dir / "plots").glob("strat_*.png"))
    assert any(p.name == "strat_KXHIGHNY_T-6h.png" for p in plots)
```

- [ ] **Step 2: Confirm failure**

`nix develop --command uv run pytest tests/unit/test_report.py -v -k strat_heatmap`
Expected: 1 failed.

- [ ] **Step 3: Add the heatmap helper to `report.py`**

```python
# Append in report.py (after the existing _write_plot helper):

def _write_strat_heatmap(
    *, rows: list[dict[str, object]], horizon: str,
    series_ticker: str, out_dir: Path,
) -> None:
    cells = [
        r for r in rows
        if r["horizon"] == horizon
        and r["series_ticker"] == series_ticker
        and int(r["n_markets"]) >= 30
    ]
    if not cells:
        return
    price_buckets = sorted({int(r["price_bucket_lo"]) for r in cells})
    dist_buckets = sorted({int(r["distance_bucket_idx"]) for r in cells})
    by_cell = {
        (int(r["price_bucket_lo"]), int(r["distance_bucket_idx"])): r
        for r in cells
    }
    bias_grid: list[list[float]] = []
    for pb in price_buckets:
        row: list[float] = []
        for db in dist_buckets:
            cell = by_cell.get((pb, db))
            row.append(
                float(cell["bias_bps"]) if cell is not None else float("nan")
            )
        bias_grid.append(row)

    fig, ax = plt.subplots(
        figsize=(max(6, 0.8 * len(dist_buckets)), max(4, 0.4 * len(price_buckets)))
    )
    im = ax.imshow(
        bias_grid, aspect="auto", origin="lower",
        cmap="RdBu_r",
    )
    ax.set_xticks(range(len(dist_buckets)))
    ax.set_xticklabels([str(d) for d in dist_buckets])
    ax.set_yticks(range(len(price_buckets)))
    ax.set_yticklabels([f"{pb}-{pb + 5}¢" for pb in price_buckets])
    ax.set_xlabel("Distance from implied median (strike-steps)")
    ax.set_ylabel("Price bucket (yes ¢)")
    ax.set_title(f"Bias heatmap · {series_ticker} · {horizon}")
    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label("bias (bps)")
    # Annotate cells with n
    for i, pb in enumerate(price_buckets):
        for j, db in enumerate(dist_buckets):
            cell = by_cell.get((pb, db))
            if cell is not None:
                ax.text(
                    j, i, f"n={int(cell['n_markets'])}",
                    ha="center", va="center", fontsize=7,
                )
    fig.tight_layout()
    fig.savefig(
        out_dir / "plots" / f"strat_{series_ticker}_{horizon}.png", dpi=120
    )
    plt.close(fig)
```

- [ ] **Step 4: Call it from `write_report`**

In `write_report`, after the existing plot-writing loop, before the strategy section:

```python
    # Stratified heatmaps
    async with store.conn.execute(
        "SELECT * FROM strat_bucket_stats"
    ) as cur:
        strat_rows = [dict(r) for r in await cur.fetchall()]
    if strat_rows:
        strat_series = sorted({str(r["series_ticker"]) for r in strat_rows})
        strat_horizons = sorted({str(r["horizon"]) for r in strat_rows})
        for s in strat_series:
            for h in strat_horizons:
                _write_strat_heatmap(
                    rows=strat_rows, horizon=h, series_ticker=s, out_dir=out_dir,
                )
```

- [ ] **Step 5: Run, confirm passing**

`nix develop --command uv run pytest tests/unit/test_report.py -v`
Expected: 4 passed (3 existing + 1 new).

- [ ] **Step 6: Commit**

```bash
git add src/predmarkbot/research/report.py tests/unit/test_report.py
git commit -m "feat(research): stratified heatmap plot per (series, horizon)"
```

---

### Task 3.2: Markdown section + strategy-suggester v2

**Files:**
- Modify: `src/predmarkbot/research/report.py`
- Modify: `tests/unit/test_report.py`

- [ ] **Step 1: Append failing tests**

```python
@pytest.mark.asyncio
async def test_report_has_stratified_section(tmp_path: Path) -> None:
    async with ResearchStore(tmp_path / "r.db") as store:
        # 1000+ stratified obs in KXHIGHNY across one cell
        rows = []
        for _ in range(50):  # 50 cells x 25 markets each = 1250 obs
            pass
        rows = [
            {
                "horizon": "T-6h", "series_ticker": "KXHIGHNY",
                "price_bucket_lo": 0, "price_bucket_hi": 5,
                "distance_bucket_idx": i, "strike_step": 1.0,
                "n_markets": 60, "n_yes": 10,
                "realized_rate": 0.167, "expected_rate": 0.025,
                "bias_bps": 1417, "ci_lo": 0.10, "ci_hi": 0.25,
                "p_value": 1e-8,
            }
            for i in range(-9, 10)  # 19 cells, ~1140 obs total
        ]
        await store.replace_strat_bucket_stats(rows)
        out_dir = tmp_path / "out"
        await write_report(store=store, out_dir=out_dir)
    body = (out_dir / "report.md").read_text()
    assert "Stratified longshot bias" in body
    assert "KXHIGHNY" in body


@pytest.mark.asyncio
async def test_strategy_v2_suggester_fires_on_persistent_cell(tmp_path: Path) -> None:
    async with ResearchStore(tmp_path / "r.db") as store:
        # Same (series, price, distance) triple shows +1500 bps at T-24h, T-6h, T-1h
        rows = []
        for horizon in ("T-24h", "T-6h", "T-1h"):
            rows.append({
                "horizon": horizon, "series_ticker": "KXHIGHNY",
                "price_bucket_lo": 0, "price_bucket_hi": 5,
                "distance_bucket_idx": 2, "strike_step": 1.0,
                "n_markets": 100, "n_yes": 17,
                "realized_rate": 0.17, "expected_rate": 0.025,
                "bias_bps": 1450, "ci_lo": 0.10, "ci_hi": 0.25,
                "p_value": 1e-8,
            })
        await store.replace_strat_bucket_stats(rows)
        out_dir = tmp_path / "out"
        await write_report(store=store, out_dir=out_dir)
    body = (out_dir / "report.md").read_text()
    # The suggester template includes the cell coordinates
    assert "KXHIGHNY" in body and "0-5¢" in body
    assert "distance bucket" in body or "strike-step" in body.lower()
```

- [ ] **Step 2: Confirm failure**

`nix develop --command uv run pytest tests/unit/test_report.py -v -k "stratified_section or strategy_v2"`
Expected: 2 failed.

- [ ] **Step 3: Add module-level constants + suggester to `report.py`**

Near the existing `MIN_BIAS_BPS_FOR_STRATEGY`:

```python
MIN_STRAT_BIAS_BPS = 300
MIN_STRAT_CELL_N = 50
SERIES_DISTANCE_UNIT: dict[str, str] = {
    "KXHIGHNY":  "°F (NYC high temp)",
    "KXHIGHCHI": "°F (Chicago high temp)",
    "KXHIGHLAX": "°F (LAX high temp)",
    "KXHIGHMIA": "°F (Miami high temp)",
    "KXHIGHATL": "°F (Atlanta high temp)",
    "KXHIGHDEN": "°F (Denver high temp)",
    "KXHIGHHOU": "°F (Houston high temp)",
    "KXHIGHPHX": "°F (Phoenix high temp)",
}
```

Add the suggester helper:

```python
def _suggest_strat_strategies(rows: list[dict[str, object]]) -> list[str]:
    out: list[str] = []
    persistence_horizons = {"T-24h", "T-6h", "T-1h"}
    # Group by (series, price_bucket_lo, distance_bucket_idx) -> {horizon: row}
    by_triple: dict[tuple[str, int, int], dict[str, dict[str, object]]] = {}
    for r in rows:
        series = str(r["series_ticker"])
        price_lo = int(r["price_bucket_lo"])
        dist_idx = int(r["distance_bucket_idx"])
        by_triple.setdefault((series, price_lo, dist_idx), {})[
            str(r["horizon"])
        ] = r
    for (series, price_lo, dist_idx), per_h in sorted(by_triple.items()):
        eligible = {h: per_h[h] for h in persistence_horizons if h in per_h}
        if len(eligible) < 2:
            continue
        biases = [int(eligible[h]["bias_bps"]) for h in eligible]
        ns = [int(eligible[h]["n_markets"]) for h in eligible]
        ps = [float(eligible[h]["p_value"]) for h in eligible]
        same_sign = all((b > 0) == (biases[0] > 0) for b in biases)
        passes = (
            same_sign
            and min(abs(b) for b in biases) >= MIN_STRAT_BIAS_BPS
            and min(ns) >= MIN_STRAT_CELL_N
            and max(ps) < 0.01
        )
        if not passes:
            continue
        side = "YES" if biases[0] > 0 else "NO"
        step_units = SERIES_DISTANCE_UNIT.get(series, "strike-steps")
        out.append(
            f"### `{series}` — price bucket {price_lo}-{price_lo + 5}¢, "
            f"distance bucket {dist_idx:+d} {step_units}\n"
            f"Buy **{side}** when a market in this cell is observed at "
            f"any of {sorted(eligible)}. Persistent bias of "
            f"{biases[0]:+d} bps across horizons; min n={min(ns)}; "
            f"max p={max(ps):.1e}."
        )
    return out
```

Add the markdown section + integrate the suggester. Find the existing "## Suggested strategies" block in `write_report` and adjust it to look like this:

```python
    # ----- Stratified section -----
    if strat_rows:
        md.append("## Stratified longshot bias\n")
        per_series_n: dict[str, int] = {}
        for r in strat_rows:
            per_series_n[str(r["series_ticker"])] = (
                per_series_n.get(str(r["series_ticker"]), 0)
                + int(r["n_markets"])
            )
        for s in sorted(per_series_n):
            total = per_series_n[s]
            if total >= 1000:
                md.append(f"### {s} ({total} observations)\n")
                for h in sorted({str(r["horizon"]) for r in strat_rows}):
                    md.append(f"![{s} {h}](plots/strat_{s}_{h}.png)\n")
            else:
                md.append(
                    f"- *{s}: {total} stratified observations — "
                    f"too few for plot; see notebook 04 for raw data.*"
                )
        md.append("")

    # ----- Suggested strategies (existing + new v2) -----
    md.append("## Suggested strategies\n")
    sug = _suggest_strategies(rows)
    sug_v2 = _suggest_strat_strategies(strat_rows) if strat_rows else []
    if not sug and not sug_v2:
        md.append(
            "No bias pattern met the strategy threshold "
            f"(≥{MIN_BIAS_BPS_FOR_STRATEGY} bps un-stratified or "
            f"≥{MIN_STRAT_BIAS_BPS} bps stratified, persistent at T-6h+, "
            f"≥{MIN_MARKETS_FOR_STRATEGY} markets / "
            f"≥{MIN_STRAT_CELL_N} per cell).\n"
        )
    else:
        for s in sug:
            md.append(s)
            md.append("")
        for s in sug_v2:
            md.append(s)
            md.append("")
```

(Read the current `write_report` to splice the section in cleanly — the variable name for the rows used by `_suggest_strategies` may differ.)

- [ ] **Step 4: Run, confirm passing**

`nix develop --command uv run pytest tests/unit/test_report.py -v`
Expected: 6 passed (4 existing + 2 new).

- [ ] **Step 5: Commit**

```bash
nix develop --command uv run ruff check src tests
nix develop --command uv run mypy src
git add src/predmarkbot/research/report.py tests/unit/test_report.py
git commit -m "feat(research): stratified report section + per-cell strategy suggester v2"
```

---

## Phase 4 — Notebook

### Task 4.1: notebooks/04_stratified_explore.ipynb + README update

**Files:**
- Create: `notebooks/04_stratified_explore.ipynb`
- Modify: `notebooks/README.md`

- [ ] **Step 1: Create the notebook**

```bash
cat > notebooks/04_stratified_explore.ipynb <<'IPYNB'
{
 "cells": [
  {"cell_type":"markdown","metadata":{},"source":["# Stratified longshot exploration\n","\n","Slices `strat_bucket_stats` by series, distance bucket, and price bucket."]},
  {"cell_type":"code","execution_count":null,"metadata":{},"outputs":[],"source":["import os, sqlite3\n","from pathlib import Path\n","import pandas as pd\n","import seaborn as sns\n","import matplotlib.pyplot as plt\n","\n","DB = Path(os.environ.get('PREDMARKBOT_RESEARCH_DB',\n","    str(Path.home() / '.local/share/predmarkbot/research.db')))\n","conn = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)"]},
  {"cell_type":"code","execution_count":null,"metadata":{},"outputs":[],"source":["strat = pd.read_sql('SELECT * FROM strat_bucket_stats', conn)\n","strat.groupby(['series_ticker', 'horizon']).size().unstack(fill_value=0)"]},
  {"cell_type":"code","execution_count":null,"metadata":{},"outputs":[],"source":["# Heatmap for one series at T-6h\n","series = strat.series_ticker.value_counts().index[0]\n","sub = strat[(strat.series_ticker==series) & (strat.horizon=='T-6h')]\n","pivot = sub.pivot_table(index='price_bucket_lo', columns='distance_bucket_idx', values='bias_bps', aggfunc='first')\n","sns.heatmap(pivot, cmap='RdBu_r', center=0, annot=True, fmt='.0f', cbar_kws={'label': 'bias (bps)'})\n","plt.title(f'{series} · T-6h · bias (bps)')"]},
  {"cell_type":"code","execution_count":null,"metadata":{},"outputs":[],"source":["# Top 20 cells by absolute bias (n>=50)\n","strat[(strat.n_markets>=50) & (strat.p_value<0.01)].reindex(\n","    strat[(strat.n_markets>=50) & (strat.p_value<0.01)].bias_bps.abs().sort_values(ascending=False).index\n",")[['horizon','series_ticker','price_bucket_lo','distance_bucket_idx','n_markets','bias_bps','p_value']].head(20)"]}
 ],
 "metadata": {"kernelspec":{"display_name":"Python 3","language":"python","name":"python3"}},
 "nbformat": 4,
 "nbformat_minor": 5
}
IPYNB
```

- [ ] **Step 2: Update `notebooks/README.md`** — append a new row to the table:

```markdown
| `04_stratified_explore.ipynb` | Per-series heatmaps + top stratified cells from `strat_bucket_stats` |
```

- [ ] **Step 3: Verify the notebook parses**

```bash
nix develop --command uv run --group research python -c "
import json
nb = json.load(open('notebooks/04_stratified_explore.ipynb'))
assert nb['nbformat'] == 4
print('notebook ok')
"
```

Expected: `notebook ok`.

- [ ] **Step 4: Commit**

```bash
git add notebooks/04_stratified_explore.ipynb notebooks/README.md
git commit -m "feat(research): notebook 04 — stratified bias exploration"
```

---

## Phase 5 — Integration test extension

### Task 5.1: Extend the e2e test to assert strat_bucket_stats > 0

**Files:**
- Modify: `tests/integration/test_research_e2e.py`

- [ ] **Step 1: Modify the assertion**

In `tests/integration/test_research_e2e.py::test_full_pipeline_one_week_one_series`, after the existing `n_b = await rebuild_bucket_stats(store=store)` line, add:

```python
        n_s = await rebuild_strat_bucket_stats(store=store)
```

Add the import at the top of the file (alongside the other research imports):

```python
from predmarkbot.research.analyze import (
    rebuild_bucket_stats,
    rebuild_horizon_prices,
    rebuild_strat_bucket_stats,
)
```

And add an assertion near the existing ones:

```python
    assert n_s > 0, "expected some strat_bucket_stats cells"
```

- [ ] **Step 2: Skip-path still works**

```bash
nix develop --command uv run pytest tests/integration/test_research_e2e.py -v -m integration
```

Expected: 1 skipped (without `KALSHI_INTEGRATION_OK`).

- [ ] **Step 3: Commit**

```bash
nix develop --command uv run ruff check tests
git add tests/integration/test_research_e2e.py
git commit -m "test(research): e2e asserts strat_bucket_stats populated"
```

---

## Phase 6 — Bigger empirical pull (manual gate)

### Task 6.1: Run the prod pull + commit the report

This is the only non-code task — same shape as Task 7 of the previous research plan.

- [ ] **Step 1: Verify the existing prod credentials still work**

```bash
nix develop --command uv run python -c "
import asyncio
from predmarkbot.kalshi.rest import KalshiRestClient
async def main():
    async with KalshiRestClient(base_url='https://api.elections.kalshi.com/trade-api/v2', signer=None) as c:
        data = await c.get('/series/KXHIGHNY')
    print('prod reachable, series category:', data['series']['category'])
asyncio.run(main())
"
```

Expected: `prod reachable, series category: Climate and Weather`.

- [ ] **Step 2: Wipe the existing research DB so this pull is the clean record**

```bash
rm -f ~/.local/share/predmarkbot/research.db
```

- [ ] **Step 3: Run the bigger pull**

```bash
nix develop --command bash -c '
  uv run python -m predmarkbot research run \
    --env prod \
    --from $(date -u -d "120 days ago" +%Y-%m-%d) \
    --to   $(date -u +%Y-%m-%d) \
    --series KXHIGHNY,KXHIGHCHI,KXHIGHLAX,KXHIGHMIA,KXHIGHATL,KXHIGHDEN,KXHIGHHOU,KXHIGHPHX \
    --rate 3
'
```

Expected wall-clock: ~25-45 min. Logs stream progress every 100 markets. Watch for 429s (the REST client retries; persistent failures land in `_fetch_failures`).

If 429s are frequent enough that the pull is slowing dramatically, abort and re-run with `--rate 2`.

- [ ] **Step 4: Sanity-check the pull**

```bash
nix develop --command uv run python -c "
import sqlite3
c = sqlite3.connect('/home/rupan/.local/share/predmarkbot/research.db')
print('total markets:', c.execute('SELECT count(*) FROM markets').fetchone()[0])
print('by series:')
for s,n in c.execute('SELECT series_ticker, count(*) FROM markets GROUP BY series_ticker ORDER BY 2 DESC').fetchall():
    print(f'  {s}: {n}')
print('strat cells:', c.execute('SELECT count(*) FROM strat_bucket_stats').fetchone()[0])
print('total stratified observations:', c.execute('SELECT sum(n_markets) FROM strat_bucket_stats').fetchone()[0])
"
```

Expected: ≥1000 markets total across the 8 series; ≥100 strat cells; total stratified observations ≥1000 (likely 5,000-10,000).

- [ ] **Step 5: Inspect the report**

```bash
ls docs/research/$(date -u +%Y-%m-%d)-favorite-longshot/
head -60 docs/research/$(date -u +%Y-%m-%d)-favorite-longshot/report.md
```

Expected: the report has all four sections — summary, cross-horizon table, stratified longshot bias section with per-series heatmaps, suggested strategies (likely with at least one auto-suggestion now that n ≥ 1000).

- [ ] **Step 6: Commit the report**

```bash
git add docs/research/$(date -u +%Y-%m-%d)-favorite-longshot/
git commit -m "docs(research): $(date -u +%Y-%m-%d) KXHIGH* 120-day stratified report

Eight KXHIGH* weather series (NYC, Chicago, LAX, Miami, Atlanta,
Denver, Houston, Phoenix) over the last 120 days. Stratified by
distance from the day's market-implied median strike."
```

---

## Self-review

**Spec coverage:** every spec section maps to a task:
- Architecture / new module → Task 1.1.
- Schema → Task 0.1.
- `rebuild_strat_bucket_stats` → Task 2.1.
- Report extensions (heatmap, markdown section, strategy v2) → Tasks 3.1 + 3.2.
- Notebook → Task 4.1.
- Integration test → Task 5.1.
- Bigger pull → Task 6.1.
- Math (implied median + distance bucketing) → Task 1.1.
- Per-series strike step → Task 1.1 + Task 2.1.
- Eligibility filter (≥3 strikes in cohort, ≤30¢ to 50¢) → tested in Task 1.1 + Task 2.1.

**Placeholder scan:** no TBDs, no "implement appropriate error handling", every code step has actual code.

**Type consistency:** `series_ticker` is consistently `str`; `bucket_lo`/`bucket_hi` are `int`; `distance_bucket_idx` is `int`; `strike_step` is `float`; `bias_bps` is `int`. Function signatures (`compute_implied_median(cohort: list[tuple[float, int]])`, `distance_bucket_idx(*, strike, median, step)`, `strike_step_for_series(strikes: list[float])`) match between definitions in Task 1.1 and uses in Task 2.1.

**Known limitations carried forward** (already in the spec's Open Items):
- `MIN_STRAT_BIAS_BPS=300` and `MIN_STRAT_CELL_N=50` are first-cut defaults; may need tuning after Task 6.1's report lands.
- Cross-series distance normalization is intentionally out of scope.
- `yes_strike` NULL markets are silently excluded from stratification (test_rebuild_strat_bucket_stats_skips_single_strike_cohort exercises this path).

---

## What's next (post Task 6.1)

1. **Read the committed report.** Inspect the per-series heatmaps. The expected finding (based on the pilot) is the +14 to +20¢ bias band concentrated in the +1 to +3 strike-step columns. If instead the bias is uniform across all distance buckets, the strategy is simpler ("any 0-5¢ KXHIGH market"); if concentrated, the strategy targets only the actionable cells.
2. **Plan 5 (likely): live `LongshotStrategy` implementation.** A new `Strategy` subclass that, on each `OrderbookUpdate` for a KX-prefixed market, checks if the market's price + current implied-median distance falls in a known-biased cell from the report, emits a `TradeIntent` if so. Reuses the existing `RiskManager` / `Executor` / `Notifier` pipeline.
3. **Periodic re-pull.** The report should be regenerated monthly to catch any decay in the bias. Cron a `predmarkbot research run --env prod ...` on the user's homelab + commit the result via GitOps.
