from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .authorization import Authority, Policy, verify
from .codec import digest, document
from .errors import Code, ProtocolError
from .journal import TERMINAL, Journal
from .models import (
    DIGEST,
    MUTATIONS,
    REPOSITORY,
    SHA,
    Operation,
    Plan,
    number,
    obj,
    stamp,
    string,
    timestamp,
)
from .planner import plan as build_plan

CONTEXT = "ci/woodpecker/development-validation"
WORKFLOW = "development-validation"
EVIDENCE_STRINGS = {
    "observed_at": r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
    "repository": REPOSITORY,
    "visibility": "private|public",
    "branch": r"[a-zA-Z0-9][a-zA-Z0-9_./-]{0,127}",
    "source_sha": SHA,
    "policy_digest": DIGEST,
    "enrollment_revision": SHA,
    "deployed_package_digest": DIGEST,
    "workflow": WORKFLOW,
    "context": CONTEXT,
    "event": "push|manual",
    "ci_status": "pending|success|failure|error|killed",
}
EVIDENCE_NUMBERS = {"repository_id", "enrollment_id", "pipeline_id"}
EVIDENCE_BOOLEANS = {
    "clone_read_only",
    "pull_read_only",
    "webhook_delivered",
    "branch_protection",
    "protection_probe",
}


@dataclass(frozen=True)
class Observation:
    state: str
    evidence: dict[str, Any]
    outputs: dict[str, Any] = field(default_factory=dict)
    owned: bool = False

    def validated(
        self, operation: Operation, now: datetime, policy: Policy
    ) -> Observation:
        row = obj(document(self), "state evidence outputs owned")
        string(row["state"], "absent|pending|conflict|verified")
        if (
            type(row["owned"]) is not bool
            or not isinstance(row["evidence"], dict)
            or not isinstance(row["outputs"], dict)
        ):
            raise ProtocolError(Code.INVALID_SPEC)
        evidence = row["evidence"]
        if (
            not set(evidence)
            <= set(EVIDENCE_STRINGS) | EVIDENCE_NUMBERS | EVIDENCE_BOOLEANS
            or "observed_at" not in evidence
        ):
            raise ProtocolError(Code.INVALID_SPEC)
        for key, value in evidence.items():
            if key in EVIDENCE_STRINGS:
                string(value, EVIDENCE_STRINGS[key])
            elif key in EVIDENCE_NUMBERS:
                number(value)
            elif type(value) is not bool:
                raise ProtocolError(Code.INVALID_SPEC)
        age = (now - timestamp(evidence["observed_at"])).total_seconds()
        if not 0 <= age <= policy.max_evidence_age_seconds:
            raise ProtocolError(Code.VERIFICATION_PENDING)
        outputs = row["outputs"]
        if row["state"] == "verified":
            if set(outputs) != {port.name for port in operation.outputs}:
                raise ProtocolError(Code.INVALID_SPEC)
            for port in operation.outputs:
                if port.value_type == "id":
                    number(outputs[port.name])
                else:
                    string(
                        outputs[port.name],
                        DIGEST if port.value_type == "digest" else SHA,
                    )
        elif outputs:
            raise ProtocolError(Code.INVALID_SPEC)
        return Observation(row["state"], evidence, outputs, row["owned"])


@dataclass(frozen=True)
class OperationContext:
    request_id: str
    plan: Plan
    operation: Operation
    inputs: dict[str, Any]
    attempt: int


class Adapter(Protocol):
    def inspect(self, context: OperationContext) -> Observation: ...

    def mutate(self, context: OperationContext) -> None: ...


