# Favorite-Longshot Bias Research — Design

**Date:** 2026-05-31
**Author:** rsunderapand@wisc.edu
**Status:** Draft — pending user review

## Overview

A research project to test whether **favorite-longshot bias** — the most-replicated finding in prediction-market literature — exists on Kalshi. The bias states that markets systematically overprice longshots (low-probability contracts trading below ~10¢) and underprice favorites (high-probability contracts trading above ~90¢): horse-racing, sports books, election markets, and play-money platforms all show it, but its presence on Kalshi specifically has not been characterized.

The output is an analysis report plus a SQLite data warehouse usable by Jupyter notebooks for follow-up exploration. The live trading bot (`predmarkbot run`) is **not modified** by this project — it keeps running `ArbStrategy` in shadow mode. If the research finds an exploitable bias, a separate follow-on project will implement a strategy that trades on it.

## Goals

1. Determine whether favorite-longshot bias exists on Kalshi over the last 6 months of resolved markets.
2. If present, characterize: which price buckets, which categories, at which time horizons before market close.
3. Produce a committed, reproducible report (markdown + plots) that turns the question "does this work on Kalshi?" into an answer with confidence intervals.
4. Build the data infrastructure as **reusable for the planned follow-on** (low-volume anomaly research) so the next project doesn't re-pull Kalshi history.

## Non-goals (v1)

- No real-time trading impact. Live bot is untouched.
- No machine-learning models. Analysis is bucket counts + Wilson confidence intervals.
- No automatic strategy generation. The report *suggests* strategies in prose; humans decide whether to implement.
- No web UI. Output is markdown files + PNGs + Jupyter notebooks.
- No historical orderbook reconstruction. We use Kalshi's candlestick endpoint as the primitive.

## Decisions

Outcomes of the brainstorming clarifying-questions phase:

| # | Question | Decision | Rationale |
|---|----------|----------|-----------|
| 1 | Research target | Favorite-longshot bias first; low-volume anomalies second | Operator's choice; most-replicated finding goes first |
| 2 | Investigation style | Targeted (test for a documented inefficiency) | More likely to find a result than open exploration |
| 3 | Data scope | All Kalshi categories, last 6 months of resolved markets | ~10k-50k markets — enough for per-category breakdowns and stable bucket counts |
| 4 | Terminal-price methodology | Multiple horizons reported side-by-side: T-7d, T-24h, T-6h, T-1h | Reveals *when* a bias becomes exploitable; one extra SQL pivot, very high information value |
| 5 | Code location + consumption | Same repo (`~/predmarkbot/src/predmarkbot/research/`); both CLI and notebooks on the same DB | Research informs the live strategy; partition by deps not by repo |

## Architecture

```
Kalshi REST API (public + signed)
      │
      ▼  research.fetch  (CLI: predmarkbot research pull)
┌──────────────────────────────────────┐
│  research.db (SQLite)                │
│   markets             (metadata)     │
│   candlesticks        (hourly OHLC)  │
│   _fetch_failures     (retry queue)  │
└──────────────────────────────────────┘
      │
      ▼  research.horizons + research.analyze  (CLI: research analyze)
┌──────────────────────────────────────┐
│   horizon_prices                     │
│   bucket_stats                       │
└──────────────────────────────────────┘
      │
      ▼  research.report  (CLI: research report)
docs/research/YYYY-MM-DD-favorite-longshot/
├── report.md
└── plots/*.png
```

Single new subpackage `src/predmarkbot/research/` that is **not imported by the runtime bot**. Research depends on `pandas`, `matplotlib`, `seaborn`, `jupyterlab` — all installed via a new `[dependency-groups] research` block in `pyproject.toml`, so the runtime container image stays lean (it runs `uv sync --no-dev` and never sees these deps).

Research DB lives at `~/.local/share/predmarkbot/research.db` (override with `PREDMARKBOT_RESEARCH_DB` env var). Path is **separate from the bot's runtime `state.db`**. The research DB is single-writer (only the fetcher writes), read-many (analyze + notebooks both read).

## Components

### `research/fetch.py`

