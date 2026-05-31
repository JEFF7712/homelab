"""Startup-time clock skew check against an external HTTP reference."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from email.utils import parsedate_to_datetime

import httpx

_REFERENCE_URL = "https://time.cloudflare.com/"


class ClockSkewError(RuntimeError):
    """Raised when local clock disagrees with reference by more than the threshold."""


async def check_clock_skew(
    *,
    now_provider: Callable[[], datetime],
    max_skew_seconds: int = 5,
    reference_url: str = _REFERENCE_URL,
    timeout_seconds: float = 5.0,
) -> float:
    """Return signed skew (local - server) in seconds, or raise ClockSkewError."""
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.get(reference_url)
    except httpx.HTTPError as exc:
        raise ClockSkewError(f"could not reach {reference_url}: {exc}") from exc

    date_header = resp.headers.get("Date")
    if not date_header:
        raise ClockSkewError(f"{reference_url} returned no Date header")

    server_time = parsedate_to_datetime(date_header)
    local_time = now_provider()
    skew = (local_time - server_time).total_seconds()
    if abs(skew) > max_skew_seconds:
        raise ClockSkewError(
            f"clock skew {skew:+.1f}s exceeds max {max_skew_seconds}s"
        )
    return skew
