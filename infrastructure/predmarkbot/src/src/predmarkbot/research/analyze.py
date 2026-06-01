"""Rebuild derived research tables from source data."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

from predmarkbot.research.horizons import HORIZON_OFFSETS, snap_to_horizon
from predmarkbot.research.stats import (
    bias_bps,
    binomial_p_value,
    bucket_for,
    wilson_ci,
)
from predmarkbot.research.store import ResearchStore
from predmarkbot.research.stratify import (
    cohort_key,
    compute_implied_median,
    distance_bucket_idx,
    strike_step_for_series,
)

_log = logging.getLogger(__name__)


async def rebuild_horizon_prices(*, store: ResearchStore) -> int:
    """Drop+recreate horizon_prices for every (ticker, horizon) pair.

    Returns number of (ticker, horizon) rows written.
    """
    async with store.conn.execute(
        "SELECT ticker, close_ts FROM markets"
    ) as cur:
        markets = list(await cur.fetchall())

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

    # 3. Group into cohorts: (horizon, series, cohort_date) ->
    #    [(strike, price, ticker, is_yes), ...]
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