- **Purpose:** Pull resolved-market metadata + hourly candlesticks from Kalshi into the research DB.
- **Inputs:** `--from`, `--to` (date range), optional `--categories` filter, optional `--refetch` flag.
- **Behavior:**
  - Page through `GET /markets?status=settled&min_close_ts=…&max_close_ts=…&limit=200` until exhausted, inserting metadata rows.
  - For each market not already covered in `candlesticks`, fetch `GET /markets/{ticker}/candlesticks?period_interval=60&start_ts=…&end_ts=…` (hourly bars) and insert.
  - Rate-limit at 5 req/s via a token bucket. On 429: exponential backoff with jitter (base 1s, cap 60s), max 5 retries; persistent failures go to `_fetch_failures` for next-run retry.
  - Idempotent: re-running skips tickers already fully covered; resumable mid-pull via Ctrl-C handling.
  - Streams progress every 100 markets / 50 candlestick calls.
- **Depends on:** `predmarkbot.kalshi.rest` (the existing signed REST client), `research.store`.

### `research/store.py`

- **Purpose:** Thin SQLite wrapper. Schema migrations + typed CRUD.
- **Behavior:** Mirrors the pattern of `predmarkbot.state.StateStore` (async context manager with `aiosqlite`, `_schema_version` tracking). All money values stored as integer cents.
- **Schema:**

```sql
CREATE TABLE markets (
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
  fetched_at      TEXT NOT NULL
);
CREATE INDEX idx_markets_category_close ON markets(category, close_ts);

CREATE TABLE candlesticks (
  ticker          TEXT NOT NULL,
  ts              TEXT NOT NULL,
  open_yes_cents  INTEGER NOT NULL,
  high_yes_cents  INTEGER NOT NULL,
  low_yes_cents   INTEGER NOT NULL,
  close_yes_cents INTEGER NOT NULL,
  volume          INTEGER NOT NULL,
  PRIMARY KEY (ticker, ts)
);
CREATE INDEX idx_candles_ticker ON candlesticks(ticker);

CREATE TABLE _fetch_failures (
  ticker          TEXT PRIMARY KEY,
  endpoint        TEXT NOT NULL,
  last_error      TEXT NOT NULL,
  attempts        INTEGER NOT NULL,
  last_attempt_at TEXT NOT NULL
);

-- Derived (rebuilt by analyze, dropped+recreated each run)
CREATE TABLE horizon_prices (
  ticker          TEXT NOT NULL,
  horizon         TEXT NOT NULL,    -- 'T-7d' | 'T-24h' | 'T-6h' | 'T-1h'
  price_yes_cents INTEGER,
  PRIMARY KEY (ticker, horizon),
  FOREIGN KEY (ticker) REFERENCES markets(ticker)
);

CREATE TABLE bucket_stats (
  horizon         TEXT NOT NULL,
  category        TEXT NOT NULL,    -- specific category or 'ALL'
  bucket_lo       INTEGER NOT NULL, -- 0,5,10,...,95
  bucket_hi       INTEGER NOT NULL,
  n_markets       INTEGER NOT NULL,
  n_yes           INTEGER NOT NULL,
  realized_rate   REAL NOT NULL,
  expected_rate   REAL NOT NULL,
  bias_bps        INTEGER NOT NULL,
  ci_lo           REAL NOT NULL,    -- 95% Wilson lower
  ci_hi           REAL NOT NULL,    -- 95% Wilson upper
  p_value         REAL NOT NULL,    -- two-tailed binomial
  PRIMARY KEY (horizon, category, bucket_lo)
);

CREATE TABLE _schema_version (version INTEGER PRIMARY KEY);
```

### `research/horizons.py`

- **Purpose:** Given a market's close timestamp and its candlestick series, return the close price at each of T-7d / T-24h / T-6h / T-1h.
- **Algorithm:** For target_ts = close_ts − offset, find the candle whose hour bucket covers target_ts and use its `close_yes_cents`. If no such candle exists, walk backward up to 24h and use the most recent. If still nothing, return `None`. Pure function, no I/O.

### `research/analyze.py`

- **Purpose:** Rebuild `horizon_prices` + `bucket_stats` from `markets` + `candlesticks`. Idempotent; drops + recreates derived tables each run.
- **Bucketing:** 5¢-wide buckets `[0,5), [5,10), …, [95,100)` — 20 buckets total. Excludes `void` results from numerator + denominator.
- **Statistics:**
  - `realized_rate = n_yes / n_markets`
  - `expected_rate = bucket_midpoint / 100`
  - `bias_bps = round((realized_rate − expected_rate) × 10000)`
  - 95% Wilson confidence interval for `realized_rate`
  - Two-tailed binomial test p-value on `H₀: realized = expected`
