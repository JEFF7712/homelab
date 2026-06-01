from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from predmarkbot.research.analyze import rebuild_bucket_stats, rebuild_horizon_prices
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


@pytest.mark.asyncio
async def test_rebuild_bucket_stats_handles_known_distribution(
    tmp_path: Path,
) -> None:
    """50 markets at 50¢ with 25 yes + 50 markets at 10¢ with 10 yes."""
    async with ResearchStore(tmp_path / "r.db") as store:
        close = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        open_ts = (close - timedelta(days=14)).isoformat()
        close_iso = close.isoformat()
        settled_iso = close.isoformat()

        hp_rows: list[tuple[str, str, int]] = []

        # Insert 50 markets at 50¢: 25 yes, 25 no
        for i in range(50):
            result = "yes" if i < 25 else "no"
            ticker = f"M50-{i}"
            await store.upsert_market(
                ticker=ticker, event_ticker="E", series_ticker="S",
                category="sports", title="t",
                open_ts=open_ts, close_ts=close_iso, settled_ts=settled_iso,
                result=result, yes_strike=None,
            )
            hp_rows.append((ticker, "T-24h", 50))

        # Insert 50 markets at 10¢: 10 yes, 40 no
        for i in range(50):
            result = "yes" if i < 10 else "no"
            ticker = f"M10-{i}"
            await store.upsert_market(
                ticker=ticker, event_ticker="E", series_ticker="S",
                category="sports", title="t",
                open_ts=open_ts, close_ts=close_iso, settled_ts=settled_iso,
                result=result, yes_strike=None,
            )
            hp_rows.append((ticker, "T-24h", 10))

        await store.replace_horizon_prices(hp_rows)

        n_rows = await rebuild_bucket_stats(store=store)

        async with store.conn.execute(
            """
            SELECT bucket_lo, n_markets, n_yes, realized_rate
            FROM bucket_stats
            WHERE horizon='T-24h' AND category='sports'
            ORDER BY bucket_lo
            """
        ) as cur:
            rows = await cur.fetchall()

    assert n_rows > 0
    by_lo = {r["bucket_lo"]: r for r in rows}
    # bucket_for(50) = 50, bucket_for(10) = 10
    row50 = by_lo[50]
    assert row50["n_markets"] == 50
    assert row50["n_yes"] == 25
    assert abs(row50["realized_rate"] - 0.5) < 1e-9

    row10 = by_lo[10]
    assert row10["n_markets"] == 50
    assert row10["n_yes"] == 10
    assert abs(row10["realized_rate"] - 0.2) < 1e-9


@pytest.mark.asyncio
async def test_bucket_stats_excludes_void_results(tmp_path: Path) -> None:
    """A void market should not appear in bucket_stats (result not in yes/no)."""
    async with ResearchStore(tmp_path / "r.db") as store:
        close = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        open_ts = (close - timedelta(days=14)).isoformat()
        close_iso = close.isoformat()
        settled_iso = close.isoformat()

        await store.upsert_market(
            ticker="VOID-1", event_ticker="E", series_ticker="S",
            category="politics", title="t",
            open_ts=open_ts, close_ts=close_iso, settled_ts=settled_iso,
            result="void", yes_strike=None,
        )
        await store.replace_horizon_prices([("VOID-1", "T-24h", 50)])

        n_rows = await rebuild_bucket_stats(store=store)

        async with store.conn.execute(
            "SELECT count(*) FROM bucket_stats"
        ) as cur:
            row = await cur.fetchone()

    assert n_rows == 0
    assert row[0] == 0


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
        # Add the median strike (and two flankers) so the cohort qualifies.
        # All flankers are 1°F apart so median-of-consecutive-diffs = 1.0,
        # which gives distance_bucket_idx = +3 for the 77°F strikes above.
        for strike, price, _n in [(73.0, 30, 1), (74.0, 50, 1), (75.0, 20, 1)]:
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
