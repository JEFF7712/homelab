from __future__ import annotations

import base64
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from predmarkbot.kalshi.auth import KalshiSigner, load_private_key, sign_message

FIXTURE_KEY = Path(__file__).parent.parent / "fixtures" / "test_key.pem"


def test_load_private_key_from_pem() -> None:
    key = load_private_key(FIXTURE_KEY)
    assert key.key_size == 2048


def test_sign_message_is_verifiable_by_public_key() -> None:
    key = load_private_key(FIXTURE_KEY)
    message = b"1717000000000GET/trade-api/v2/portfolio/balance"
    sig_b64 = sign_message(key, message)
    sig = base64.b64decode(sig_b64)
    # Verify with the public key — must not raise
    public_key = key.public_key()
    public_key.verify(
        sig,
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )


def test_signer_produces_three_headers() -> None:
    signer = KalshiSigner(key_id="my-key-id", private_key=load_private_key(FIXTURE_KEY))
    headers = signer.sign(method="GET", path="/trade-api/v2/portfolio/balance")
    assert headers["KALSHI-ACCESS-KEY"] == "my-key-id"
    assert headers["KALSHI-ACCESS-TIMESTAMP"].isdigit()
    assert isinstance(headers["KALSHI-ACCESS-SIGNATURE"], str)
    assert len(headers["KALSHI-ACCESS-SIGNATURE"]) > 100  # base64 of 256-byte sig


def test_signer_timestamp_is_milliseconds_now(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    signer = KalshiSigner(
        key_id="k",
        private_key=load_private_key(FIXTURE_KEY),
        now_ms=lambda: 1_717_000_000_000,
    )
    headers = signer.sign(method="POST", path="/trade-api/v2/portfolio/orders")
    assert headers["KALSHI-ACCESS-TIMESTAMP"] == "1717000000000"