- **Minimum bucket size:** Buckets with `n_markets < 30` stay in the table but are flagged (and the report omits them from plots).
- **Rollup levels:** `category` ∈ {actual category} ∪ {`'ALL'`}.

### `research/report.py`

- **Purpose:** Read `bucket_stats`, write markdown + matplotlib PNGs.
- **Output:** `docs/research/YYYY-MM-DD-favorite-longshot/{report.md, plots/*.png}`.
- **Sections of `report.md`:**
  1. **Summary** — dataset size, date range, # markets per category, per-category resolution rates.
  2. **Bias curves** — one PNG per horizon, x = bucket midpoint, y = realized − expected (cents). 95% Wilson CI as shaded band.
  3. **Cross-horizon table** — rows = buckets (ALL category), columns = horizons, cells = `bias_bps ± CI_half_width`. Bold cells with p < 0.01.
  4. **Per-category breakdown** — one row per category with ≥1000 markets, plot of bias curve at the most-actionable horizon (T-6h by default).
  5. **Suggested strategies** — deterministic rule: if a monotonic bias of magnitude ≥ `MIN_BIAS_BPS_FOR_STRATEGY` (default 200 bps = 2¢/contract) persists at T-6h or longer in a category with ≥1000 markets, generate a one-paragraph sketch ("buy NO on every <P¢ market in {category} between T-Hh and T-(H-1)h"). If no such pattern exists, the report says so explicitly.
- **Determinism:** Same DB content + same date → same report bytes. Committable to git.

### CLI (`src/predmarkbot/cli.py` additions)

Four new subcommands grouped under `predmarkbot research`:

```
predmarkbot research pull     [--from YYYY-MM-DD] [--to YYYY-MM-DD]
                              [--categories X,Y] [--refetch]
predmarkbot research analyze
predmarkbot research report   [--out PATH]
predmarkbot research run      # pull → analyze → report; flags forward to pull
```

The `run` subcommand stops at the first failure; the individual subcommands stay available for iterating on report code without re-fetching.

### Notebooks (`notebooks/` at repo root)

```
notebooks/
├── README.md                            # how to launch + db path conventions
├── 01_favorite_longshot_explore.ipynb   # poke at bucket_stats, custom buckets
├── 02_category_drilldown.ipynb          # per-category time series
└── 03_market_inspector.ipynb            # single-market candlestick + horizon-price plot
```

Each notebook opens `research.db` read-only (`sqlite3.connect(..., uri=True, mode='ro')`). Notebooks never write. `.ipynb` files are committed but their outputs are stripped via a `nbstripout` pre-commit hook installed by `uv sync --group research`.

### Dependency partitioning

`pyproject.toml` adds:

```toml
[dependency-groups]
research = [
  "pandas>=2.2",
  "matplotlib>=3.9",
  "seaborn>=0.13",
  "jupyterlab>=4.2",
  "scipy>=1.13",        # Wilson CI + binomial test
  "nbstripout>=0.7",
]
```