class Worker:
    def __init__(
        self,
        journal: Journal,
        authority: Authority,
        trusted_keys: Mapping[str, Ed25519PublicKey],
        adapter: Adapter,
        clock: Callable[[], datetime],
    ):
        self.journal = journal
        self.authority = authority
        self.trusted_keys = dict(trusted_keys)
        self.adapter = adapter
        self.clock = clock

    def submit(self, plan: Plan, principal: str, submission_key: str) -> str:
        plan = plan.validated()
        policy = self.authority.admit(principal, plan.project)
        policy.check(plan, principal)
        existing = self.journal.lookup(
            principal, submission_key, digest(document(plan))
        )
        if existing is not None:
            return existing
        build_plan(plan.project, plan.discovery, policy, self.clock())
        return self.journal.submit(plan, principal, submission_key)

    def _context(
        self, status: dict[str, Any], plan: Plan, operation: Operation
    ) -> OperationContext:
        records = {row["id"]: row for row in status["operations"]}
        inputs = {}
        for reference in operation.inputs:
            producer = records[reference.operation_id]
            if (
                producer["state"] != "verified"
                or reference.output not in producer["outputs"]
            ):
                raise ProtocolError(Code.VERIFICATION_PENDING, operation.operation_id)
            inputs[reference.name] = producer["outputs"][reference.output]
        return OperationContext(
            status["request_id"],
            plan,
            operation,
            inputs,
            records[operation.operation_id]["attempt"],
        )

    def _inspect(self, context: OperationContext, policy: Policy) -> Observation:
        result = self.adapter.inspect(context).validated(
            context.operation, self.clock(), policy
        )
        if result.state == "conflict":
            raise ProtocolError(Code.RESOURCE_CONFLICT, context.operation.operation_id)
        if result.state != "verified":
            return result
        evidence, operation, project = (
            result.evidence,
            context.operation.operation_id,
            context.plan.project,
        )
        if (
            evidence.get("repository") != project.repository
            or evidence.get("branch") != project.default_branch
        ):
            raise ProtocolError(Code.PLAN_STALE, operation)
        expected_repo = context.inputs.get("repository_id")
        if expected_repo is not None and evidence.get("repository_id") != expected_repo:
            raise ProtocolError(Code.RESOURCE_CONFLICT, operation)
        if (
            "enrollment_id" in context.inputs
            and evidence.get("enrollment_id") != context.inputs["enrollment_id"]
        ):
            raise ProtocolError(Code.RESOURCE_CONFLICT, operation)
        if operation == "repository":
            expected = context.plan.discovery.repository_id
            if (
                evidence.get("repository_id") != result.outputs["repository_id"]
                or (
                    expected is not None and expected != result.outputs["repository_id"]
                )
                or evidence.get("visibility") != project.visibility
            ):
                raise ProtocolError(Code.RESOURCE_CONFLICT, operation)
            if expected is None and (not context.attempt or not result.owned):
                raise ProtocolError(Code.RESOURCE_CONFLICT, operation)
        if operation == "clone" and evidence.get("clone_read_only") is not True:
            raise ProtocolError(Code.RESOURCE_CONFLICT, operation)
        if (
            operation == "enrollment"
            and evidence.get("enrollment_id") != result.outputs["enrollment_id"]
        ):
            raise ProtocolError(Code.RESOURCE_CONFLICT, operation)
        if operation == "pull" and evidence.get("pull_read_only") is not True:
            raise ProtocolError(Code.RESOURCE_CONFLICT, operation)
        if operation == "protection" and evidence.get("branch_protection") is not True:
            raise ProtocolError(Code.RESOURCE_CONFLICT, operation)
        if (
            operation in {"source", "ci", "readiness"}
            and evidence.get("source_sha") != project.source_sha
        ):
            raise ProtocolError(Code.SOURCE_MOVED, operation)
        if operation == "source" and result.outputs["source_sha"] != project.source_sha:
            raise ProtocolError(Code.SOURCE_MOVED, operation)
        if (
            operation in {"policy", "ci", "readiness"}
            and evidence.get("policy_digest") != context.plan.policy_digest
        ):
            raise ProtocolError(Code.POLICY_CHANGED, operation)
        if operation == "policy" and (
            result.outputs["policy_digest"] != context.plan.policy_digest
            or "enrollment_revision" not in evidence
            or "deployed_package_digest" not in evidence
        ):
            raise ProtocolError(Code.VERIFICATION_PENDING, operation)
        if operation in {"ci", "readiness"}:
            pipeline_id = (
                result.outputs["pipeline_id"]
                if operation == "ci"
                else context.inputs["pipeline_id"]
            )
            if (
                evidence.get("pipeline_id") != pipeline_id
                or evidence.get("workflow") != WORKFLOW
                or evidence.get("context") != CONTEXT
                or evidence.get("event") not in {"push", "manual"}
            ):
                raise ProtocolError(Code.VERIFICATION_PENDING, operation)
            if evidence.get("ci_status") in {"failure", "error", "killed"}:
                raise ProtocolError(Code.CI_FAILED, operation)
        if operation == "readiness" and (
            evidence.get("ci_status") != "success"
            or any(
                evidence.get(key) is not True
                for key in (
                    ("webhook_delivered", "branch_protection")
                    if policy.routine_onboarding
                    else ("webhook_delivered", "branch_protection", "protection_probe")
                )
            )
        ):
            raise ProtocolError(Code.VERIFICATION_PENDING, operation)
        return result

    def _dependencies(
        self, status: dict[str, Any], plan: Plan, operation: Operation, policy: Policy
    ) -> None:
        ancestors = set(operation.depends_on)
        for predecessor in reversed(plan.operations):
            if predecessor.operation_id in ancestors:
                ancestors.update(predecessor.depends_on)
        rows = {row["id"]: row for row in status["operations"]}
        for predecessor in plan.operations:
            if predecessor.operation_id not in ancestors:
                continue
            row = rows[predecessor.operation_id]
            if row["state"] != "verified":
                raise ProtocolError(Code.VERIFICATION_PENDING, operation.operation_id)
            observed = self._inspect(self._context(status, plan, predecessor), policy)
            if observed.state != "verified" or observed.outputs != row["outputs"]:
                raise ProtocolError(Code.PLAN_STALE, operation.operation_id)

    def _record(self, context: OperationContext, observation: Observation) -> None:
        self.journal.record(
            context.request_id,
            context.operation.operation_id,
            "verified",
            stamp(self.clock()),
            observation.outputs,
            observation.evidence,
        )

    def run(self, request_id: str, principal: str) -> dict[str, Any]:
        with self.journal.execution():
            status = self.journal.status(request_id, principal)
            if status["state"] in TERMINAL:
                if status["state"] == "cancelled":
                    self.journal.recover(request_id)
                    try:
                        plan = Plan.parse(status["plan"])
                        policy = self.authority.current(request_id).policy
                        policy.check(plan, principal)
                        status = self.journal.status(request_id, principal)
                        for operation in plan.operations:
                            row = next(
                                item
                                for item in status["operations"]
                                if item["id"] == operation.operation_id
                            )
                            if row["state"] == "uncertain":
                                context = self._context(status, plan, operation)
                                result = self._inspect(context, policy)
                                if result.state == "verified":
                                    self._record(context, result)
                                    status = self.journal.status(request_id, principal)
                    except Exception:
                        pass
                return self.journal.status(request_id, principal)
            plan = Plan.parse(status["plan"])
            self.journal.recover(request_id)
            self.journal.state(request_id, "queued")
            self.journal.state(request_id, "running")
            current_operation: Operation | None = None
            try:
                policy = self.authority.current(request_id).policy
                policy.check(plan, principal)
                for operation in plan.operations:
                    current_operation = operation
                    status = self.journal.status(request_id, principal)
                    if status["state"] == "cancelled":
                        break
                    row = next(
                        item
                        for item in status["operations"]
                        if item["id"] == operation.operation_id
                    )
                    if row["state"] == "verified":
                        continue
                    context = self._context(status, plan, operation)
                    result = self._inspect(context, policy)
                    if result.state == "verified":
                        self._dependencies(status, plan, operation, policy)
                        self._record(context, result)
                        continue
                    if (
                        result.state == "pending"
                        or operation.operation_id not in MUTATIONS
                    ):
                        raise ProtocolError(
                            Code.VERIFICATION_PENDING, operation.operation_id
                        )
                    self._dependencies(status, plan, operation, policy)
                    result = self._inspect(context, policy)
                    if result.state == "verified":
                        self._record(context, result)
                        continue
                    if result.state != "absent":
                        raise ProtocolError(
                            Code.VERIFICATION_PENDING, operation.operation_id
                        )
                    if row["next_attempt_at"] and self.clock() < timestamp(
                        row["next_attempt_at"]
                    ):
                        self.journal.state(
                            request_id, "waiting", Code.VERIFICATION_PENDING
                        )
                        return self.journal.status(request_id, principal)
                    snapshot = self.authority.current(request_id)
                    grant = verify(
                        plan,
                        request_id,
                        operation.operation_id,
                        principal,
                        snapshot,
                        self.trusted_keys,
                        self.clock(),
                    )
                    if (
                        self.journal.status(request_id, principal)["state"]
                        == "cancelled"
                    ):
                        break
                    attempt = self.journal.begin(
                        request_id,
                        operation.operation_id,
                        stamp(self.clock()),
                        document(grant),
                    )
                    context = replace(context, attempt=attempt)
                    try:
                        if (
                            self.journal.status(request_id, principal)["state"]
                            == "cancelled"
                        ):
                            break
                        self.adapter.mutate(context)
                        result = self._inspect(context, snapshot.policy)
                        if result.state != "verified":
                            raise ProtocolError(
                                Code.OUTCOME_UNCERTAIN, operation.operation_id
                            )
                        self._record(context, result)
                    except Exception:
                        self.journal.record(
                            request_id,
                            operation.operation_id,
                            "uncertain",
                            stamp(self.clock()),
                            {},
                            {},
                            Code.OUTCOME_UNCERTAIN,
                            stamp(
                                self.clock()
                                + timedelta(seconds=min(60, 2 ** min(attempt, 6)))
                            ),
                        )
                        raise ProtocolError(
                            Code.OUTCOME_UNCERTAIN, operation.operation_id
                        ) from None
                else:
                    policy = self.authority.current(request_id).policy
                    policy.check(plan, principal)
                    status = self.journal.status(request_id, principal)
                    readiness = plan.operations[-1]
                    self._dependencies(status, plan, readiness, policy)
                    result = self._inspect(
                        self._context(status, plan, readiness), policy
                    )
                    if result.state != "verified":
                        raise ProtocolError(
                            Code.VERIFICATION_PENDING, readiness.operation_id
                        )
                    self.journal.state(request_id, "succeeded")
            except ProtocolError as error:
                state = (
                    "waiting"
                    if error.code
                    in {Code.VERIFICATION_PENDING, Code.DEPENDENCY_UNAVAILABLE}
                    else "failed"
                    if error.code == Code.CI_FAILED
                    else "blocked"
                )
                if (
                    current_operation is not None
                    and error.code != Code.OUTCOME_UNCERTAIN
                ):
                    self.journal.record(
                        request_id,
                        current_operation.operation_id,
                        "waiting"
                        if state == "waiting"
                        else "failed"
                        if state == "failed"
                        else "blocked",
                        stamp(self.clock()),
                        {},
                        {},
                        error.code,
                        stamp(self.clock() + timedelta(seconds=2))
                        if state == "waiting"
                        else None,
                    )
                self.journal.state(request_id, state, error.code)
            except Exception:
                self.journal.state(request_id, "waiting", Code.DEPENDENCY_UNAVAILABLE)
            return self.journal.status(request_id, principal)
