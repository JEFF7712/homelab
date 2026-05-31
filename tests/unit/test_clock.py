from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from predmarkbot.clock import ClockSkewError, check_clock_skew


@pytest.mark.asyncio
@respx.mock
async def test_clock_within_tolerance_returns_skew_seconds() -> None:
    server_time = datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC)
    respx.get("https://time.cloudflare.com/").respond(
        headers={"Date": server_time.strftime("%a, %d %b %Y %H:%M:%S GMT")},
    )
    skew = await check_clock_skew(now_provider=lambda: server_time, max_skew_seconds=5)
    assert abs(skew) < 1


@pytest.mark.asyncio
@respx.mock
async def test_clock_skew_too_large_raises() -> None:
    server_time = datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC)
    local_time = datetime(2026, 5, 30, 12, 0, 30, tzinfo=UTC)  # 30s ahead
    respx.get("https://time.cloudflare.com/").respond(
        headers={"Date": server_time.strftime("%a, %d %b %Y %H:%M:%S GMT")},
    )
    with pytest.raises(ClockSkewError) as exc:
        await check_clock_skew(now_provider=lambda: local_time, max_skew_seconds=5)
    assert "skew" in str(exc.value).lower()


@pytest.mark.asyncio
@respx.mock
async def test_clock_check_handles_network_error() -> None:
    respx.get("https://time.cloudflare.com/").mock(side_effect=httpx.ConnectError("nope"))
    with pytest.raises(ClockSkewError):
        await check_clock_skew(now_provider=lambda: datetime.now(UTC))
