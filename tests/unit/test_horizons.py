from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from predmarkbot.research.horizons import (
    HORIZON_OFFSETS,
    horizon_label,
    snap_to_horizon,
)


def _candle(close_ts: datetime, close_yes: int) -> dict:
    return {
        "ts": close_ts.isoformat(),
        "open_yes_cents": close_yes,
        "high_yes_cents": close_yes,
        "low_yes_cents": close_yes,
        "close_yes_cents": close_yes,
        "volume": 1,
    }


def test_horizon_offsets_are_correct() -> None:
    expected = {
        "T-7d": timedelta(days=7),
        "T-24h": timedelta(hours=24),
        "T-6h": timedelta(hours=6),
        "T-1h": timedelta(hours=1),
    }
    assert expected == HORIZON_OFFSETS


def test_horizon_label_lists_in_order() -> None:
    labels = list(HORIZON_OFFSETS.keys())
    assert labels == ["T-7d", "T-24h", "T-6h", "T-1h"]
    assert all(horizon_label(o) in HORIZON_OFFSETS for o in HORIZON_OFFSETS.values())


def test_snap_to_exact_hour_returns_that_candle() -> None:
    close = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    candles = [
        _candle(close - timedelta(hours=h), 50 + h)
        for h in range(0, 24)
    ]
    price = snap_to_horizon(close_ts=close, candles=candles, horizon="T-6h")
    assert price == 56  # 6 hours back -> 50 + 6


def test_snap_walks_backward_through_gap() -> None:
    close = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    # Candle at T-8h (50), but no candle at T-6h
    candles = [_candle(close - timedelta(hours=8), 50)]
    price = snap_to_horizon(close_ts=close, candles=candles, horizon="T-6h")
    assert price == 50


def test_snap_returns_none_when_no_candle_in_24h_window() -> None:
    close = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    # Candle 48h before T-1h -> outside the 24h walk-back window
    candles = [_candle(close - timedelta(hours=49), 50)]
    price = snap_to_horizon(close_ts=close, candles=candles, horizon="T-1h")
    assert price is None


def test_snap_returns_none_for_empty_candles() -> None:
    close = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    assert snap_to_horizon(close_ts=close, candles=[], horizon="T-7d") is None


def test_invalid_horizon_label_raises() -> None:
    close = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    with pytest.raises(KeyError):
        snap_to_horizon(close_ts=close, candles=[], horizon="T-99d")
