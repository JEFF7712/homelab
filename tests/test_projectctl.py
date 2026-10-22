from __future__ import annotations

import base64
import copy
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.projectctl.authorization import (
    SIGNATURE_PREFIX,
    Approval,
    AuthoritySnapshot,
    Policy,
    SignedApproval,
    verify,
)
from scripts.projectctl.codec import canonical, digest, document, loads
from scripts.projectctl.errors import Code, ProtocolError, success
from scripts.projectctl.journal import Journal
from scripts.projectctl.models import (
    CAPABILITIES,
    Discovery,
    Plan,
    Platform,
    Project,
    stamp,
)
from scripts.projectctl.planner import plan
from scripts.projectctl.worker import (
    CONTEXT,
    UNCERTAIN_RETRIES,
    WORKFLOW,
    Observation,
    OperationContext,
    Worker,
)

ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 15, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


class FakeAuthority:
    def __init__(self, policy: Policy):
        self.policy = policy
        self.approval: SignedApproval | None = None
        self.unavailable = False
        self.calls = 0
        self.revoke_on_call: int | None = None

    def admit(self, principal: str, project: Project) -> Policy:
        if (
            principal != self.policy.principal
            or project.project_key != self.policy.project_key
        ):
            raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
        return self.policy

    def current(self, request_id: str) -> AuthoritySnapshot:
        self.calls += 1
        if self.unavailable:
            raise TimeoutError("service supplied secret must not enter the journal")
        if self.calls == self.revoke_on_call:
            self.approval = None
        return AuthoritySnapshot(self.policy, "b" * 40, self.approval)


class FakeAdapter:
    def __init__(self, clock: Clock):
        self.clock = clock
        self.resources: dict[str, dict[str, Any]] = {}
        self.created_by: str | None = None
        self.writes: list[str] = []
        self.reads: list[str] = []
        self.crash_after: str | None = None
        self.timeout_after: str | None = None
        self.timeout_before: str | None = None
        self.policy_deployed = True
        self.source_sha = "a" * 40
        self.ci_status = "success"
        self.webhook = True
        self.protected = True
        self.overrides: dict[str, dict[str, Any]] = {}
        self.cancel_after: tuple[Journal, str, str] | None = None

    def inspect(self, context: OperationContext) -> Observation:
        operation = context.operation.operation_id
        self.reads.append(operation)
        evidence: dict[str, Any] = {
            "observed_at": stamp(self.clock()),
            "repository": context.plan.project.repository,
            "repository_id": 41,
            "enrollment_id": 73,
            "branch": context.plan.project.default_branch,
            "visibility": context.plan.project.visibility,
            "source_sha": self.source_sha,
            "policy_digest": context.plan.policy_digest,
            "enrollment_revision": "b" * 40,
            "deployed_package_digest": "sha256:" + "c" * 64,
            "pipeline_id": 91,
            "workflow": WORKFLOW,
            "context": CONTEXT,
            "event": "push",
            "ci_status": self.ci_status,
            "clone_read_only": True,
            "pull_read_only": True,
            "webhook_delivered": self.webhook,
            "branch_protection": self.protected,
            "protection_probe": self.protected,
        }
        evidence.update(self.overrides.get(operation, {}))
        if operation == "catalog":
            return Observation(
                "verified", evidence, {"artifact_digest": "sha256:" + "d" * 64}
            )
        if operation == "source":
            return Observation("verified", evidence, {"source_sha": self.source_sha})
        if operation == "policy":
            return (
                Observation(
                    "verified", evidence, {"policy_digest": context.plan.policy_digest}
                )
                if self.policy_deployed
                else Observation("pending", evidence)
            )
        if operation == "readiness":
            return Observation("verified", evidence)
        if operation not in self.resources:
            return Observation("absent", evidence)
        return Observation(
            "verified",
            evidence,
            self.resources[operation],
            self.created_by == context.request_id,
        )

    def mutate(self, context: OperationContext) -> None:
        operation = context.operation.operation_id
        self.writes.append(operation)
        if self.timeout_before == operation:
            self.timeout_before = None
            raise TimeoutError("private service response")
        self.resources[operation] = (
            {"repository_id": 41}
            if operation == "repository"
            else {"enrollment_id": 73}
            if operation == "enrollment"
            else {"pipeline_id": 91}
            if operation == "ci"
            else {}
        )
        if operation == "repository":
            self.created_by = context.request_id
        if self.cancel_after is not None:
            journal, request_id, principal = self.cancel_after
            journal.cancel(request_id, principal)
            self.cancel_after = None
        if self.crash_after == operation:
            self.crash_after = None
            raise SystemExit("simulated worker death")
        if self.timeout_after == operation:
            self.timeout_after = None
            raise TimeoutError("private service response")


class ProtocolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = Clock()
        self.platform = Platform(
            "forgejo-local", "woodpecker-local", "JEFF7712/homelab"
        )
        self.project = Project(
            1,
            "molecular-ml",
            "JEFF7712/molecular-ml",
            "private",
            "main",
            "sha1",
            "a" * 40,
            "python-uv-v1",
            CAPABILITIES,
        )
        self.policy = Policy(
            self.platform,
            "agent-test",
            self.project.project_key,
            self.project.repository,
            "private",
            "sha256:" + "1" * 64,
        )
        self.discovery = Discovery(
            1, stamp(self.clock()), "absent", None, None, None, None
        )
        self.plan = plan(self.project, self.discovery, self.policy, self.clock())
        self.key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
        self.keys = {"test-operator": self.key.public_key()}
        self.authority = FakeAuthority(self.policy)
        self.adapter = FakeAdapter(self.clock)
        self.journal = Journal(Path(self.temp.name) / "worker" / "journal.sqlite")
        self.worker = Worker(
            self.journal, self.authority, self.keys, self.adapter, self.clock
        )

    def submit(self, key: str = "submission-1") -> str:
        return self.worker.submit(self.plan, self.policy.principal, key)

    def approve(self, target_request_id: str, **changes: Any) -> SignedApproval:
        approval = Approval(
            1,
            "grant-1",
            target_request_id,
            digest(document(self.plan)),
            self.plan.principal,
            self.project.project_key,
            self.platform,
            self.plan.policy_digest,
            tuple(operation.operation_id for operation in self.plan.operations),
            stamp(self.clock() - timedelta(seconds=1)),
            stamp(self.clock() + timedelta(seconds=120)),
            "b" * 40,
            digest(None),
        )
        approval = replace(approval, **changes)
        payload = json.dumps(
            document(approval), sort_keys=True, separators=(",", ":")
        ).encode("ascii")
        signature = base64.b64encode(
            self.key.sign(b"projectctl.approval.v1\n" + payload)
        ).decode("ascii")
        signed = SignedApproval(1, approval, "test-operator", signature)
        self.authority.approval = signed
        return signed

    def run_request(self) -> tuple[str, dict[str, Any]]:
        request_id = self.submit()
        self.approve(request_id)
        return request_id, self.worker.run(request_id, self.policy.principal)

    def operation(self, status: dict[str, Any], name: str) -> dict[str, Any]:
        return next(item for item in status["operations"] if item["id"] == name)

    def assert_code(self, expected: Code, callback: Any) -> None:
        with self.assertRaises(ProtocolError) as raised:
            callback()
        self.assertEqual(raised.exception.code, expected)

    def test_plan_is_deterministic_and_has_no_side_effects(self) -> None:
        self.assertEqual(
            document(self.plan),
            document(plan(self.project, self.discovery, self.policy, self.clock())),
        )
        self.assertEqual(Plan.parse(loads(canonical(document(self.plan)))), self.plan)
        self.assertEqual(self.adapter.writes, [])
        self.assertEqual(self.authority.calls, 0)

    def test_strict_documents_reject_unknown_fields_and_types(self) -> None:
        modifications = [
            ("schema_version", True),
            ("schema_version", 2),
            ("extra", "command"),
            ("operations", []),
            ("registry_transitions", ["enrolled", "pending"]),
        ]
        for field, value in modifications:
            with self.subTest(field=field, value=value):
                payload = document(self.plan)
                payload[field] = value
                self.assert_code(Code.INVALID_SPEC, lambda: Plan.parse(payload))
        for field, value in [
            ("source_sha", "main"),
            ("object_format", "sha256"),
            ("ci_contract", "root-shell"),
            ("default_branch", "x/../main"),
            ("default_branch", "main.lock/topic"),
            ("repository", "../../homelab"),
        ]:
            with self.subTest(field=field, value=value):
                payload = document(self.project)
                payload[field] = value
                self.assert_code(Code.INVALID_SPEC, lambda: Project.parse(payload))

    def test_canonical_rejections_and_boundaries(self) -> None:
        for raw in [
            '{"a":1,"a":2}',
            '{"a":{"b":1,"b":2}}',
            '{"x":NaN}',
            '{"x":1.0}',
            '{"x":9007199254740992}',
            '{"x":"\\n"}',
            '{"x":"é"}',
            "[]" * 140000,
            "[" * 1000 + "]" * 1000,
        ]:
            with self.subTest(raw=raw[:60]):
                self.assert_code(Code.INVALID_SPEC, lambda: loads(raw))
        self.assertEqual(
            canonical({"b": -9007199254740991, "a": 9007199254740991}),
            b'{"a":9007199254740991,"b":-9007199254740991}',
        )

    def test_cross_process_canonicalization_vector(self) -> None:
        vectors = json.loads(
            (ROOT / "tests/fixtures/projectctl/canonical.json").read_text()
        )
        for vector in vectors:
            self.assertEqual(canonical(vector["payload"]).decode(), vector["canonical"])
            self.assertEqual(digest(vector["payload"]), vector["digest"])
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import sys; from scripts.projectctl.codec import loads,digest; print(digest(loads(sys.stdin.read())))",
                ],
                input=json.dumps(vector["payload"], indent=2),
                text=True,
                capture_output=True,
                check=True,
                cwd=ROOT,
            )
            self.assertEqual(result.stdout.strip(), vector["digest"])

    def test_malformed_operation_graph_is_rejected(self) -> None:
        for change in ("cycle", "unknown_output", "wrong_type", "duplicate", "command"):
            payload = document(self.plan)
            if change == "cycle":
                payload["operations"][0]["depends_on"] = ["readiness"]
            elif change == "unknown_output":
                payload["operations"][1]["inputs"][0]["output"] = "caller-supplied-id"
            elif change == "wrong_type":
                payload["operations"][1]["inputs"][0]["value_type"] = "shell"
            elif change == "duplicate":
                payload["operations"].append(copy.deepcopy(payload["operations"][0]))
            else:
                payload["operations"][0]["command"] = "sudo"
            with self.subTest(change=change):
                self.assert_code(Code.INVALID_SPEC, lambda: Plan.parse(payload))

    def test_missing_stale_future_and_moved_discovery_are_blocked(self) -> None:
        for discovery, code in [
            (
                replace(self.discovery, repository_state="unknown"),
                Code.DEPENDENCY_UNAVAILABLE,
            ),
            (
                replace(self.discovery, observed_at="2026-10-14T12:00:00Z"),
                Code.PLAN_STALE,
            ),
            (
                replace(self.discovery, observed_at="2026-10-16T12:00:00Z"),
                Code.PLAN_STALE,
            ),
            (
                Discovery(
                    1, stamp(self.clock()), "present", 41, "private", "main", "e" * 40
                ),
                Code.SOURCE_MOVED,
            ),
        ]:
            with self.subTest(code=code):
                self.assert_code(
                    code,
                    lambda: plan(self.project, discovery, self.policy, self.clock()),
                )

    def test_policy_admission_uses_trusted_configuration(self) -> None:
        forged = replace(self.plan, principal="other-agent")
        self.assert_code(
            Code.AUTHORIZATION_REQUIRED,
            lambda: self.worker.submit(forged, "other-agent", "key"),
        )
        self.assert_code(
            Code.AUTHORIZATION_REQUIRED,
            lambda: plan(
                replace(self.project, visibility="public"),
                self.discovery,
                self.policy,
                self.clock(),
            ),
        )
        self.assert_code(
            Code.POLICY_CHANGED,
            lambda: self.worker.submit(
                replace(self.plan, policy_digest="sha256:" + "f" * 64),
                self.plan.principal,
                "key",
            ),
        )

    def test_unsigned_request_cannot_mutate(self) -> None:
        request_id = self.submit()
        status = self.worker.run(request_id, self.plan.principal)
        self.assertEqual(status["state"], "blocked")
        self.assertEqual(self.adapter.writes, [])

    def test_approval_binds_request_identity_plan_scope_and_time(self) -> None:
        request_id = self.submit()
        cases = [
            ({"request_id": "req-other"}, Code.AUTHORIZATION_REQUIRED),
            ({"principal": "another-agent"}, Code.AUTHORIZATION_REQUIRED),
            ({"project_key": "another-project"}, Code.AUTHORIZATION_REQUIRED),
            (
                {"platform": replace(self.platform, forgejo="other-instance")},
                Code.AUTHORIZATION_REQUIRED,
            ),
            ({"plan_digest": "sha256:" + "f" * 64}, Code.AUTHORIZATION_REQUIRED),
            ({"policy_digest": "sha256:" + "f" * 64}, Code.AUTHORIZATION_REQUIRED),
            ({"allowed_operations": ("clone",)}, Code.AUTHORIZATION_REQUIRED),
            (
                {"allowed_operations": ("repository", "root")},
                Code.AUTHORIZATION_REQUIRED,
            ),
            ({"expires_at": stamp(self.clock())}, Code.AUTHORIZATION_EXPIRED),
            (
                {"expires_at": stamp(self.clock() + timedelta(days=1))},
                Code.AUTHORIZATION_REQUIRED,
            ),
            (
                {"not_before": stamp(self.clock() + timedelta(seconds=10))},
                Code.AUTHORIZATION_REQUIRED,
            ),
        ]
        for changes, code in cases:
            self.approve(request_id, **changes)
            with self.subTest(changes=changes):
                self.assert_code(
                    code,
                    lambda: verify(
                        self.plan,
                        request_id,
                        "repository",
                        self.plan.principal,
                        self.authority.current(request_id),
                        self.keys,
                        self.clock(),
                    ),
                )
        signed = self.approve(request_id)
        self.authority.approval = replace(
            signed, payload=replace(signed.payload, grant_id="tampered")
        )
        self.assert_code(
            Code.AUTHORIZATION_REQUIRED,
            lambda: verify(
                self.plan,
                request_id,
                "repository",
                self.plan.principal,
                self.authority.current(request_id),
                self.keys,
                self.clock(),
            ),
        )
        self.assert_code(
            Code.AUTHORIZATION_REQUIRED,
            lambda: verify(
                self.plan,
                request_id,
                "repository",
                self.plan.principal,
                AuthoritySnapshot(self.policy, "b" * 40, signed),
                {},
                self.clock(),
            ),
        )

    def test_signature_domain_is_separate(self) -> None:
        request_id = self.submit()
        signed = self.approve(request_id)
        wrong = base64.b64encode(
            self.key.sign(canonical(document(signed.payload)))
        ).decode()
        self.authority.approval = replace(signed, signature=wrong)
        self.assertEqual(SIGNATURE_PREFIX, b"projectctl.approval.v1\n")
        self.assert_code(
            Code.AUTHORIZATION_REQUIRED,
            lambda: verify(
                self.plan,
                request_id,
                "repository",
                self.plan.principal,
                self.authority.current(request_id),
                self.keys,
                self.clock(),
            ),
        )

    def test_complete_onboarding_uses_verified_allocation_outputs(self) -> None:
        request_id, status = self.run_request()
        self.assertEqual(status["state"], "succeeded", status)
        self.assertEqual(
            self.adapter.writes,
            ["repository", "clone", "enrollment", "pull", "protection", "ci"],
        )
        self.assertTrue(
            all(item["state"] == "verified" for item in status["operations"])
        )
        attempts = self.journal.attempts(request_id, self.plan.principal)
        self.assertEqual(len(attempts), 6)
        self.assertTrue(
            all(
                loads(attempt["grant"])["grant_id"] == "grant-1" for attempt in attempts
            )
        )
        self.assertEqual(
            self.operation(status, "enrollment")["outputs"], {"enrollment_id": 73}
        )

    def test_disconnect_and_retry_return_same_request(self) -> None:
        request_id = self.submit()
        self.approve(request_id)
        self.adapter.crash_after = "repository"
        with self.assertRaises(SystemExit):
            self.worker.run(request_id, self.plan.principal)
        reopened = Journal(self.journal.path)
        worker = Worker(reopened, self.authority, self.keys, self.adapter, self.clock)
        self.assertEqual(
            worker.submit(self.plan, self.plan.principal, "submission-1"), request_id
        )
        status = worker.run(request_id, self.plan.principal)
        self.assertEqual(status["state"], "succeeded", status)
        self.assertEqual(self.adapter.writes.count("repository"), 1)
        self.assertEqual(
            loads(reopened.attempts(request_id, self.plan.principal)[0]["outputs"]),
            {"repository_id": 41},
        )

    def test_timeout_after_success_is_reconciled_without_replay(self) -> None:
        request_id = self.submit()
        self.approve(request_id)
        self.adapter.timeout_after = "repository"
        first = self.worker.run(request_id, self.plan.principal)
        self.assertEqual(self.operation(first, "repository")["state"], "uncertain")
        second = self.worker.run(request_id, self.plan.principal)
        self.assertEqual(second["state"], "succeeded", second)
        self.assertEqual(self.adapter.writes.count("repository"), 1)

    def test_timeout_before_effect_has_bounded_retry(self) -> None:
        request_id = self.submit()
        self.approve(request_id)
        self.adapter.timeout_before = "repository"
        self.assertEqual(
            self.worker.run(request_id, self.plan.principal)["code"],
            Code.OUTCOME_UNCERTAIN,
        )
        self.worker.run(request_id, self.plan.principal)
        self.assertEqual(self.adapter.writes, ["repository"])
        self.clock.advance(3)
        final = self.worker.run(request_id, self.plan.principal)
        self.assertEqual(final["state"], "succeeded", final)
        self.assertEqual(self.adapter.writes.count("repository"), 2)

    def test_uncertain_outcomes_return_to_the_reconciler_until_exhausted(
        self,
    ) -> None:
        request_id = self.submit()
        self.approve(request_id)
        for attempt in range(1, UNCERTAIN_RETRIES + 1):
            self.adapter.timeout_before = "repository"
            status = self.worker.run(request_id, self.plan.principal)
            self.assertEqual(status["code"], Code.OUTCOME_UNCERTAIN)
            self.assertEqual(self.adapter.writes.count("repository"), attempt)
            pending = request_id in [item[0] for item in self.journal.pending()]
            if attempt < UNCERTAIN_RETRIES:
                self.assertEqual(status["state"], "waiting", status)
                self.assertTrue(pending)
            else:
                self.assertEqual(status["state"], "blocked", status)
                self.assertFalse(pending)
            self.clock.advance(2**attempt + 1)

    def test_unknown_repository_cannot_be_adopted_by_name(self) -> None:
        self.adapter.resources["repository"] = {"repository_id": 41}
        _, status = self.run_request()
        self.assertEqual(status["code"], Code.RESOURCE_CONFLICT)
        self.assertEqual(self.adapter.writes, [])

    def test_uncertain_creation_requires_ownership_proof(self) -> None:
        request_id = self.submit()
        self.approve(request_id)
        self.adapter.crash_after = "repository"
        with self.assertRaises(SystemExit):
            self.worker.run(request_id, self.plan.principal)
        self.adapter.created_by = "different-request"
        status = self.worker.run(request_id, self.plan.principal)
        self.assertEqual(status["code"], Code.RESOURCE_CONFLICT)
        self.assertEqual(self.adapter.writes, ["repository"])

    def test_expired_grant_allows_discovery_but_no_more_writes(self) -> None:
        request_id = self.submit()
        self.approve(request_id)
        self.adapter.crash_after = "repository"
        with self.assertRaises(SystemExit):
            self.worker.run(request_id, self.plan.principal)
        self.clock.advance(121)
        status = self.worker.run(request_id, self.plan.principal)
        self.assertEqual(status["code"], Code.AUTHORIZATION_EXPIRED)
        self.assertEqual(self.operation(status, "repository")["state"], "verified")
        self.assertEqual(self.adapter.writes, ["repository"])

    def test_revocation_is_refreshed_before_each_write(self) -> None:
        request_id = self.submit()
        self.approve(request_id)
        self.authority.revoke_on_call = 3
        status = self.worker.run(request_id, self.plan.principal)
        self.assertEqual(status["code"], Code.AUTHORIZATION_REVOKED)
        self.assertEqual(self.adapter.writes, ["repository"])

    def test_policy_change_and_unavailable_authority_stop_writes(self) -> None:
        request_id = self.submit()
        self.approve(request_id)
        self.authority.policy = replace(
            self.policy, implementation_digest="sha256:" + "f" * 64
        )
        self.assertEqual(
            self.worker.run(request_id, self.plan.principal)["code"],
            Code.POLICY_CHANGED,
        )
        self.authority.unavailable = True
        self.assertEqual(
            self.worker.run(request_id, self.plan.principal)["code"],
            Code.DEPENDENCY_UNAVAILABLE,
        )
        self.assertEqual(self.adapter.writes, [])

    def test_pending_policy_requires_explicit_dispatch_after_deployment(self) -> None:
        self.adapter.policy_deployed = False
        request_id, status = self.run_request()
        self.assertEqual(status["state"], "waiting")
        self.assertNotIn("ci", self.adapter.writes)
        self.adapter.policy_deployed = True
        reads, writes, refreshes = (
            len(self.adapter.reads),
            len(self.adapter.writes),
            self.authority.calls,
        )
        self.journal.status(request_id, self.plan.principal)
        self.assertEqual(
            (len(self.adapter.reads), len(self.adapter.writes), self.authority.calls),
            (reads, writes, refreshes),
        )
        self.clock.advance(3)
        self.assertEqual(
            self.worker.run(request_id, self.plan.principal)["state"], "succeeded"
        )

    def test_older_green_pipeline_is_rejected(self) -> None:
        self.adapter.overrides["ci"] = {"source_sha": "e" * 40}
        request_id, first = self.run_request()
        self.assertNotEqual(first["state"], "succeeded")
        second = self.worker.run(request_id, self.plan.principal)
        self.assertEqual(second["code"], Code.SOURCE_MOVED)
        self.assertEqual(self.adapter.writes.count("ci"), 1)

    def test_webhook_protection_and_pipeline_failure_are_independent_gates(
        self,
    ) -> None:
        self.adapter.webhook = False
        request_id, status = self.run_request()
        self.assertEqual(status["state"], "waiting")
        self.adapter.webhook = True
        self.adapter.protected = False
        self.clock.advance(3)
        self.assertEqual(
            self.worker.run(request_id, self.plan.principal)["state"], "waiting"
        )
        self.adapter.protected = True
        self.adapter.ci_status = "failure"
        self.clock.advance(3)
        status = self.worker.run(request_id, self.plan.principal)
        self.assertEqual(status["code"], Code.CI_FAILED)
        self.assertEqual(status["state"], "failed")

    def test_source_drift_is_rechecked_before_ci(self) -> None:
        self.adapter.policy_deployed = False
        request_id, _ = self.run_request()
        self.adapter.policy_deployed = True
        self.adapter.source_sha = "e" * 40
        self.clock.advance(3)
        status = self.worker.run(request_id, self.plan.principal)
        self.assertEqual(status["code"], Code.SOURCE_MOVED)
        self.assertNotIn("ci", self.adapter.writes)

    def test_same_key_conflict_and_cross_request_ownership(self) -> None:
        request_id = self.submit()
        self.assertEqual(self.submit(), request_id)
        changed = replace(self.plan, project=replace(self.project, source_sha="e" * 40))
        self.assert_code(
            Code.IDEMPOTENCY_CONFLICT,
            lambda: self.worker.submit(changed, self.plan.principal, "submission-1"),
        )
        self.assert_code(Code.RESOURCE_CONFLICT, lambda: self.submit("another-key"))
        alias = replace(self.plan, project=replace(self.project, project_key="alias"))
        self.assert_code(
            Code.RESOURCE_CONFLICT,
            lambda: self.journal.submit(alias, self.plan.principal, "alias"),
        )
        self.assert_code(
            Code.AUTHORIZATION_REQUIRED,
            lambda: self.journal.status(request_id, "another-agent"),
        )

    def test_host_lock_rejects_concurrent_execution(self) -> None:
        request_id = self.submit()
        self.approve(request_id)
        with self.journal.execution():
            self.assert_code(
                Code.RESOURCE_CONFLICT,
                lambda: self.worker.run(request_id, self.plan.principal),
            )
        self.assertEqual(self.adapter.writes, [])

    def test_cancellation_is_terminal_and_retains_allocated_resources(self) -> None:
        request_id = self.submit()
        self.approve(request_id)
        self.adapter.cancel_after = (self.journal, request_id, self.plan.principal)
        status = self.worker.run(request_id, self.plan.principal)
        self.assertEqual(status["state"], "cancelled")
        self.assertEqual(self.operation(status, "repository")["state"], "verified")
        self.assertEqual(self.adapter.writes, ["repository"])
        self.assertEqual(
            self.worker.run(request_id, self.plan.principal)["state"], "cancelled"
        )
        self.assertEqual(self.adapter.writes, ["repository"])
        other = self.submit("new-request")
        self.assertNotEqual(other, request_id)
        self.assertEqual(
            self.worker.run(other, self.plan.principal)["state"], "blocked"
        )

    def test_cancelled_crash_can_only_reconcile_the_inflight_attempt(self) -> None:
        request_id = self.submit()
        self.approve(request_id)
        self.adapter.crash_after = "repository"
        with self.assertRaises(SystemExit):
            self.worker.run(request_id, self.plan.principal)
        self.journal.cancel(request_id, self.plan.principal)
        status = self.worker.run(request_id, self.plan.principal)
        self.assertEqual(status["state"], "cancelled")
        self.assertEqual(self.operation(status, "repository")["state"], "verified")
        self.assertEqual(self.adapter.writes, ["repository"])

    def test_observation_does_not_persist_raw_secrets_or_extra_fields(self) -> None:
        self.adapter.overrides["repository"] = {"authorization": "secret-service-token"}
        request_id, _ = self.run_request()
        self.assertEqual(self.adapter.writes, [])
        self.assertNotIn(
            "secret-service-token",
            json.dumps(self.journal.status(request_id, self.plan.principal)),
        )
        with closing(sqlite3.connect(self.journal.path)) as db, db:
            self.assertNotIn("secret-service-token", "\n".join(db.iterdump()))

    def test_journal_permissions_and_unknown_schema(self) -> None:
        self.assertEqual(self.journal.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.journal.path.parent.stat().st_mode & 0o777, 0o700)
        self.journal.path.chmod(0o644)
        self.assert_code(Code.RESOURCE_CONFLICT, lambda: Journal(self.journal.path))
        self.journal.path.chmod(0o600)
        with closing(sqlite3.connect(self.journal.path)) as db, db:
            db.execute("PRAGMA user_version=99")
        self.assert_code(Code.INVALID_SPEC, lambda: Journal(self.journal.path))

    def test_error_envelope_has_no_untrusted_text(self) -> None:
        error = ProtocolError(Code.RESOURCE_CONFLICT, "repository")
        self.assertEqual(error.exit_code, 4)
        self.assertFalse(error.envelope()["ok"])
        self.assertEqual(error.envelope()["error"]["recovery"]["action"], "review")
        self.assertEqual(success({"state": "blocked"})["error"], None)

    def test_retry_rejoins_request_after_discovery_expires(self) -> None:
        request_id = self.submit()
        self.clock.advance(301)
        self.assertEqual(self.submit(), request_id)
        self.assert_code(Code.PLAN_STALE, lambda: self.submit("new-key"))

    def test_two_process_submissions_coalesce(self) -> None:
        program = (
            "import sys; from pathlib import Path; "
            "from scripts.projectctl.codec import loads; "
            "from scripts.projectctl.models import Plan; "
            "from scripts.projectctl.journal import Journal; "
            "journal=Journal(Path(sys.argv[1])); print('ready',flush=True); "
            "plan=Plan.parse(loads(sys.stdin.readline())); "
            "print(journal.submit(plan,plan.principal,'concurrent'),flush=True)"
        )
        with ExitStack() as stack:
            processes = []
            for _ in range(2):
                process = stack.enter_context(
                    subprocess.Popen(
                        [sys.executable, "-c", program, str(self.journal.path)],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        cwd=ROOT,
                    )
                )
                self.assertIsNotNone(process.stdout)
                assert process.stdout is not None
                self.assertEqual(process.stdout.readline().strip(), "ready")
                processes.append(process)
            for process in processes:
                assert process.stdin is not None
                process.stdin.write(canonical(document(self.plan)).decode() + "\n")
                process.stdin.flush()
            identifiers = []
            for process in processes:
                assert process.stdout is not None
                identifiers.append(process.stdout.readline().strip())
                self.assertEqual(process.wait(timeout=10), 0)
            self.assertEqual(identifiers[0], identifiers[1])
            self.assertTrue(identifiers[0].startswith("req-"))

    def test_existing_repository_requires_the_approved_numeric_id(self) -> None:
        self.plan = plan(
            self.project,
            Discovery(
                1,
                stamp(self.clock()),
                "present",
                41,
                "private",
                "main",
                self.project.source_sha,
            ),
            self.policy,
            self.clock(),
        )
        self.adapter.resources["repository"] = {"repository_id": 41}
        _, status = self.run_request()
        self.assertEqual(status["state"], "succeeded", status)
        self.assertNotIn("repository", self.adapter.writes)

    def test_lost_journal_does_not_adopt_old_allocations(self) -> None:
        old_request, _ = self.run_request()
        other_journal = Journal(Path(self.temp.name) / "restored" / "journal.sqlite")
        worker = Worker(
            other_journal, self.authority, self.keys, self.adapter, self.clock
        )
        request_id = worker.submit(self.plan, self.plan.principal, "new-journal")
        self.assertNotEqual(request_id, old_request)
        self.approve(request_id)
        status = worker.run(request_id, self.plan.principal)
        self.assertEqual(status["code"], Code.RESOURCE_CONFLICT)
        self.assertEqual(self.adapter.writes.count("repository"), 1)

    def test_revoked_grant_cannot_retry_a_known_absent_write(self) -> None:
        request_id = self.submit()
        self.approve(request_id)
        self.adapter.timeout_before = "repository"
        self.worker.run(request_id, self.plan.principal)
        self.clock.advance(3)
        self.authority.approval = None
        status = self.worker.run(request_id, self.plan.principal)
        self.assertEqual(status["code"], Code.AUTHORIZATION_REVOKED)
        self.assertEqual(self.adapter.writes, ["repository"])

    def test_cancelled_grant_cannot_authorize_a_new_request(self) -> None:
        first = self.submit()
        self.approve(first)
        self.journal.cancel(first, self.plan.principal)
        second = self.submit("new-key")
        status = self.worker.run(second, self.plan.principal)
        self.assertEqual(status["code"], Code.AUTHORIZATION_REQUIRED)
        self.assertEqual(self.adapter.writes, [])

    def test_stale_evidence_and_non_numeric_outputs_cannot_advance(self) -> None:
        self.adapter.overrides["repository"] = {"observed_at": "2026-10-14T12:00:00Z"}
        request_id, status = self.run_request()
        self.assertEqual(status["code"], Code.VERIFICATION_PENDING)
        self.assertEqual(self.adapter.writes, [])
        self.adapter.overrides.clear()
        self.adapter.resources["repository"] = {"repository_id": "untrusted-output"}
        self.clock.advance(3)
        self.assertEqual(
            self.worker.run(request_id, self.plan.principal)["code"], Code.INVALID_SPEC
        )
        self.assertEqual(self.adapter.writes, [])

    def test_journal_rejects_symlink_database(self) -> None:
        target = self.journal.path.parent / "linked.sqlite"
        target.symlink_to(self.journal.path)
        self.assert_code(Code.RESOURCE_CONFLICT, lambda: Journal(target))

    def test_known_signature_vector_verifies_in_separate_process(self) -> None:
        vector = json.loads(
            (ROOT / "tests/fixtures/projectctl/approval.json").read_text()
        )
        self.assertEqual(document(self.approve("req-vector")), vector["approval"])
        signed = SignedApproval.parse(vector["approval"])
        evidence = verify(
            self.plan,
            "req-vector",
            "repository",
            self.plan.principal,
            AuthoritySnapshot(self.policy, "b" * 40, signed),
            self.keys,
            self.clock(),
        )
        self.assertEqual(evidence.policy_digest, self.plan.policy_digest)
        program = (
            "import sys,json,base64; "
            "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey; "
            "v=json.load(sys.stdin); a=v['approval']; "
            "p=json.dumps(a['payload'],sort_keys=True,separators=(',',':')).encode('ascii'); "
            "Ed25519PublicKey.from_public_bytes(bytes.fromhex(v['public_key'])).verify("
            "base64.b64decode(a['signature']),b'projectctl.approval.v1\\n'+p); print('verified')"
        )
        result = subprocess.run(
            [sys.executable, "-c", program],
            input=json.dumps(vector),
            text=True,
            capture_output=True,
            check=True,
            cwd=ROOT,
        )
        self.assertEqual(result.stdout.strip(), "verified")


if __name__ == "__main__":
    unittest.main()
