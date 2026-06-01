from __future__ import annotations

import os
from pathlib import Path

import pytest

from predmarkbot.kalshi.rest import KalshiRestClient
from predmarkbot.research.analyze import (
    rebuild_bucket_stats,
    rebuild_horizon_prices,
    rebuild_strat_bucket_stats,
)
from predmarkbot.research.fetch import pull_all
from predmarkbot.research.report import write_report
from predmarkbot.research.store import ResearchStore

pytestmark = pytest.mark.integration

DEMO_BASE = "https://demo-api.kalshi.co/trade-api/v2"


@pytest.mark.asyncio
async def test_full_pipeline_one_week_one_series(tmp_path: Path) -> None:
    """Pull 7 days of weather markets from demo, analyze, report. Bounded scope."""
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
        n_s = await rebuild_strat_bucket_stats(store=store)
        out = tmp_path / "report"
        await write_report(store=store, out_dir=out)
    assert n_h > 0, "expected some horizon_prices rows"
    assert n_b > 0, "expected some bucket_stats rows"
    assert n_s >= 0, "strat_bucket_stats rebuild complete (may be 0 if no multi-strike cohorts)"
    assert (out / "report.md").exists()
    assert (out / "report.md").stat().st_size > 200
