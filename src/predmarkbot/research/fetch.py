"""Kalshi historical-data fetcher: resolved markets + candlesticks."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from predmarkbot.kalshi.rest import KalshiApiError, KalshiRestClient
from predmarkbot.research.ratelimit import TokenBucket
from predmarkbot.research.store import ResearchStore

_log = logging.getLogger(__name__)


def _to_iso(ts: object) -> str:
    """Normalize Kalshi's 'Z' timestamps to '+00:00' offset form."""
    s = str(ts)
    if s.endswith("Z"):
        return s[:-1] + "+00:00"
    return s


async def _series_category(
    *,
    rest: KalshiRestClient,
    bucket: TokenBucket,
    series_ticker: str,
    cache: dict[str, str],
) -> str:
    """Look up a series's category, caching per-process. Returns 'unknown' on miss."""
    if series_ticker in cache:
        return cache[series_ticker]
    await bucket.acquire()
    try:
        data = await rest.get(f"/series/{series_ticker}")
    except KalshiApiError as exc:
        _log.warning("series lookup failed for %s: %s", series_ticker, exc)
        cache[series_ticker] = "unknown"
        return "unknown"
    raw = data.get("series", {}).get("category")
    cat = str(raw) if raw else "unknown"
    cache[series_ticker] = cat
    return cat


async def fetch_resolved_markets(
    *,
    rest: KalshiRestClient,
    store: ResearchStore,
    bucket: TokenBucket,
    from_close: str,
    to_close: str,
    categories: set[str] | None = None,
    series: set[str] | None = None,
) -> int:
    """Paginate resolved-market metadata from Kalshi and upsert into store.

    Returns total markets upserted. Categories are looked up per-series
    (Kalshi puts `category` on the series, not the market) with an in-process
    cache so each unique series is fetched at most once per pull.
    """
    # Kalshi expects min_close_ts / max_close_ts as Unix epoch ints.
    from_unix = _iso_to_unix(from_close)
    to_unix = _iso_to_unix(to_close)
    series_cache: dict[str, str] = {}
    # If user passed a series filter, iterate per-series so Kalshi can
    # filter server-side; otherwise enumerate everything.
    series_iter = sorted(series) if series else [None]
    count = 0
    for series_filter in series_iter:
        cursor = ""
        while True:
            await bucket.acquire()
            params = (
                f"?status=settled&limit=200"
                f"&min_close_ts={from_unix}&max_close_ts={to_unix}"
            )
            if series_filter:
                params += f"&series_ticker={series_filter}"
            if cursor:
                params += f"&cursor={cursor}"
            data = await rest.get(f"/markets{params}")
            markets = data.get("markets", [])
            for m in markets:
                ticker = str(m["ticker"])
                # Kalshi sometimes omits series_ticker on resolved-market metadata
                # even though it's encoded as the first hyphen-separated segment
                # of the ticker (e.g. KXHIGHNY-26MAY30-T75 -> KXHIGHNY).
                raw_series = m.get("series_ticker")
                series_ticker = (
                    str(raw_series) if raw_series else ticker.split("-", 1)[0]
                )
                # Category lives on the series, not the market. Look it up (cached).
                raw_cat = m.get("category")
                if raw_cat:
                    cat = str(raw_cat)
                else:
                    cat = await _series_category(
                        rest=rest, bucket=bucket,
                        series_ticker=series_ticker, cache=series_cache,
                    )
                if categories and cat not in categories:
                    continue
                await store.upsert_market(
                    ticker=ticker,
                    event_ticker=str(m.get("event_ticker", "")),
                    series_ticker=series_ticker,
                    category=cat,
                    title=str(m.get("title", "")),
                    open_ts=_to_iso(m.get("open_time", "")),
                    close_ts=_to_iso(m.get("close_time", "")),
                    settled_ts=_to_iso(m["settle_time"]) if m.get("settle_time") else None,
                    result=str(m.get("result", "")),
                    # Kalshi calls the threshold "floor_strike" in market
                    # metadata. We store it under the `yes_strike` column
                    # for schema compatibility. For bucketed (B-prefix)
                    # markets there's also a `cap_strike`; we use the floor
                    # as the bucket's representative strike.
                    yes_strike=_safe_float(
                        m.get("yes_strike") or m.get("floor_strike")
                    ),
                )
                count += 1
            cursor = str(data.get("cursor", "") or "")
            if not cursor:
                break
            if count % 100 == 0:
                _log.info("fetched %d markets so far", count)
    _log.info("fetched %d resolved markets in window", count)
    return count


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _iso_to_unix(ts: str) -> int:
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return int(datetime.fromisoformat(ts).timestamp())


