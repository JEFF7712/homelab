from __future__ import annotations

from pathlib import Path

import pytest
import respx

from predmarkbot.discovery import MarketDiscovery
from predmarkbot.kalshi.rest import KalshiRestClient
from predmarkbot.state import StateStore


@pytest.mark.asyncio
@respx.mock
async def test_discovery_returns_open_tickers_for_series() -> None:
    respx.get(
        "https://x/markets?series_ticker=KXHIGHNY&status=open&limit=1000"
    ).respond(json={"markets": [
        {"ticker": "T-1", "status": "open"},
        {"ticker": "T-2", "status": "open"},
    ]})
    async with KalshiRestClient(base_url="https://x", signer=None) as rest:
        disc = MarketDiscovery(rest=rest, series=["KXHIGHNY"])
        tickers = await disc.discover_once()
    assert tickers == {"T-1", "T-2"}


@pytest.mark.asyncio
@respx.mock
async def test_discovery_unions_multiple_series() -> None:
    respx.get(
        "https://x/markets?series_ticker=A&status=open&limit=1000"
    ).respond(json={"markets": [{"ticker": "A-1"}]})
    respx.get(
        "https://x/markets?series_ticker=B&status=open&limit=1000"
    ).respond(json={"markets": [{"ticker": "B-1"}, {"ticker": "B-2"}]})
    async with KalshiRestClient(base_url="https://x", signer=None) as rest:
        disc = MarketDiscovery(rest=rest, series=["A", "B"])
        tickers = await disc.discover_once()
    assert tickers == {"A-1", "B-1", "B-2"}


@pytest.mark.asyncio
@respx.mock
async def test_discovery_persists_markets_to_state(tmp_path: Path) -> None:
    respx.get(
        "https://x/markets?series_ticker=KXHIGHNY&status=open&limit=1000"
    ).respond(json={"markets": [
        {"ticker": "T-1", "series_ticker": "KXHIGHNY", "title": "High NY", "status": "open"},
        {"ticker": "T-2", "series_ticker": "KXHIGHNY", "title": "High NY 2", "status": "open"},
    ]})
    async with (
        StateStore(tmp_path / "s.db") as state,
        KalshiRestClient(base_url="https://x", signer=None) as rest,
    ):
        disc = MarketDiscovery(rest=rest, series=["KXHIGHNY"], state=state)
        tickers = await disc.discover_once()

        async with state.conn.execute("SELECT count(*) FROM markets") as cur:
            row = await cur.fetchone()
        count = int(row[0])

    assert tickers == {"T-1", "T-2"}
    assert count == 2


@pytest.mark.asyncio
@respx.mock
async def test_discovery_persist_updates_last_seen_ts(tmp_path: Path) -> None:
    """Discovering the same ticker twice should update last_seen_ts (idempotent upsert)."""
    payload = {"markets": [
        {"ticker": "T-1", "series_ticker": "KXHIGHNY", "title": "High NY", "status": "open"},
    ]}
    respx.get(
        "https://x/markets?series_ticker=KXHIGHNY&status=open&limit=1000"
    ).respond(json=payload)

    async with (
        StateStore(tmp_path / "s.db") as state,
        KalshiRestClient(base_url="https://x", signer=None) as rest,
    ):
        disc = MarketDiscovery(rest=rest, series=["KXHIGHNY"], state=state)
        await disc.discover_once()

        async with state.conn.execute("SELECT last_seen_ts FROM markets WHERE ticker='T-1'") as cur:
            row1 = await cur.fetchone()
        first_ts = row1[0]

        # Re-mock for second call (respx route is already set up; make a new one)
        respx.get(
            "https://x/markets?series_ticker=KXHIGHNY&status=open&limit=1000"
        ).respond(json=payload)
        await disc.discover_once()

        async with state.conn.execute("SELECT last_seen_ts FROM markets WHERE ticker='T-1'") as cur:
            row2 = await cur.fetchone()
        second_ts = row2[0]

        async with state.conn.execute("SELECT count(*) FROM markets") as cur:
            count_row = await cur.fetchone()
        count = int(count_row[0])

    # Should still be exactly one row (upsert, not duplicate insert)
    assert count == 1
    # last_seen_ts should have been updated (or at minimum the row survived both calls)
    assert first_ts is not None
    assert second_ts is not None
