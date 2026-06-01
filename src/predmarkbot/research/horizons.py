"""Snap market candlestick history to fixed pre-close horizons."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import cast

HORIZON_OFFSETS: dict[str, timedelta] = {
    "T-7d":  timedelta(days=7),
    "T-24h": timedelta(hours=24),
    "T-6h":  timedelta(hours=6),
    "T-1h":  timedelta(hours=1),
}

_BACKWARD_SEARCH_WINDOW = timedelta(hours=24)


def horizon_label(offset: timedelta) -> str:
    """Reverse lookup: timedelta -> label. Raises KeyError if not registered."""
    for label, off in HORIZON_OFFSETS.items():
        if off == offset:
            return label
    raise KeyError(offset)


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def snap_to_horizon(
    *, close_ts: datetime, candles: Iterable[dict[str, object]], horizon: str
) -> int | None:
    """Return the close_yes_cents of the candle covering close_ts - offset[horizon].

    If no candle covers that exact hour, walk backward up to 24h for the most
    recent. If still none, return None.
    """
    target = close_ts - HORIZON_OFFSETS[horizon]
    earliest = target - _BACKWARD_SEARCH_WINDOW

    in_window: list[tuple[datetime, int]] = []
    for c in candles:
        ts = _parse_ts(str(c["ts"]))
        if earliest <= ts <= target:
            in_window.append((ts, int(cast(int, c["close_yes_cents"]))))
    if not in_window:
        return None
    in_window.sort(key=lambda x: x[0])
    return in_window[-1][1]