def _unix_to_iso(unix_s: int) -> str:
    return datetime.fromtimestamp(unix_s, tz=UTC).isoformat()


async def fetch_candlesticks(
    *,
    rest: KalshiRestClient,
    store: ResearchStore,
    bucket: TokenBucket,
    ticker: str,
    series_ticker: str,
    start_ts: str,
    end_ts: str,
    period_minutes: int = 60,
) -> None:
    """Fetch hourly candlesticks for one ticker; idempotent upsert.

    Kalshi's candlestick endpoint requires the series_ticker in the path:
    /series/{series_ticker}/markets/{ticker}/candlesticks
    """
    start_unix = _iso_to_unix(start_ts)
    end_unix = _iso_to_unix(end_ts)
    await bucket.acquire()
    try:
        data = await rest.get(
            f"/series/{series_ticker}/markets/{ticker}/candlesticks"
            f"?period_interval={period_minutes}"
            f"&start_ts={start_unix}&end_ts={end_unix}"
        )
    except KalshiApiError as exc:
        await store.record_fetch_failure(
            ticker=ticker, endpoint="candlesticks", error=str(exc),
        )
        _log.warning("candlestick fetch failed for %s: %s", ticker, exc)
        return

    rows: list[tuple[str, int, int, int, int, int]] = []
    for c in data.get("candlesticks", []):
        ts = _unix_to_iso(int(c["end_period_ts"]))
        yes = c.get("yes_bid", {}) or c.get("price", {})
        rows.append((
            ts,
            int(yes.get("open", 0)),
            int(yes.get("high", 0)),
            int(yes.get("low", 0)),
            int(yes.get("close", 0)),
            int(c.get("volume", 0)),
        ))
    if rows:
        await store.insert_candlesticks(ticker=ticker, rows=rows)


async def pull_all(
    *,
    rest: KalshiRestClient,
    store: ResearchStore,
    from_close: str,
    to_close: str,
    categories: set[str] | None = None,
    series: set[str] | None = None,
    rate_per_sec: float = 5.0,
    refetch: bool = False,
) -> tuple[int, int]:
    """Orchestrate full pull: markets, then candlesticks for any market
    not already fully covered.

    Returns (n_markets, n_candle_tickers_fetched).
    """
    bucket = TokenBucket(rate_per_sec=rate_per_sec, burst=int(rate_per_sec))
    n_markets = await fetch_resolved_markets(
        rest=rest, store=store, bucket=bucket,
        from_close=from_close, to_close=to_close,
        categories=categories, series=series,
    )

    have_candles = set() if refetch else await store.tickers_with_candles()
    all_tickers = set(await store.list_market_tickers())
    todo = sorted(all_tickers - have_candles)

    n_done = 0
    for ticker in todo:
        async with store.conn.execute(
            "SELECT series_ticker, open_ts, close_ts FROM markets WHERE ticker=?",
            (ticker,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            continue
        await fetch_candlesticks(
            rest=rest, store=store, bucket=bucket,
            ticker=ticker,
            series_ticker=str(row["series_ticker"]),
            start_ts=str(row["open_ts"]),
            end_ts=str(row["close_ts"]),
        )
        n_done += 1
        if n_done % 50 == 0:
            _log.info("fetched candlesticks for %d / %d markets", n_done, len(todo))
    _log.info("pull complete: %d markets, %d candle-fetches", n_markets, n_done)
    return n_markets, n_done
