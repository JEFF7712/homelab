from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .codec import digest, document
from .config import Configuration
from .errors import Code, ProtocolError
from .http import segment
from .journal import Journal
from .models import CAPABILITIES, Project, obj, stamp, string
from .planner import plan
from .remote import RemoteAuthority, discover
from .services import ServiceAdapter
from .worker import Worker


class Runtime:
    def __init__(self, config: Configuration):
        self.config = config
        self.journal = Journal(config.state_directory / "journal.sqlite")
        self.authority = RemoteAuthority(config, self.journal)
        self.adapter = ServiceAdapter(config, self.journal, self.authority)
        self.worker = Worker(
            self.journal,
            self.authority,
            config.trusted_keys,
            self.adapter,
            lambda: datetime.now(timezone.utc),
        )

    def handle(self, principal: str, request: Any) -> dict[str, Any]:
        request = obj(request, "schema_version action payload")
        action, payload = string(request["action"]), request["payload"]
        string(principal)
        if action == "plan":
            project = Project.parse(payload)
            policy = self.authority.admit(principal, project)
            candidate = plan(
                project,
                discover(self.config, project),
                policy,
                datetime.now(timezone.utc),
            )
            if policy.routine_onboarding:
                self.authority.check_routine(
                    {"principal": principal, "plan": document(candidate)},
                    self.authority.main_revision(),
                )
            return {
                "plan": document(candidate),
                "plan_digest": digest(document(candidate)),
            }
        if action == "request":
            from .models import Plan

            payload = obj(payload, "plan plan_digest idempotency_key")
            candidate = Plan.parse(payload["plan"])
            if payload["plan_digest"] != digest(document(candidate)):
                raise ProtocolError(Code.INVALID_SPEC)
            if self.authority.admit(principal, candidate.project).routine_onboarding:
                self.authority.check_routine(
                    {"principal": principal, "plan": document(candidate)},
                    self.authority.main_revision(),
                )
            request_id = self.worker.submit(
                candidate, principal, string(payload["idempotency_key"])
            )
            self.authority.capture_context(request_id, principal)
            if self.authority.admit(principal, candidate.project).routine_onboarding:
                status = self.journal.status(request_id, principal)
                if status["state"] == "awaiting_authorization":
                    self.journal.state(request_id, "queued")
            return self.journal.status(request_id, principal)
        if action in {"status", "cancel", "approval-context"}:
            payload = obj(payload, "request_id")
            request_id = string(payload["request_id"])
            if action == "cancel":
                self.journal.cancel(request_id, principal)
            if action == "approval-context":
                return self.authority.approval_context(request_id, principal)
            return self.journal.status(request_id, principal)
        if action == "doctor":
            payload = obj(payload, "project_key")
            return self.doctor(principal, string(payload["project_key"]))
        raise ProtocolError(Code.INVALID_SPEC)

    def reconcile(self) -> dict[str, Any]:
        results = []
        for request_id, principal in self.journal.pending():
            status = self.journal.status(request_id, principal)
            try:
                project = Project.parse(status["plan"]["project"])
                if not self.config.policy(principal, project).routine_onboarding:
                    continue
                self.authority.capture_context(request_id, principal)
                result = self.worker.run(request_id, principal)
                results.append(
                    {
                        "request_id": request_id,
                        "state": result["state"],
                        "code": result["code"],
                    }
                )
            except ProtocolError as error:
                if error.code == Code.RESOURCE_CONFLICT:
                    results.append(
                        {
                            "request_id": request_id,
                            "state": self.journal.status(request_id, principal)[
                                "state"
                            ],
                            "code": error.code.value,
                        }
                    )
                    continue
                state = (
                    "waiting"
                    if error.code == Code.DEPENDENCY_UNAVAILABLE
                    else "blocked"
                )
                self.journal.state(request_id, state, error.code)
                results.append(
                    {
                        "request_id": request_id,
                        "state": state,
                        "code": error.code.value,
                    }
                )
        return {"requests": results}

    def doctor(self, principal: str, project_key: str) -> dict[str, Any]:
        scopes = [
            scope
            for scope in self.config.scopes
            if scope.principal == principal and scope.project_key == project_key
        ]
        if len(scopes) != 1:
            raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
        scope = scopes[0]
        report: dict[str, Any] = {
            "schema_version": 1,
            "project_key": project_key,
            "observed_at": stamp(datetime.now(timezone.utc)),
            "capabilities": {key: {"state": "unknown"} for key in CAPABILITIES},
        }
        repository = self.config.apis["forgejo"].request(
            f"/api/v1/repos/{scope.repository}", missing=True
        )
        if repository is None:
            report["capabilities"]["repository"] = {"state": "missing"}
            return report
        if repository.get("full_name") != scope.repository:
            raise ProtocolError(Code.RESOURCE_CONFLICT)
        branch = repository["default_branch"]
        head = self.config.apis["forgejo"].request(
            f"/api/v1/repos/{scope.repository}/branches/{segment(branch)}", missing=True
        )
        report["capabilities"]["repository"] = {
            "state": "ready"
            if repository.get("private") is (scope.visibility == "private") and head
            else "degraded",
            "repository_id": repository["id"],
            "branch": branch,
            "source_sha": head["commit"]["id"] if head else None,
        }
        enrollment = self.config.apis["woodpecker"].request(
            f"/api/repos/lookup/{scope.repository}", missing=True
        )
        if enrollment is None:
            report["capabilities"]["ci_enrollment"] = {"state": "missing"}
            return report
        if enrollment.get("full_name") != scope.repository or str(
            enrollment.get("forge_remote_id")
        ) != str(repository["id"]):
            raise ProtocolError(Code.RESOURCE_CONFLICT)
        report["capabilities"]["ci_enrollment"] = {
            "state": "ready"
            if self.adapter._enrollment_ready(enrollment, scope.visibility == "private")
            else "degraded",
            "enrollment_id": enrollment["id"],
        }
        if head:
            sha = head["commit"]["id"]
            pipelines = self.config.apis["woodpecker"].pages(
                f"/api/repos/{enrollment['id']}/pipelines?branch={segment(branch)}"
            )
            matching = [
                row
                for row in pipelines
                if row.get("commit") == sha
                and row.get("ref") == f"refs/heads/{branch}"
                and row.get("event") in {"push", "manual"}
            ]
            if matching:
                latest = max(matching, key=lambda row: row["number"])
                stamped = self.adapter._configuration_matches(
                    enrollment["id"], latest["number"]
                )
                detail = self.config.apis["woodpecker"].request(
                    f"/api/repos/{enrollment['id']}/pipelines/{latest['number']}"
                )
                passed = (
                    self.adapter.validation_passed(detail)
                    and self.adapter._commit_status(scope.repository, sha) == "success"
                )
                report["capabilities"]["ci_validation"] = {
                    "state": "ready"
                    if passed and stamped
                    else "degraded"
                    if stamped
                    else "unknown",
                    "pipeline_number": latest["number"],
                    "source_sha": sha,
                    "contract_verified": stamped,
                }
            try:
                rule = self.config.apis["forgejo"].request(
                    f"/api/v1/repos/{scope.repository}/branch_protections/{segment(branch)}",
                    missing=True,
                )
            except ProtocolError as error:
                if error.code != Code.AUTHORIZATION_REQUIRED:
                    raise
                report["capabilities"]["branch_protection"] = {
                    "state": "unknown",
                    "reason": "observer_cannot_read_protection",
                    "requires_operator_verification": True,
                }
            else:
                report["capabilities"]["branch_protection"] = {
                    "state": "configured"
                    if rule and rule.get("enable_status_check") is True
                    else "missing",
                    "enforcement_probe": "request-scoped evidence required",
                }
        return report
