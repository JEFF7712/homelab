"""WebSocket client + message parsing for Kalshi market data.

The runtime client is a thin asyncio loop that yields parsed messages.
The parser is split out so it can be unit-tested against captured fixtures.

NOTE: the JSON schema below should be verified against current Kalshi WS
docs; if it differs, update parse_message and the fixture files together.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Self

import websockets
import websockets.exceptions
from websockets.asyncio.client import ClientConnection

from predmarkbot.kalshi.auth import KalshiSigner

_log = logging.getLogger(__name__)

# Ping/pong keepalive intervals (seconds).  Without these, a silently-dead TCP
# connection looks alive to the websockets client indefinitely.
_PING_INTERVAL = 20
_PING_TIMEOUT = 20

# Reconnect backoff: start at 2s, double each attempt, cap at 60s.
_RECONNECT_BACKOFF_BASE = 2.0
_RECONNECT_BACKOFF_MAX = 60.0

# Log a "frames received" line every N frames for observability.
_LOG_EVERY_N_FRAMES = 100


@dataclass(frozen=True)
class SnapshotMessage:
    ticker: str
    seq: int
    yes: list[tuple[int, int]]
    no: list[tuple[int, int]]


@dataclass(frozen=True)
class DeltaMessage:
    ticker: str
    seq: int
    side: str  # "yes" | "no"
    price: int
    delta: int


@dataclass(frozen=True)
class UnknownMessage:
    type: str


ParsedMessage = SnapshotMessage | DeltaMessage | UnknownMessage


def _levels(raw: object) -> list[tuple[int, int]]:
    if not isinstance(raw, list):
        raise ValueError(f"expected list of price levels, got {type(raw).__name__}")
    return [(int(p), int(q)) for p, q in raw]


def parse_message(raw: dict[str, Any]) -> ParsedMessage:
    # Kalshi WS frame layout:
    #   {"type": "...", "sid": N, "seq": N, "msg": {...}}
    # seq is on the OUTER frame, not inside msg. yes/no may be absent on
    # snapshots for markets with empty order books — treat as [].
    t = raw.get("type")
    if not isinstance(t, str):
        raise ValueError(f"message missing 'type': {raw!r}")
    if t == "orderbook_snapshot":
        msg = raw.get("msg")
        if not isinstance(msg, dict):
            raise ValueError(f"snapshot missing 'msg': {raw!r}")
        if "seq" not in raw:
            raise ValueError(f"snapshot missing top-level 'seq': {raw!r}")
        try:
            return SnapshotMessage(
                ticker=str(msg["market_ticker"]),
                seq=int(raw["seq"]),
                yes=_levels(msg.get("yes", [])),
                no=_levels(msg.get("no", [])),
            )
        except KeyError as exc:
            raise ValueError(f"snapshot missing field: {exc}") from exc
    if t == "orderbook_delta":
        msg = raw.get("msg")
        if not isinstance(msg, dict):
            raise ValueError(f"delta missing 'msg': {raw!r}")
        if "seq" not in raw:
            raise ValueError(f"delta missing top-level 'seq': {raw!r}")
        try:
            return DeltaMessage(
                ticker=str(msg["market_ticker"]),
                seq=int(raw["seq"]),
                side=str(msg["side"]),
                price=int(msg["price"]),
                delta=int(msg["delta"]),
            )
        except KeyError as exc:
            raise ValueError(f"delta missing field: {exc}") from exc
    return UnknownMessage(type=t)


class KalshiWsClient:
    """Subscribes to orderbook channel for given tickers, yields parsed messages.

    Usage (normal production path):

        async with KalshiWsClient(base_url=url, signer=signer) as ws:
            await ws.subscribe_orderbook(tickers)
            async for msg in ws.messages():
                ...

    Features:
    - Explicit ping/pong keepalive so silently-dead TCP connections surface
      within ~40 seconds (ping_interval + ping_timeout).
    - Automatic reconnect with exponential backoff on any disconnect.
    - Observability: logs on connect, subscribe, every 100 frames, and disconnect.
    - On reconnect, subscribe is re-sent automatically; DataFeed handles the
      resulting SnapshotMessages by rebuilding in-memory books.
    """

    def __init__(self, *, base_url: str, signer: KalshiSigner | None = None) -> None:
        self._base_url = base_url
        self._signer = signer
        self._tickers: list[str] = []
        # _ws is kept only for the no-tickers legacy path (direct __aenter__ use).
        self._ws: ClientConnection | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    def _auth_headers(self) -> dict[str, str] | None:
        if self._signer is not None:
            return self._signer.sign(method="GET", path="/trade-api/ws/v2")
        return None

    async def subscribe_orderbook(self, tickers: list[str]) -> None:
        """Register tickers for subscription.

        In the reconnect-loop path (messages() manages connections), this just
        stores the ticker list; the subscribe command is sent by messages() on
        each connect/reconnect.

        In the legacy no-reconnect path (caller manages connection via _ws),
        this also sends the subscribe command immediately.
        """
        self._tickers = tickers
        if self._ws is not None:
            # Legacy path: connection already open, send immediately.
            await self._ws.send(
                json.dumps(
                    {
                        "id": 1,
                        "cmd": "subscribe",
                        "params": {
                            "channels": ["orderbook_delta"],
                            "market_tickers": tickers,
                        },
                    }
                )
            )
            _log.info("ws subscribed to %d tickers", len(tickers))

    async def messages(self) -> AsyncIterator[ParsedMessage]:
        """Yield parsed messages forever, reconnecting on any disconnect.

        When tickers have been registered, this method manages its own connection
        lifecycle (open → subscribe → drain → reconnect on failure).

        When no tickers are registered, falls back to draining self._ws directly
        (legacy path for callers that manage the connection externally).
        """
        if not self._tickers:
            # Legacy path: drain the externally-managed connection.
            assert self._ws is not None, (
                "call subscribe_orderbook() before messages(), "
                "or open a connection via the internal _ws attribute"
            )
            async for raw in self._ws:
                data = json.loads(raw)
                parsed: ParsedMessage
                try:
                    parsed = parse_message(data)
                except ValueError:
                    parsed = UnknownMessage(type="malformed")
                yield parsed
            return

        # Normal production path: reconnect loop.
        backoff = _RECONNECT_BACKOFF_BASE
        frame_count = 0
        while True:
            try:
                async with websockets.connect(
                    self._base_url,
                    additional_headers=self._auth_headers(),
                    ping_interval=_PING_INTERVAL,
                    ping_timeout=_PING_TIMEOUT,
                ) as ws:
                    _log.info("kalshi ws connected to %s", self._base_url)
                    await ws.send(
                        json.dumps(
                            {
                                "id": 1,
                                "cmd": "subscribe",
                                "params": {
                                    "channels": ["orderbook_delta"],
                                    "market_tickers": self._tickers,
                                },
                            }
                        )
                    )
                    _log.info("ws subscribed to %d tickers", len(self._tickers))
                    backoff = _RECONNECT_BACKOFF_BASE  # reset on successful connect

                    async for raw in ws:
                        data = json.loads(raw)
                        try:
                            parsed = parse_message(data)
                        except ValueError:
                            parsed = UnknownMessage(type="malformed")

                        # Log unexpected server message types (e.g. subscribe ACK)
                        if (
                            isinstance(parsed, UnknownMessage)
                            and parsed.type not in ("malformed",)
                        ):
                            _log.info("ws server message type=%s", parsed.type)

                        frame_count += 1
                        if frame_count % _LOG_EVERY_N_FRAMES == 0:
                            _log.info("ws received %d frames so far", frame_count)

                        yield parsed

            except asyncio.CancelledError:
                _log.info("ws messages() cancelled; stopping")
                return
            except websockets.exceptions.ConnectionClosed as exc:
                _log.warning("ws disconnected: %s; reconnecting in %.0fs", exc, backoff)
            except Exception as exc:  # noqa: BLE001
                _log.warning("ws error: %s; reconnecting in %.0fs", exc, backoff)

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _RECONNECT_BACKOFF_MAX)
