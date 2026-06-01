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


@pytest.mark.asyncio
async def test_write_report_creates_plots(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    async with ResearchStore(db) as store:
        rows = [
            {
                "horizon": "T-6h",
                "category": "ALL",
                "bucket_lo": lo,
                "bucket_hi": lo + 5,
                "n_markets": 50,
                "n_yes": 25,
                "realized_rate": 0.5,
                "expected_rate": 0.525,
                "bias_bps": -250,
                "ci_lo": 0.47,
                "ci_hi": 0.53,
                "p_value": 0.1,
            }
            for lo in range(5, 105, 5)  # 20 buckets at 5,10,...,100
        ]
        await store.replace_bucket_stats(rows)
        out_dir = tmp_path / "out"
        await write_report(store=store, out_dir=out_dir)
    assert (out_dir / "plots" / "bias_T-6h_ALL.png").exists()


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


@pytest.mark.asyncio
async def test_report_has_stratified_section(tmp_path: Path) -> None:
    async with ResearchStore(tmp_path / "r.db") as store:
        # 1000+ stratified obs in KXHIGHNY across multiple cells
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
        # Same (series, price, distance) triple shows +1450 bps at T-24h, T-6h, T-1h
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
    assert "KXHIGHNY" in body and "0-5¢" in body
    assert "distance bucket" in body or "strike-step" in body.lower()