Runtime container image (Plan 2's Dockerfile) keeps `uv sync --frozen --no-dev --no-editable` — research deps are not installed in the deployed image.

## Data flow & runtime behavior

### `pull` walkthrough

1. Open DB, ensure schema is migrated.
2. Compute `[from, to]` date range, default to `[today-180d, today]`.
3. For each ~24h window in the range:
   - Page through `/markets?status=settled&min_close_ts=…&max_close_ts=…&limit=200`.
   - Upsert into `markets`. Idempotent on `ticker`.
4. For each market whose candlestick coverage is incomplete (no rows in `candlesticks` for that ticker, OR not enough hours to span from `open_ts` to `close_ts`):
   - Fetch hourly candles, insert.
5. Rate-limit at 5 req/s. Backoff on 429. Failures → `_fetch_failures`.
6. On SIGINT: cleanly commit, exit 0.

### `analyze` walkthrough

1. `DROP TABLE IF EXISTS horizon_prices; CREATE TABLE …` — same for `bucket_stats`.
2. For each market and each of 4 horizons, compute the snapshot price via `horizons.snap_to_horizon`. Insert into `horizon_prices`.
3. For each `(horizon, category-or-ALL)`:
   - Pull all markets with non-NULL `horizon_prices` and non-void result.
   - Group into 20 buckets, count `(n_markets, n_yes)`.
   - Compute Wilson CI and binomial p-value.
   - Insert into `bucket_stats`.

### `report` walkthrough

1. Read `bucket_stats`.
2. Generate matplotlib PNGs (one per horizon for the global view, one per qualifying category).
3. Format markdown report with the five sections above.
4. Write to `docs/research/YYYY-MM-DD-favorite-longshot/`.

## Error handling

| Failure | Response |
|---|---|
| Kalshi API 429 | Exponential backoff with jitter; max 5 retries; persistent → `_fetch_failures` |
| Kalshi API 5xx | Same as 429 |
| Kalshi API 4xx (non-429) | Log + record in `_fetch_failures` with the error body; no retry |
| Schema for a specific market differs from expected | Log warning + skip the market; record in `_fetch_failures` |
| SQLite write failure | Log + exit non-zero; mid-pull state is committed to the WAL so re-running resumes |
| Ctrl-C during pull | Commit in-progress writes, exit 0 |
| `analyze` finds no data | Skip writing derived tables (leave previous state intact); exit non-zero with explicit message |
| `report` finds no `bucket_stats` rows | Write a stub report saying "no data; run `analyze` first" |

## Testing strategy

### Layer 1 — Unit tests (`pytest`, fast, offline)

- `research/horizons.py::snap_to_horizon` — handcrafted candle sequences with gaps, edge cases (target_ts exactly at hour boundary, target_ts before first candle, target_ts after last candle).
- `research/analyze.py::bucket` — 0, 4, 5, 95, 99, 100 boundary inputs.
- `research/analyze.py::wilson_ci` — compare against known values from `scipy.stats.proportion_confint` for several `(n, k)` pairs.
- `research/analyze.py::bias_bps` — integer-rounding edges.
- `research/store.py` — schema migration round-trip; insert + fetch each table.

Target ~80% line coverage on `research/horizons.py` and `research/analyze.py`.

### Layer 2 — Integration with mocked Kalshi (`pytest`, fast, offline)

- `research/fetch.py` against `respx`-mocked Kalshi responses:
  - Paginated `/markets` (test: pages combine correctly, dedupe works).
  - Candlestick endpoint normal response (test: rows insert with correct types).
  - 429 response on first call, 200 on retry (test: backoff happens, eventual insert succeeds).
  - 429 forever (test: ends up in `_fetch_failures`, no exception).
  - Mid-pull Ctrl-C simulation (test: in-progress writes commit).

### Layer 3 — End-to-end integration (`-m integration`, opt-in, hits demo)

- Pull `KXHIGHNY` for the last 7 days from Kalshi demo, run `analyze`, run `report`, assert the report files exist and have non-zero content. Bounded scope; documents the contract against the real API.

### Not tested

- The notebooks (exploratory; regression-testing them is overkill).
- The matplotlib output appearance (pixel-diff testing is too brittle for this).

## Future work

- **Low-volume anomaly research** (Plan A → C, the next research project). Reuses the same `research.db`; adds `analyze_volume.py` and `report_volume.py`.
- **Strategy implementation** of any pattern discovered here. Goes in `src/predmarkbot/strategy/longshot.py`. Reuses every existing pipeline component (RiskManager, Executor, Notifier).
- **Incremental refresh** — current `pull` is incremental on ticker but always queries the date range. A future enhancement could persist a "last successful pull window" so daily reruns only fetch new markets.
- **Slippage modeling** — current bias math ignores execution costs beyond Kalshi's fee schedule. A future enhancement could simulate fills at top-of-book depth from the `volume` column to give "after-slippage" bias numbers.
- **Cross-market-correlation analysis** — e.g. does the bias in weather correlate with the bias in sports? Same data layer can support this.

## Open items (resolve in implementation plan)

1. **Exact Kalshi candlestick endpoint shape** — verify the `period_interval` param accepts `60` (minutes) for hourly bars; if Kalshi uses a different scheme, adjust. Hit demo during plan-writing to confirm.
2. **`category` field availability** — confirm `/markets` returns a `category` field (it does on `series`, may need a series-level join). If not, derive from `series_ticker` lookups.
3. **`yes_strike` for ranged markets** — verify the field name on Kalshi's responses; some markets may not have a strike concept (binary events).
4. **6-month date math** — the API may not support arbitrary date ranges in one call; will likely page by smaller windows. Plan should test the largest acceptable window.
5. **`MIN_BIAS_BPS_FOR_STRATEGY` default value** — proposed 200 bps (2¢/contract) but final value should account for Kalshi's fee structure. Confirm during analyze implementation.
