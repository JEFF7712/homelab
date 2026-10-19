from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from typing import Any

from .errors import Code, ProtocolError

MAX_BYTES = 262144
MAX_INTEGER = 2**53 - 1


def _validate(value: Any, depth: int = 0) -> None:
    if depth > 32:
        raise ProtocolError(Code.INVALID_SPEC)
    if value is None or type(value) is bool:
        return
    if type(value) is int and abs(value) <= MAX_INTEGER:
        return
    if isinstance(value, str) and all(32 <= ord(char) <= 126 for char in value):
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProtocolError(Code.INVALID_SPEC)
            _validate(key, depth + 1)
            _validate(item, depth + 1)
        return
    if isinstance(value, (tuple, list)):
        for item in value:
            _validate(item, depth + 1)
        return
    raise ProtocolError(Code.INVALID_SPEC)


def canonical(value: Any) -> bytes:
    _validate(value)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")
    if len(encoded) > MAX_BYTES:
        raise ProtocolError(Code.INVALID_SPEC)
    return encoded


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ProtocolError(Code.INVALID_SPEC)
        result[key] = value
    return result


def loads(raw: str | bytes) -> Any:
    if len(raw) > MAX_BYTES:
        raise ProtocolError(Code.INVALID_SPEC)
    try:
        result = json.loads(raw, object_pairs_hook=_pairs)
        canonical(result)
        return result
    except (ValueError, UnicodeError, RecursionError):
        raise ProtocolError(Code.INVALID_SPEC) from None


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def document(value: Any) -> dict[str, Any]:
    if not is_dataclass(value) or isinstance(value, type):
        raise ProtocolError(Code.INVALID_SPEC)
    return loads(canonical(asdict(value)))
