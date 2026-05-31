"""WebSocket client + message parsing for Kalshi market data.

The runtime client is a thin asyncio loop that yields parsed messages.
The parser is split out so it can be unit-tested against captured fixtures.

NOTE: the JSON schema below should be verified against current Kalshi WS
docs; if it differs, update parse_message and the fixture files together.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Self

import websockets
from websockets.asyncio.client import ClientConnection

from predmarkbot.kalshi.auth import KalshiSigner


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
    t = raw.get("type")
    if not isinstance(t, str):
        raise ValueError(f"message missing 'type': {raw!r}")
    if t == "orderbook_snapshot":
        msg = raw.get("msg")
        if not isinstance(msg, dict):
            raise ValueError(f"snapshot missing 'msg': {raw!r}")
        try:
            return SnapshotMessage(
                ticker=str(msg["market_ticker"]),
                seq=int(msg["seq"]),
                yes=_levels(msg["yes"]),
                no=_levels(msg["no"]),
            )
        except KeyError as exc:
            raise ValueError(f"snapshot missing field: {exc}") from exc
    if t == "orderbook_delta":
        msg = raw.get("msg")
        if not isinstance(msg, dict):
            raise ValueError(f"delta missing 'msg': {raw!r}")
        try:
            return DeltaMessage(
                ticker=str(msg["market_ticker"]),
                seq=int(msg["seq"]),
                side=str(msg["side"]),
                price=int(msg["price"]),
                delta=int(msg["delta"]),
            )
        except KeyError as exc:
            raise ValueError(f"delta missing field: {exc}") from exc
    return UnknownMessage(type=t)


class KalshiWsClient:
    """Subscribes to orderbook channel for given tickers, yields parsed messages."""

    def __init__(self, *, base_url: str, signer: KalshiSigner | None = None) -> None:
        self._base_url = base_url
        self._signer = signer
        self._ws: ClientConnection | None = None

    async def __aenter__(self) -> Self:
        additional_headers: dict[str, str] | None = None
        if self._signer is not None:
            additional_headers = self._signer.sign(method="GET", path="/trade-api/ws/v2")
        self._ws = await websockets.connect(
            self._base_url,
            additional_headers=additional_headers,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._ws is not None:
            await self._ws.close()

    async def subscribe_orderbook(self, tickers: list[str]) -> None:
        assert self._ws is not None
        await self._ws.send(
            json.dumps(
                {
                    "id": 1,
                    "cmd": "subscribe",
                    "params": {"channels": ["orderbook_delta"], "market_tickers": tickers},
                }
            )
        )

    async def messages(self) -> AsyncIterator[ParsedMessage]:
        assert self._ws is not None
        async for raw in self._ws:
            data = json.loads(raw)
            parsed: ParsedMessage
            try:
                parsed = parse_message(data)
            except ValueError:
                parsed = UnknownMessage(type="malformed")
            yield parsed
