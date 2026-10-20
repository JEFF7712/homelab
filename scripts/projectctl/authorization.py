from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .codec import canonical, digest, document
from .errors import Code, ProtocolError
from .models import (
    DIGEST,
    SHA,
    Plan,
    Platform,
    Project,
    number,
    obj,
    string,
    strings,
    timestamp,
)

SIGNATURE_PREFIX = b"projectctl.approval.v1\n"


@dataclass(frozen=True)
class Policy:
    platform: Platform
    principal: str
    project_key: str
    repository: str
    visibility: str
    implementation_digest: str
    max_discovery_age_seconds: int = 300
    max_grant_seconds: int = 3600
    max_evidence_age_seconds: int = 300
    routine_onboarding: bool = False

    @property
    def policy_digest(self) -> str:
        string(self.implementation_digest, DIGEST)
        for value in (
            self.max_discovery_age_seconds,
            self.max_grant_seconds,
            self.max_evidence_age_seconds,
        ):
            number(value)
        return digest(document(self))

    def check(self, plan: Plan, principal: str) -> None:
        if (
            plan.principal != principal
            or principal != self.principal
            or plan.platform != self.platform
            or plan.project.project_key != self.project_key
            or plan.project.repository != self.repository
            or plan.project.visibility != self.visibility
        ):
            raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
        if plan.policy_digest != self.policy_digest:
            raise ProtocolError(Code.POLICY_CHANGED)


@dataclass(frozen=True)
class Approval:
    schema_version: int
    grant_id: str
    request_id: str
    plan_digest: str
    principal: str
    project_key: str
    platform: Platform
    policy_digest: str
    allowed_operations: tuple[str, ...]
    not_before: str
    expires_at: str
    catalog_revision: str
    catalog_entry_digest: str

    @classmethod
    def parse(cls, value: Any) -> Approval:
        row = obj(
            value,
            "schema_version grant_id request_id plan_digest principal project_key platform policy_digest allowed_operations not_before expires_at catalog_revision catalog_entry_digest",
        )
        start, end = timestamp(row["not_before"]), timestamp(row["expires_at"])
        if end <= start:
            raise ProtocolError(Code.INVALID_SPEC)
        return cls(
            1,
            string(row["grant_id"]),
            string(row["request_id"]),
            string(row["plan_digest"], DIGEST),
            string(row["principal"]),
            string(row["project_key"]),
            Platform.parse(row["platform"]),
            string(row["policy_digest"], DIGEST),
            strings(row["allowed_operations"]),
            row["not_before"],
            row["expires_at"],
            string(row["catalog_revision"], SHA),
            string(row["catalog_entry_digest"], DIGEST),
        )


@dataclass(frozen=True)
class SignedApproval:
    schema_version: int
    payload: Approval
    key_id: str
    signature: str

    @classmethod
    def parse(cls, value: Any) -> SignedApproval:
        row = obj(value, "schema_version payload key_id signature")
        signature = string(row["signature"], r"[A-Za-z0-9+/]{86}==")
        try:
            decoded = base64.b64decode(signature, validate=True)
        except binascii.Error:
            raise ProtocolError(Code.INVALID_SPEC) from None
        if len(decoded) != 64 or base64.b64encode(decoded).decode("ascii") != signature:
            raise ProtocolError(Code.INVALID_SPEC)
        return cls(1, Approval.parse(row["payload"]), string(row["key_id"]), signature)


@dataclass(frozen=True)
class AuthoritySnapshot:
    policy: Policy
    protected_revision: str
    approval: SignedApproval | None
    routine: bool = False


class Authority(Protocol):
    def admit(self, principal: str, project: Project) -> Policy: ...

    def current(self, request_id: str) -> AuthoritySnapshot: ...


@dataclass(frozen=True)
class GrantEvidence:
    grant_id: str
    approval_digest: str
    protected_revision: str
    policy_digest: str
    worker_version: str = "projectctl-v1"


def verify_signature(
    signed: SignedApproval, trusted_keys: Mapping[str, Ed25519PublicKey]
) -> Approval:
    signed = SignedApproval.parse(document(signed))
    key = trusted_keys.get(signed.key_id)
    if key is None:
        raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
    try:
        key.verify(
            base64.b64decode(signed.signature),
            SIGNATURE_PREFIX + canonical(document(signed.payload)),
        )
    except InvalidSignature:
        raise ProtocolError(Code.AUTHORIZATION_REQUIRED) from None
    return signed.payload


def verify(
    plan: Plan,
    request_id: str,
    operation_id: str,
    principal: str,
    snapshot: AuthoritySnapshot,
    trusted_keys: Mapping[str, Ed25519PublicKey],
    now: datetime,
) -> GrantEvidence:
    plan = plan.validated()
    snapshot.policy.check(plan, principal)
    string(snapshot.protected_revision, SHA)
    if snapshot.routine and snapshot.policy.routine_onboarding:
        if operation_id not in {
            "clone",
            "enrollment",
            "pull",
            "protection",
            "ci",
            "readiness",
        }:
            raise ProtocolError(Code.AUTHORIZATION_REQUIRED, operation_id)
        return GrantEvidence(
            "routine-onboarding",
            digest({"request_id": request_id, "plan": document(plan)}),
            snapshot.protected_revision,
            snapshot.policy.policy_digest,
        )
    if snapshot.approval is None:
        raise ProtocolError(Code.AUTHORIZATION_REVOKED, operation_id)
    approval = verify_signature(snapshot.approval, trusted_keys)
    if (
        approval.request_id != request_id
        or approval.plan_digest != digest(document(plan))
        or approval.principal != principal
        or approval.project_key != plan.project.project_key
        or approval.platform != plan.platform
        or approval.policy_digest != plan.policy_digest
        or operation_id not in approval.allowed_operations
        or not set(approval.allowed_operations)
        <= {operation.operation_id for operation in plan.operations}
    ):
        raise ProtocolError(Code.AUTHORIZATION_REQUIRED, operation_id)
    start, end = timestamp(approval.not_before), timestamp(approval.expires_at)
    if (end - start).total_seconds() > snapshot.policy.max_grant_seconds:
        raise ProtocolError(Code.AUTHORIZATION_REQUIRED, operation_id)
    if now.tzinfo is None or now < start:
        raise ProtocolError(Code.AUTHORIZATION_REQUIRED, operation_id)
    if now >= end:
        raise ProtocolError(Code.AUTHORIZATION_EXPIRED, operation_id)
    return GrantEvidence(
        approval.grant_id,
        digest(document(approval)),
        snapshot.protected_revision,
        snapshot.policy.policy_digest,
    )
