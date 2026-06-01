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
    Median is more robust to outliers than min — e.g. a series that
    mostly has 1°F spacing with one accidental 0.5°F pair shouldn't be
    bucketed at 0.5°F resolution.
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
