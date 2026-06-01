from __future__ import annotations

from pathlib import Path

import pytest

from predmarkbot.research.store import ResearchStore


@pytest.mark.asyncio
async def test_store_creates_schema_on_first_open(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    async with ResearchStore(db) as store:
        version = await store.schema_version()
    assert version == 2


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
        "strat_bucket_stats",
    ]


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
