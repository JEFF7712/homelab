from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from typing import Any

from .authorization import AuthoritySnapshot, Policy, SignedApproval, verify_signature
from .catalog import entry, verify_transition
from .codec import digest, document, loads
from .config import Configuration
from .errors import Code, ProtocolError
from .http import segment
from .journal import Journal
from .models import SHA, Discovery, Plan, Project, stamp, string


class RemoteAuthority:
    def __init__(self, config: Configuration, journal: Journal):
        self.config = config
        self.journal = journal
        self.forgejo = config.apis["forgejo"]

    def admit(self, principal: str, project: Project) -> Policy:
        return self.config.policy(principal, project)

    def main_revision(self) -> str:
        repo = self.config.platform.homelab_repository
        branch = self.forgejo.request(f"/api/v1/repos/{repo}/branches/main")
        return string(branch["commit"]["id"], SHA)

    def contents(self, path: str, revision: str, *, missing: bool = False) -> Any:
        string(revision, SHA)
        repo = self.config.platform.homelab_repository
        data = self.forgejo.request(
            f"/api/v1/repos/{repo}/contents/{path}?ref={revision}", missing=missing
        )
        if data is None:
            return None
        if (
            not isinstance(data, dict)
            or data.get("type") != "file"
            or data.get("encoding") != "base64"
        ):
            raise ProtocolError(Code.DEPENDENCY_UNAVAILABLE)
        try:
            raw = base64.b64decode(data["content"].replace("\n", ""), validate=True)
            return loads(raw)
        except (KeyError, ValueError, TypeError, binascii.Error):
            raise ProtocolError(Code.DEPENDENCY_UNAVAILABLE) from None

    def catalog(self, revision: str) -> dict[str, Any]:
        result = self.contents("config/ci/projects.json", revision)
        if not isinstance(result, dict):
            raise ProtocolError(Code.INVALID_SPEC)
        return result

    def baseline(
        self, status: dict[str, Any]
    ) -> tuple[
        SignedApproval | dict[str, Any] | None,
        str,
        dict[str, Any] | None,
        dict[str, Any],
    ]:
        request_id = string(status["request_id"])
        project = Plan.parse(status["plan"]).project
        revision = self.main_revision()
        catalog = self.catalog(revision)
        approval: SignedApproval | dict[str, Any] | None
        policy = self.config.policy(status["principal"], project)
        if policy.routine_onboarding:
            self.check_routine(status, revision)
            context = self.journal.authorization_context(request_id)
            if context is None:
                raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
            approval = context
            catalog_revision = context["catalog_revision"]
            entry_digest = context["catalog_entry_digest"]
        else:
            raw = self.contents(
                f"config/projectctl/approvals/{request_id}.json", revision, missing=True
            )
            if raw is None:
                return None, revision, None, catalog
            approval = SignedApproval.parse(raw)
            payload = verify_signature(approval, self.config.trusted_keys)
            if (
                payload.request_id != request_id
                or payload.principal != status["principal"]
                or payload.project_key != project.project_key
                or payload.platform != self.config.platform
                or payload.plan_digest != status["plan_digest"]
                or payload.policy_digest != policy.policy_digest
            ):
                raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
            catalog_revision = payload.catalog_revision
            entry_digest = payload.catalog_entry_digest
        baseline = entry(self.catalog(catalog_revision), project)
        if digest(baseline) != entry_digest:
            raise ProtocolError(Code.PLAN_STALE)
        enrollment = next(
            (
                row["outputs"].get("enrollment_id")
                for row in status["operations"]
                if row["id"] == "enrollment" and row["state"] == "verified"
            ),
            None,
        )
        verify_transition(project, baseline, entry(catalog, project), enrollment)
        return approval, revision, baseline, catalog

    def check_routine(self, status: dict[str, Any], revision: str) -> None:
        project = Plan.parse(status["plan"]).project
        scope = next(
            row
            for row in self.config.scopes
            if row.principal == status["principal"]
            and row.project_key == project.project_key
        )
        callers = self.contents(
            "config/projectctl/callers.json", revision, missing=True
        )
        expected = {
            "project_key": scope.project_key,
            "repository": scope.repository,
            "visibility": scope.visibility,
            "routine": scope.routine,
        }
        if (
            scope.routine is None
            or not isinstance(callers, dict)
            or expected not in callers.get(scope.principal, {}).get("projects", [])
        ):
            raise ProtocolError(Code.AUTHORIZATION_REVOKED)
        if (
            status["plan"]["discovery"]["repository_id"]
            != scope.routine["repository_id"]
        ):
            raise ProtocolError(Code.RESOURCE_CONFLICT)

    def current(self, request_id: str) -> AuthoritySnapshot:
        with self.journal.connection() as db:
            row = db.execute(
                "SELECT principal FROM requests WHERE id=?", (request_id,)
            ).fetchone()
        if row is None:
            raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
        status = self.journal.status(request_id, row["principal"])
        project = Plan.parse(status["plan"]).project
        approval, revision, _, _ = self.baseline(status)
        return AuthoritySnapshot(
            self.admit(row["principal"], project),
            revision,
            approval if isinstance(approval, SignedApproval) else None,
            routine=isinstance(approval, dict),
        )

    def approval_context(self, request_id: str, principal: str) -> dict[str, Any]:
        status = self.journal.status(request_id, principal)
        saved = self.journal.authorization_context(request_id)
        if saved is not None:
            return saved
        project = Plan.parse(status["plan"]).project
        revision = self.main_revision()
        target = entry(self.catalog(revision), project)
        return {
            "schema_version": 1,
            "request_id": request_id,
            "catalog_revision": revision,
            "catalog_entry_digest": digest(target),
        }

    def capture_context(self, request_id: str, principal: str) -> None:
        status = self.journal.status(request_id, principal)
        if not self.admit(
            principal, Plan.parse(status["plan"]).project
        ).routine_onboarding:
            return
        context = self.approval_context(request_id, principal)
        self.check_routine(status, context["catalog_revision"])
        self.journal.authorization_context(request_id, context)


def discover(config: Configuration, project: Project) -> Discovery:
    api = config.apis["forgejo"]
    repo = api.request(f"/api/v1/repos/{project.repository}", missing=True)
    observed = stamp(datetime.now(UTC))
    if repo is None:
        return Discovery(1, observed, "absent", None, None, None, None)
    if repo.get("full_name") != project.repository:
        raise ProtocolError(Code.RESOURCE_CONFLICT)
    branch = api.request(
        f"/api/v1/repos/{project.repository}/branches/{segment(project.default_branch)}",
        missing=True,
    )
    result = Discovery(
        1,
        observed,
        "present",
        repo["id"],
        "private" if repo.get("private") else "public",
        repo["default_branch"],
        branch["commit"]["id"] if branch else None,
    )
    return Discovery.parse(document(result))
