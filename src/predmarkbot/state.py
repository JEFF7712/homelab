"""SQLite-backed durable state. Single source of truth across restarts."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Self

import aiosqlite

from predmarkbot.events import Fill, Side, TradeOrder

_SCHEMA: list[str] = [
    # v1
    """
    CREATE TABLE IF NOT EXISTS _schema_version (
        version INTEGER PRIMARY KEY
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS markets (
        ticker        TEXT PRIMARY KEY,
        series_ticker TEXT NOT NULL,
        title         TEXT NOT NULL,
        status        TEXT NOT NULL,
        last_seen_ts  TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS orderbook_snapshots (
        ticker      TEXT NOT NULL,
        ts          TEXT NOT NULL,
        yes_levels  TEXT NOT NULL,
        no_levels   TEXT NOT NULL,
        PRIMARY KEY (ticker, ts)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        client_order_id TEXT PRIMARY KEY,
        ticker          TEXT NOT NULL,
        side            TEXT NOT NULL,
        price_cents     INTEGER NOT NULL,
        size            INTEGER NOT NULL,
        status          TEXT NOT NULL,
        submitted_at    TEXT NOT NULL,
        kalshi_order_id TEXT,
        error           TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS fills (
        fill_id         TEXT PRIMARY KEY,
        client_order_id TEXT NOT NULL,
        ticker          TEXT NOT NULL,
        side            TEXT NOT NULL,
        price_cents     INTEGER NOT NULL,
        size            INTEGER NOT NULL,
        fee_cents       INTEGER NOT NULL,
        filled_at       TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS positions (
        ticker     TEXT NOT NULL,
        side       TEXT NOT NULL,
        size       INTEGER NOT NULL,
        avg_price  INTEGER NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (ticker, side)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_pnl (
        date              TEXT PRIMARY KEY,
        realized_cents    INTEGER NOT NULL,
        unrealized_cents  INTEGER NOT NULL,
        order_count       INTEGER NOT NULL,
        fill_count        INTEGER NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS shadow_intents (
        intent_id            INTEGER PRIMARY KEY AUTOINCREMENT,
        ts                   TEXT NOT NULL,
        ticker               TEXT NOT NULL,
        side                 TEXT NOT NULL,
        price_cents          INTEGER NOT NULL,
        size                 INTEGER NOT NULL,
        expected_edge_cents  INTEGER NOT NULL,
        reasoning            TEXT NOT NULL
    );
    """,
]


class StateStore:
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
            "INSERT OR IGNORE INTO _schema_version(version) VALUES (1)"
        )
        await self.conn.commit()

    async def schema_version(self) -> int:
        async with self.conn.execute(
            "SELECT max(version) FROM _schema_version"
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    # ----- orders -----
    async def insert_pending_order(self, order: TradeOrder) -> None:
        await self.conn.execute(
            """
            INSERT INTO orders
                (client_order_id, ticker, side, price_cents, size,
                 status, submitted_at)
            VALUES (?, ?, ?, ?, ?, 'pending', datetime('now'))
            """,
            (order.client_order_id, order.ticker, order.side.value,
             order.price_cents, order.size),
        )
        await self.conn.commit()

    async def mark_order_submitted(
        self, client_order_id: str, *, kalshi_order_id: str
    ) -> None:
        await self.conn.execute(
            "UPDATE orders SET status='submitted', kalshi_order_id=? "
            "WHERE client_order_id=?",
            (kalshi_order_id, client_order_id),
        )
        await self.conn.commit()

    async def mark_order_rejected(
        self, client_order_id: str, *, error: str
    ) -> None:
        await self.conn.execute(
            "UPDATE orders SET status='rejected', error=? WHERE client_order_id=?",
            (error, client_order_id),
        )
        await self.conn.commit()

    async def list_orders(self, *, status: str | None = None) -> list[dict[str, Any]]:
        query: str
        params: tuple[Any, ...]
        if status is None:
            query, params = "SELECT * FROM orders", ()
        else:
            query, params = "SELECT * FROM orders WHERE status=?", (status,)
        async with self.conn.execute(query, params) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ----- fills + positions -----
    async def record_fill(self, fill: Fill) -> None:
        await self.conn.execute(
            """
            INSERT OR IGNORE INTO fills
                (fill_id, client_order_id, ticker, side, price_cents, size,
                 fee_cents, filled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (fill.fill_id, fill.client_order_id, fill.ticker, fill.side.value,
             fill.price_cents, fill.size, fill.fee_cents,
             fill.filled_at.isoformat()),
        )
        # Update position (simplistic: long-only, no shorts in v1)
        await self.conn.execute(
            """
            INSERT INTO positions(ticker, side, size, avg_price, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(ticker, side) DO UPDATE SET
                avg_price = (positions.avg_price * positions.size
                             + excluded.avg_price * excluded.size)
                            / (positions.size + excluded.size),
                size = positions.size + excluded.size,
                updated_at = excluded.updated_at
            """,
            (fill.ticker, fill.side.value, fill.size, fill.price_cents),
        )
        await self.conn.commit()

    async def get_position(self, ticker: str, side: Side) -> int:
        async with self.conn.execute(
            "SELECT size FROM positions WHERE ticker=? AND side=?",
            (ticker, side.value),
        ) as cur:
            row = await cur.fetchone()
        return int(row["size"]) if row else 0

    async def total_open_exposure_cents(self) -> int:
        async with self.conn.execute(
            "SELECT COALESCE(SUM(size * avg_price), 0) AS total FROM positions"
        ) as cur:
            row = await cur.fetchone()
        return int(row["total"]) if row else 0

    # ----- pnl -----
    async def today_realized_pnl_cents(self, *, today: date) -> int:
        async with self.conn.execute(
            "SELECT realized_cents FROM daily_pnl WHERE date=?",
            (today.isoformat(),),
        ) as cur:
            row = await cur.fetchone()
        return int(row["realized_cents"]) if row else 0

    # ----- markets -----
    async def upsert_market(
        self,
        *,
        ticker: str,
        series_ticker: str,
        title: str,
        status: str,
        last_seen_ts: str,
    ) -> None:
        """Insert or update a discovered market row."""
        await self.conn.execute(
            """
            INSERT INTO markets (ticker, series_ticker, title, status, last_seen_ts)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                series_ticker = excluded.series_ticker,
                title = excluded.title,
                status = excluded.status,
                last_seen_ts = excluded.last_seen_ts
            """,
            (ticker, series_ticker, title, status, last_seen_ts),
        )
        await self.conn.commit()

    # ----- shadow -----
    async def record_shadow_intent(
        self, *, ts: datetime, ticker: str, side: Side,
        price_cents: int, size: int, expected_edge_cents: int, reasoning: str,
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO shadow_intents
                (ts, ticker, side, price_cents, size, expected_edge_cents, reasoning)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (ts.isoformat(), ticker, side.value, price_cents, size,
             expected_edge_cents, reasoning),
        )
        await self.conn.commit()
