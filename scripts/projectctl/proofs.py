from __future__ import annotations

import base64
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .codec import canonical
from .errors import Code, ProtocolError
from .models import DIGEST, REPOSITORY, SHA, number, obj, string, timestamp

PROBE_DOMAIN = b"projectctl.protection-probe.v1\n"
PROTECTION_FIELDS = (
    "branch_name",
    "enable_push",
    "enable_push_whitelist",
    "enable_status_check",
    "status_check_contexts",
    "apply_to_admins",
)


def probe_payload(value: Any) -> dict[str, Any]:
    row = obj(
        value,
        "schema_version request_id repository branch source_sha probe_sha probe_pr probe_pipeline protection_digest merge_rejected observed_at expires_at",
    )
    string(row["request_id"])
    string(row["branch"], r"[a-zA-Z0-9][a-zA-Z0-9_./-]{0,127}")
    string(row["repository"], REPOSITORY)
    for name in ("source_sha", "probe_sha"):
        string(row[name], SHA)
    for name in ("probe_pr", "probe_pipeline"):
        number(row[name])
    string(row["protection_digest"], DIGEST)
    if row["merge_rejected"] is not True or timestamp(row["expires_at"]) <= timestamp(
        row["observed_at"]
    ):
        raise ProtocolError(Code.INVALID_SPEC)
    return row


def verify_probe(
    value: Any, keys: Mapping[str, Ed25519PublicKey], now: datetime
) -> dict[str, Any]:
    row = obj(value, "schema_version payload key_id signature")
    payload = probe_payload(row["payload"])
    key = keys.get(string(row["key_id"]))
    if key is None:
        raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
    try:
        key.verify(
            base64.b64decode(row["signature"], validate=True),
            PROBE_DOMAIN + canonical(payload),
        )
    except (ValueError, TypeError, InvalidSignature):
        raise ProtocolError(Code.AUTHORIZATION_REQUIRED) from None
    if not timestamp(payload["observed_at"]) <= now < timestamp(payload["expires_at"]):
        raise ProtocolError(Code.VERIFICATION_PENDING)
    return payload
