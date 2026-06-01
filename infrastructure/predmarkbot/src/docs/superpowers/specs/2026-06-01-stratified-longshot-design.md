# Stratified Longshot Bias Research — Design

**Date:** 2026-06-01
**Author:** rsunderapand@wisc.edu
**Status:** Draft — pending user review

## Overview

A follow-on to the favorite-longshot research that landed 2026-05-31. The pilot found a +1417 bps gap between realized and expected resolution rates on 0-5¢ KXHIGHNY markets at n=180 (one series, 30 days) — too small to clear the strategy-suggester threshold but visually unambiguous. This project extends the same pipeline in two ways:

1. **Scale up the empirical pull.** Run the existing CLI against the 8 largest KXHIGH* weather series (NYC, Chicago, LAX, Miami, Atlanta, Denver, Houston, Phoenix) for the last 120 days. Expected ~5,800 resolved markets — well above the 1,000-market threshold the suggester requires.

2. **Stratify the analysis by intra-day distance from market-implied median.** Same-day cohorts of strikes share an underlying realized value (e.g. that day's NYC high temperature). The market's prices imply its own median guess — and the favorite-longshot bias may be uniform across all "out-of-the-money" strikes, or concentrated in the strikes that are just barely out of reach. Determining which is the precondition to writing a tradable strategy: "buy YES on all <5¢ KXHIGH markets" is a different rule from "buy YES on <5¢ KXHIGH markets within N strike-steps of the day's market-implied median."

The live bot is **not modified** by this project. A live strategy implementation is a follow-up plan after the stratified report tells us what shape the bias takes.

## Goals

1. Confirm at scale (≥1000 markets) whether the favorite-longshot bias documented in the pilot generalizes to the multi-city KXHIGH* weather corpus.
2. Determine whether the bias is **uniform** across all out-of-the-money strikes or **concentrated** at specific distance bands from the market's intra-day implied median.
3. Produce a deterministic report identifying the (price-bucket, distance-bucket) cells with the largest persistent bias, sufficient sample, and statistical significance.
4. Output is reusable for the follow-on strategy plan: the cells the report highlights become the explicit "where to trade" specification.

## Non-goals (this project)

- No live trading. The live bot keeps running `ArbStrategy` in shadow mode.
- No external weather data (NOAA, OpenWeather). Stratification is intra-day from Kalshi's own price data.
- No cross-series distance normalization. Distance is per-series in the strike's native units (°F for KXHIGH*, $ for crypto, etc.).
- No automatic strategy code generation. The report's "Suggested strategies" section produces prose; a human (or the next plan) decides whether to codify.
- No changes to `bucket_stats` or the existing un-stratified report section. The new analysis is purely additive.

## Decisions

Outcomes of the brainstorming phase:

| # | Question | Decision | Rationale |
|---|----------|----------|-----------|
| 1 | Scope | A (bigger pull) + B (stratified analysis); C (strategy code) deferred | Confirm + understand shape before committing to code |
| 2 | Stratification dimension | Intra-day market-implied median | Pure intra-day, no external data, captures "conditional on the market's own opinion" |
| 3 | Which series get stratified | Data-driven: any cohort with ≥3 strikes resolving on the same `close_ts` | Captures multi-threshold pattern across all series automatically; no hardcoded list to maintain |
| 4 | Output design | New `strat_bucket_stats` table + new report section + new notebook; existing tables/sections untouched | Additive change; the previous report remains valid |

## Architecture

```
Existing pipeline (unchanged):
  fetch → markets, candlesticks → analyze → bucket_stats → report (un-stratified section)

New layer (additive):
  analyze → strat_bucket_stats → report (stratified section + new strategy suggestions)
                ▲
                │
        src/predmarkbot/research/stratify.py
        (pure-function module: cohort discovery, implied median, distance buckets)
```

### File-level changes

```
src/predmarkbot/research/
├── stratify.py             # NEW — pure functions: median, distance, bucketing
├── store.py                # MODIFY — add strat_bucket_stats schema + writer
├── analyze.py              # MODIFY — append rebuild_strat_bucket_stats() that
│                           #          calls stratify.py and writes via store
├── report.py               # MODIFY — append stratified-section renderer +
│                           #          heatmap plot helper + v2 strategy suggester
└── (other modules unchanged)

tests/unit/
├── test_stratify.py        # NEW
├── test_analyze.py         # MODIFY — append rebuild_strat_bucket_stats tests
├── test_report.py          # MODIFY — append heatmap + strategy-v2 tests
└── test_research_store.py  # MODIFY — append strat_bucket_stats CRUD test

notebooks/
└── 04_stratified_explore.ipynb   # NEW

docs/research/YYYY-MM-DD-favorite-longshot/
└── (existing report + new "Stratified longshot bias" section)
```

No changes to `fetch.py`, `horizons.py`, `stats.py`, `ratelimit.py`, or `cli.py`. No new CLI surface — `pull`, `analyze`, `report`, `run` work exactly as before; the new output happens automatically.

## Components

### `research/stratify.py`

Pure-function module. Imports only standard library + `predmarkbot.research.stats` (for re-using `bucket_for`, `wilson_ci`, `binomial_p_value`).

```python
def cohort_key(close_ts: datetime) -> date:
    """Map a market's close_ts to its same-day cohort key (UTC date)."""

def strike_step_for_series(strikes: list[float]) -> float | None:
    """Median of |consecutive strike differences| across the input.

    Returns None if fewer than 2 distinct strikes (no spacing to compute).
    """

def compute_implied_median(
    cohort: list[tuple[float, int]],   # [(strike, price_yes_cents), ...]
    *,
    max_distance_to_50c: int = 30,
) -> float | None:
    """Pick the strike whose price is closest to 50¢; tiebreak to higher strike.

    Returns None if the cohort has < 3 markets OR the closest-to-50¢ strike
    is more than `max_distance_to_50c` away from 50¢ (all strikes extreme).
    """

def distance_bucket_idx(*, strike: float, median: float, step: float) -> int:
    """floor((strike - median) / step). Negative below median, 0 at median."""
```

### `research/store.py` additions

```sql
CREATE TABLE strat_bucket_stats (
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
```

Schema version bumps from 1 to 2. Add `replace_strat_bucket_stats(rows: list[dict])` paralleling `replace_bucket_stats`.

### `research/analyze.py` additions

`async def rebuild_strat_bucket_stats(*, store: ResearchStore) -> int`:

1. Compute per-series `strike_step` once from the `markets` table (median of consecutive `yes_strike` differences within each series, across all dates).
2. For each `(horizon, series_ticker, cohort_date)`:
   - Pull all `(ticker, yes_strike, price_yes_cents, result)` rows where the market resolved (not void) AND has a non-NULL horizon price.
   - Skip cohorts with <3 strikes.
   - Compute `implied_median = compute_implied_median(cohort)`. If None, skip.
   - For each market in the cohort, compute `distance_bucket_idx`. Group by `(price_bucket, distance_bucket)`.
3. Aggregate over all cohorts for the same `(horizon, series_ticker, price_bucket_lo, distance_bucket_idx)`: counts, Wilson CI, p-value, bias_bps.
4. Write via `store.replace_strat_bucket_stats`.

Called from `analyze` CLI immediately after `rebuild_bucket_stats`.

### `research/report.py` additions

**New helper:** `_write_strat_heatmap(rows, *, horizon, series_ticker, out_dir)` — matplotlib heatmap (rows = price bucket, cols = distance bucket, color = bias_bps).

**Section renderer** in `write_report`: after the existing "Cross-horizon bias table" section, before "Suggested strategies":

```
## Stratified longshot bias

For each series with ≥1000 stratified market-observations, a heatmap of
realized − expected (bps) per (price bucket, distance bucket) cell at each
horizon, plus a "top biased cells" table.

Series with insufficient data get a one-line note.
```

**Strategy-suggestion v2** — runs in addition to the existing suggester:

For each `(series, price_bucket, distance_bucket)` triple, suggest a trade rule if:
- `bias_bps ≥ MIN_STRAT_BIAS_BPS` (default 300)
- `n_markets ≥ MIN_STRAT_CELL_N` (default 50)
- `p_value < 0.01`
- Same bias sign at ≥2 of {T-24h, T-6h, T-1h}

Output template includes both the raw cell description ("price bucket X, distance bucket Y") and a human-readable translation when a per-series template exists:

```python
SERIES_DISTANCE_UNIT = {
    "KXHIGHNY":  "°F above/below the day's implied median temperature",
    "KXHIGHCHI": "°F above/below the day's implied median temperature",
    # ... other KXHIGH* series default to the same template
    "KXBTCD":    "$ above/below the day's implied median BTC price",
}
```

If no template for a series, the suggestion still renders with just raw bucket numbers.

### Notebook addition

`notebooks/04_stratified_explore.ipynb` — same boilerplate as 01-03 (read-only SQLite), uses pandas to load `strat_bucket_stats`, default cells plot a single-series heatmap and show top cells by `|bias_bps|`.

One-paragraph addition to `notebooks/README.md`.

## Math: implied median + distance buckets

For a same-day cohort C in series S at horizon H:

```
prices = [(strike_i, price_yes_cents_i) for i in C if price_yes_cents_i is not None]
if |prices| < 3: return None_for_all
median_market = argmin_{i in prices} |price_yes_cents_i - 50|
if |price_yes_cents_{median_market} - 50| > 30: return None_for_all
implied_median = strike_{median_market}

step = median over S of |strike_{j} - strike_{j-1}| for adjacent strikes
       (computed once per series)

for each market_i in C:
    distance_i = strike_i - implied_median
    distance_bucket_idx_i = floor(distance_i / step)
```

**Tiebreaking** in median selection: when multiple strikes have the same `|price - 50|`, pick the higher strike (deterministic, makes the +/- sign of `distance_bucket_idx` well-defined when the cohort is split evenly).

**Strike step computation:** within a series, sort `yes_strike` values from all markets ever seen, compute consecutive differences, take the median. This gives the typical step regardless of how strikes are spaced in any single cohort. Cached per-series at the start of `rebuild_strat_bucket_stats`.

## Data flow

```
1. predmarkbot research pull --env prod --series KXHIGHNY,...  (existing)
   → populates markets + candlesticks tables

2. predmarkbot research analyze  (modified)
   → rebuild_horizon_prices()         (existing)
   → rebuild_bucket_stats()           (existing)
   → rebuild_strat_bucket_stats()     (NEW)

3. predmarkbot research report  (modified)
   → existing summary + cross-horizon table + bias curves
   → NEW: stratified longshot bias section (per-series heatmaps + top-cells table)
   → existing suggested strategies + NEW per-cell strategy suggestions
```

## Error handling

| Failure | Response |
|---|---|
| Series has all markets in single-strike cohorts | Skip — produces 0 rows in `strat_bucket_stats` for that series |
| Cohort has only 1-2 strikes | Skip cohort — no implied median computable |
| Cohort has 3+ strikes but all prices > 80¢ or < 20¢ | Skip cohort — no strike is close enough to 50¢ |
| Series has 0 strikes with `yes_strike` populated | Skip — no strike_step computable |
| Empty `strat_bucket_stats` (no qualifying cohorts in the whole DB) | Report section renders a single line: "No series qualified for stratification." |
| `analyze` runs against a v1 schema DB | Schema migration adds the new table; existing tables untouched |

## Testing

### Layer 1 — Unit tests

- `test_stratify.py`:
  - `compute_implied_median` — happy path, tiebreaker (two strikes equidistant to 50¢ → higher wins), all-extreme cohort returns None, fewer-than-3 cohort returns None.
  - `strike_step_for_series` — uniform spacing, irregular spacing, single strike returns None, identical strikes returns None.
  - `distance_bucket_idx` — strike == median → 0, strike > median → positive, strike < median → negative, exact boundaries (e.g. strike = median + step → idx == 1).
  - `cohort_key` — close_ts near UTC midnight buckets to the right date.

- `test_analyze.py` (appended):
  - `rebuild_strat_bucket_stats` — handcrafted cohort with known result rates per (price, distance) cell; verify the table populates with the right counts.
  - Single-strike cohort produces 0 rows.
  - Two cohorts on different days for the same series → strike_step computed across both.

- `test_research_store.py` (appended):
  - `replace_strat_bucket_stats` round-trip.
  - Migration from v1 → v2 schema preserves existing data and adds `strat_bucket_stats`.

- `test_report.py` (appended):
  - Heatmap PNG written when `strat_bucket_stats` has ≥1000 obs for a series.
  - Heatmap PNG NOT written below threshold; report includes the "too few" line.
  - Strategy-v2 suggestion text appears when a synthetic cell crosses the threshold.

### Layer 2 — Integration (opt-in)

Extend `tests/integration/test_research_e2e.py`:
- After the KXHIGHNY 7-day pull + analyze, assert `strat_bucket_stats` has at least one row (proves the new path ran end-to-end against real data).

### What's NOT tested

- The notebook (exploratory, not regression-worthy).
- Exact pixel content of the heatmap.

## Implementation order

The plan should sequence tasks so the project is shippable at every commit:

1. Storage migration (schema v2 + `replace_strat_bucket_stats`).
2. Pure-function stratify module + its unit tests.
3. `rebuild_strat_bucket_stats` in analyze.py + tests.
4. Report extensions (markdown section + heatmap + strategy-v2 suggester) + tests.
5. Notebook + README update.
6. The bigger pull (manual gate): run the CLI with the 8 series + 120 days, commit the resulting `docs/research/` directory.

## Future work (explicitly out of this project)

- **Live strategy implementation.** Once the stratified report identifies specific (price, distance) cells with persistent bias and adequate sample, write `src/predmarkbot/strategy/longshot.py` as a Strategy subclass. Single follow-up plan.
- **Cross-series normalization.** If the stratified analysis shows different shapes per series (e.g. weather vs crypto), consider expressing distance in "z-score" or "fraction-of-strike-range" units for cross-series aggregation.
- **External signal joining.** NOAA forecast comparison would let us validate that the "implied median" tracks real expectations — research-grade enrichment, not blocking.
- **Cohort-time-of-day analysis.** The implied median can shift through the day as info arrives. Heatmaps at multiple horizons already show this implicitly; an explicit "implied-median-trajectory" analysis is a worthwhile extension.

## Open items (resolve in implementation plan)

1. **Default `MIN_STRAT_BIAS_BPS` and `MIN_STRAT_CELL_N`** — proposed 300 bps and 50 markets respectively; final values may need tuning after the first real pull.
2. **Heatmap colormap range** — fixed at e.g. ±2000 bps for cross-series comparability, or auto-scale per plot? Default to auto-scale for clarity within each plot; the report's prose will quote the absolute numbers anyway.
3. **What if `yes_strike` is NaN on some markets?** Spec assumes `yes_strike` is populated by the existing fetcher. The fetcher does store `yes_strike` from `m.get("yes_strike")` via `_safe_float`; if it's null on certain market types we'll see NULL rows in `markets.yes_strike` and need to filter them at stratify time. Verify and document in the plan.
4. **`strike_step` for series with irregular strike spacing** — median is fine for KXHIGH* (1°F apart). For crypto with mixed spacings (some markets at $0.05, others at $0.10), the median is reasonable but the bucketing will be less interpretable. Document this caveat.
5. **The bigger pull's `--rate`** — 3 req/s worked for the 30-day pilot. 8 series × 120 days is ~30× the data — confirm rate limits don't trip at scale. If they do, lower to 2 req/s and accept the longer wall-clock.
