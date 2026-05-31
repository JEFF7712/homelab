"""RSA-PSS signing for Kalshi authenticated REST requests.

Signature format (verify against current Kalshi docs while implementing):
    message = timestamp_ms + method + path
    signature = base64(RSA-PSS(SHA256, salt_len=DIGEST_LENGTH)(message))
Required headers:
    KALSHI-ACCESS-KEY:       <key_id>
    KALSHI-ACCESS-TIMESTAMP: <unix_ms_string>
    KALSHI-ACCESS-SIGNATURE: <base64_signature>
"""
from __future__ import annotations

import base64
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def load_private_key(path: Path) -> rsa.RSAPrivateKey:
    pem = path.read_bytes()
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError(f"expected RSA private key, got {type(key).__name__}")
    return key


def sign_message(key: rsa.RSAPrivateKey, message: bytes) -> str:
    sig = key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(sig).decode("ascii")


def _default_now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class KalshiSigner:
    key_id: str
    private_key: rsa.RSAPrivateKey
    now_ms: Callable[[], int] = field(default_factory=lambda: _default_now_ms)

    def sign(self, *, method: str, path: str) -> dict[str, str]:
        ts = str(self.now_ms())
        message = f"{ts}{method.upper()}{path}".encode()
        signature = sign_message(self.private_key, message)
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": signature,
        }
