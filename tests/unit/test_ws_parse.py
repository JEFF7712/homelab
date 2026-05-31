from __future__ import annotations

import json
from pathlib import Path

import pytest

from predmarkbot.kalshi.ws import (
    DeltaMessage,
    SnapshotMessage,
    UnknownMessage,
    parse_message,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_parse_snapshot() -> None:
    raw = json.loads((FIXTURES / "ws_snapshot.json").read_text())
    msg = parse_message(raw)
    assert isinstance(msg, SnapshotMessage)
    assert msg.ticker == "KXHIGHNY-26MAY30-T75"
    assert msg.seq == 100
    assert msg.yes == [(50, 10), (49, 5)]
    assert msg.no == [(48, 4), (47, 8)]


def test_parse_delta() -> None:
    raw = json.loads((FIXTURES / "ws_delta.json").read_text())
    msg = parse_message(raw)
    assert isinstance(msg, DeltaMessage)
    assert msg.ticker == "KXHIGHNY-26MAY30-T75"
    assert msg.seq == 101
    assert msg.side == "yes"
    assert msg.price == 50
    assert msg.delta == -3


def test_parse_unknown_returns_unknown() -> None:
    msg = parse_message({"type": "ping"})
    assert isinstance(msg, UnknownMessage)


def test_parse_malformed_raises() -> None:
    with pytest.raises(ValueError):
        parse_message({"type": "orderbook_snapshot", "msg": {}})  # missing fields
