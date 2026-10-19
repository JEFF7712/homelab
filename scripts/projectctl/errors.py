from __future__ import annotations

from enum import StrEnum
from typing import Any


class Code(StrEnum):
    INTERNAL_ERROR = "INTERNAL_ERROR"
    INVALID_SPEC = "INVALID_SPEC"
    AUTHORIZATION_REQUIRED = "AUTHORIZATION_REQUIRED"
    AUTHORIZATION_EXPIRED = "AUTHORIZATION_EXPIRED"
    AUTHORIZATION_REVOKED = "AUTHORIZATION_REVOKED"
    POLICY_CHANGED = "POLICY_CHANGED"
    PLAN_STALE = "PLAN_STALE"
    RESOURCE_CONFLICT = "RESOURCE_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    OUTCOME_UNCERTAIN = "OUTCOME_UNCERTAIN"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    SOURCE_MOVED = "SOURCE_MOVED"
    CI_FAILED = "CI_FAILED"
    VERIFICATION_PENDING = "VERIFICATION_PENDING"


class ProtocolError(Exception):
    def __init__(self, code: Code, operation_id: str | None = None):
        self.code = code
        self.operation_id = operation_id
        super().__init__(code.value)

    @property
    def exit_code(self) -> int:
        if self.code == Code.INTERNAL_ERROR:
            return 1
        if self.code == Code.INVALID_SPEC:
            return 2
        if (
            self.code.value.startswith("AUTHORIZATION")
            or self.code == Code.POLICY_CHANGED
        ):
            return 3
        if self.code in {Code.DEPENDENCY_UNAVAILABLE, Code.VERIFICATION_PENDING}:
            return 5
        return 4

    def envelope(self) -> dict[str, Any]:
        retryable = self.code in {
            Code.DEPENDENCY_UNAVAILABLE,
            Code.VERIFICATION_PENDING,
        }
        return {
            "schema_version": 1,
            "ok": False,
            "data": None,
            "error": {
                "code": self.code.value,
                "operation_id": self.operation_id,
                "reason": self.code.value.lower().replace("_", " "),
                "retryable": retryable,
                "recovery": {
                    "action": "inspect" if retryable else "review",
                    "requires_authorization": not retryable,
                },
            },
        }


def success(data: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": 1, "ok": True, "data": data, "error": None}
