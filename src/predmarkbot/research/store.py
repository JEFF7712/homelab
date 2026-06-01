"""SQLite-backed research data warehouse."""
from __future__ import annotations

from pathlib import Path
from typing import Self

import aiosqlite

_SCHEMA: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS _schema_version (
        version INTEGER PRIMARY KEY
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS markets (
        ticker          TEXT PRIMARY KEY,
        event_ticker    TEXT NOT NULL,
        series_ticker   TEXT NOT NULL,
        category        TEXT NOT NULL,
        title           TEXT NOT NULL,
        open_ts         TEXT NOT NULL,
        close_ts        TEXT NOT NULL,
        settled_ts      TEXT,
        result          TEXT NOT NULL,
        yes_strike      REAL,
        fetched_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_markets_category_close
        ON markets(category, close_ts);
    """,
    """
    CREATE TABLE IF NOT EXISTS candlesticks (
        ticker          TEXT NOT NULL,
        ts              TEXT NOT NULL,
        open_yes_cents  INTEGER NOT NULL,
        high_yes_cents  INTEGER NOT NULL,
        low_yes_cents   INTEGER NOT NULL,
        close_yes_cents INTEGER NOT NULL,
        volume          INTEGER NOT NULL,
        PRIMARY KEY (ticker, ts)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_candles_ticker
        ON candlesticks(ticker);
    """,
    """
    CREATE TABLE IF NOT EXISTS _fetch_failures (
        ticker          TEXT PRIMARY KEY,
        endpoint        TEXT NOT NULL,
        last_error      TEXT NOT NULL,
        attempts        INTEGER NOT NULL,
        last_attempt_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS horizon_prices (
        ticker          TEXT NOT NULL,
        horizon         TEXT NOT NULL,
        price_yes_cents INTEGER,
        PRIMARY KEY (ticker, horizon)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS bucket_stats (
        horizon         TEXT NOT NULL,
        category        TEXT NOT NULL,
        bucket_lo       INTEGER NOT NULL,
        bucket_hi       INTEGER NOT NULL,
        n_markets       INTEGER NOT NULL,
        n_yes           INTEGER NOT NULL,
        realized_rate   REAL NOT NULL,
        expected_rate   REAL NOT NULL,
        bias_bps        INTEGER NOT NULL,
        ci_lo           REAL NOT NULL,
        ci_hi           REAL NOT NULL,
        p_value         REAL NOT NULL,
        PRIMARY KEY (horizon, category, bucket_lo)
    );
    """,
    """
CREATE TABLE IF NOT EXISTS strat_bucket_stats (
    horizon              TEXT NOT NULL,
    series_ticker        TEXT NOT NULL,
    price_bucket_lo      INTEGER NOT NULL,
    price_bucket_hi      INTEGER NOT NULL,
    distance_bucket_idx  INTEGER NOT NULL,
    strike_step          REAL NOT NULL,
    n_markets            INTEGER NOT NULL,
    n_yes                INTEGER NOT NULL,
    realized_rate        REAL NOT NULL,
    expected_rate        REAL NOT NULL,
    bias_bps             INTEGER NOT NULL,
    ci_lo                REAL NOT NULL,
    ci_hi                REAL NOT NULL,
    p_value              REAL NOT NULL,
    PRIMARY KEY (horizon, series_ticker, price_bucket_lo, distance_bucket_idx)
);
""",
]


class ResearchStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def __aenter__(self) -> Self:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._migrate()
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._conn is not None:
            await self._conn.close()

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None
        return self._conn

    async def _migrate(self) -> None:
        for stmt in _SCHEMA:
            await self.conn.execute(stmt)
        await self.conn.execute(
            "INSERT OR IGNORE INTO _schema_version(version) VALUES (2)"
        )
        await self.conn.commit()

    async def schema_version(self) -> int:
        async with self.conn.execute(
            "SELECT max(version) FROM _schema_version"
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    # ----- markets -----
    async def upsert_market(
        self,
        *,
        ticker: str,
        event_ticker: str,
        series_ticker: str,
        category: str,
        title: str,
        open_ts: str,
        close_ts: str,
        settled_ts: str | None,
        result: str,
        yes_strike: float | None,
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO markets
                (ticker, event_ticker, series_ticker, category, title,
                 open_ts, close_ts, settled_ts, result, yes_strike)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                event_ticker=excluded.event_ticker,
                series_ticker=excluded.series_ticker,
                category=excluded.category,
                title=excluded.title,
                open_ts=excluded.open_ts,
                close_ts=excluded.close_ts,
                settled_ts=excluded.settled_ts,
                result=excluded.result,
                yes_strike=excluded.yes_strike,
                fetched_at=datetime('now')
            """,
            (ticker, event_ticker, series_ticker, category, title,
             open_ts, close_ts, settled_ts, result, yes_strike),
        )
        await self.conn.commit()

    async def list_market_tickers(self) -> list[str]:
        async with self.conn.execute(
            "SELECT ticker FROM markets ORDER BY ticker"
        ) as cur:
            rows = await cur.fetchall()
        return [r["ticker"] for r in rows]

    # ----- candlesticks -----
    async def insert_candlesticks(
        self,
        *,
        ticker: str,
        rows: list[tuple[str, int, int, int, int, int]],
    ) -> None:
        """Each row: (ts, open, high, low, close, volume). Idempotent."""
        await self.conn.executemany(
            """
            INSERT OR IGNORE INTO candlesticks
                (ticker, ts, open_yes_cents, high_yes_cents,
                 low_yes_cents, close_yes_cents, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [(ticker, *row) for row in rows],
        )
        await self.conn.commit()

    async def get_candlesticks(self, ticker: str) -> list[dict[str, object]]:
        async with self.conn.execute(
            "SELECT * FROM candlesticks WHERE ticker=? ORDER BY ts",
            (ticker,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def tickers_with_candles(self) -> set[str]:
        async with self.conn.execute(
            "SELECT DISTINCT ticker FROM candlesticks"
        ) as cur:
            rows = await cur.fetchall()
        return {r["ticker"] for r in rows}

    # ----- fetch failures -----
    async def record_fetch_failure(
        self, *, ticker: str, endpoint: str, error: str
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO _fetch_failures
                (ticker, endpoint, last_error, attempts)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(ticker) DO UPDATE SET
                endpoint=excluded.endpoint,
                last_error=excluded.last_error,
                attempts=_fetch_failures.attempts + 1,
                last_attempt_at=datetime('now')
            """,
            (ticker, endpoint, error),
        )
        await self.conn.commit()

    async def list_fetch_failures(self) -> list[dict[str, object]]:
        async with self.conn.execute(
            "SELECT * FROM _fetch_failures ORDER BY ticker"
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ----- derived tables -----
    async def replace_horizon_prices(
        self, rows: list[tuple[str, str, int | None]]
    ) -> None:
        """Each row: (ticker, horizon, price_or_None). Replaces entire table."""
        await self.conn.execute("DELETE FROM horizon_prices")
        await self.conn.executemany(
            """
            INSERT INTO horizon_prices(ticker, horizon, price_yes_cents)
            VALUES (?, ?, ?)
            """,
            rows,
        )
        await self.conn.commit()

    async def replace_bucket_stats(
        self, rows: list[dict[str, object]]
    ) -> None:
        """Replaces entire bucket_stats table."""
        await self.conn.execute("DELETE FROM bucket_stats")
        if rows:
            keys = list(rows[0].keys())
            placeholders = ", ".join("?" for _ in keys)
            cols = ", ".join(keys)
            await self.conn.executemany(
                f"INSERT INTO bucket_stats ({cols}) VALUES ({placeholders})",  # noqa: S608  # keys come from our own code, not user input
                [tuple(r[k] for k in keys) for r in rows],
            )
        await self.conn.commit()

    async def replace_strat_bucket_stats(
        self, rows: list[dict[str, object]]
    ) -> None:
        """Replaces entire strat_bucket_stats table."""
        await self.conn.execute("DELETE FROM strat_bucket_stats")
        if rows:
            keys = list(rows[0].keys())
            placeholders = ", ".join("?" for _ in keys)
            cols = ", ".join(keys)
            await self.conn.executemany(
                f"INSERT INTO strat_bucket_stats ({cols}) VALUES ({placeholders})",  # noqa: S608
                [tuple(r[k] for k in keys) for r in rows],
            )
        await self.conn.commit()
