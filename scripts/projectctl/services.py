from __future__ import annotations

import base64
import json
import os
import stat
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import yaml

from .authorization import verify
from .catalog import desired, entry, patch
from .codec import canonical, digest, loads
from .config import Configuration
from .errors import Code, ProtocolError
from .http import Api, segment
from .journal import Journal
from .models import CI_STEPS, SHA, Project, number, stamp, string
from .proofs import PROTECTION_FIELDS, verify_probe
from .remote import RemoteAuthority
from .worker import CONTEXT, WORKFLOW, Observation, OperationContext

DEVELOPMENT_SETTINGS = {
    "allow_pr": True,
    "allow_deploy": False,
    "trusted": {"network": False, "volumes": False, "security": False},
    "config_extension_endpoint": "",
    "secret_extension_endpoint": "",
    "registry_extension_endpoint": "",
    "netrc_trusted": [],
}


class ServiceAdapter:
    delivery_timeout = 30.0

    def __init__(
        self, config: Configuration, journal: Journal, authority: RemoteAuthority
    ):
        self.config, self.journal, self.authority = config, journal, authority
        self.forgejo, self.woodpecker = (
            config.apis["forgejo"],
            config.apis["woodpecker"],
        )
        self.registry_api = Api(
            self.woodpecker.base,
            lambda _write: config.authorization("woodpecker", True),
            allow_plaintext=config.bindings["endpoints"]["woodpecker"][
                "allow_plaintext"
            ],
        )
        self.repository_admin_api = Api(
            self.forgejo.base,
            lambda _write: config.authorization("forgejo", True),
            allow_plaintext=config.bindings["endpoints"]["forgejo"]["allow_plaintext"],
        )

    def _repo(self, context: OperationContext) -> dict[str, Any] | None:
        repo = self.forgejo.request(
            f"/api/v1/repos/{context.plan.project.repository}", missing=True
        )
        if repo is None:
            if (
                context.inputs.get(
                    "repository_id", context.plan.discovery.repository_id
                )
                is not None
                or self.journal.allocation(context.request_id, "repository") is not None
            ):
                raise ProtocolError(Code.PLAN_STALE)
            return None
        if repo.get("full_name") != context.plan.project.repository:
            raise ProtocolError(Code.RESOURCE_CONFLICT)
        number(repo.get("id"))
        expected = context.inputs.get(
            "repository_id", context.plan.discovery.repository_id
        )
        if expected is not None and repo["id"] != expected:
            raise ProtocolError(Code.RESOURCE_CONFLICT)
        return repo

    def _write(
        self,
        context: OperationContext,
        service: str,
        path: str,
        *,
        method: str,
        body: dict[str, Any] | None = None,
    ) -> Any:
        if self._status(context)["state"] == "cancelled":
            raise ProtocolError(Code.AUTHORIZATION_REVOKED)
        verify(
            context.plan,
            context.request_id,
            context.operation.operation_id,
            context.plan.principal,
            self.authority.current(context.request_id),
            self.config.trusted_keys,
            datetime.now(timezone.utc),
        )
        repo = self._repo(context)
        if context.operation.operation_id != "repository":
            if (
                repo is None
                or repo.get("private")
                is not (context.plan.project.visibility == "private")
                or repo.get("default_branch") != context.plan.project.default_branch
            ):
                raise ProtocolError(Code.PLAN_STALE)
            if service == "woodpecker" and not path.startswith("/api/repos?"):
                enrollment = self._enrollment(context, repo)
                if enrollment is None or path.split("?")[0].split("/")[3] != str(
                    enrollment["id"]
                ):
                    raise ProtocolError(Code.RESOURCE_CONFLICT)
        return self.config.apis[service].request(path, method=method, body=body)

    @staticmethod
    def validation_passed(pipeline: dict[str, Any], ci_contract: str | None) -> bool:
        steps = {CI_STEPS[ci_contract]} if ci_contract else set(CI_STEPS.values())
        workflows = [
            row for row in pipeline.get("workflows", []) if row.get("name") == WORKFLOW
        ]
        return (
            pipeline.get("status") == "success"
            and len(workflows) == 1
            and workflows[0].get("state") == "success"
            and any(
                child.get("name") in steps and child.get("state") == "success"
                for child in workflows[0].get("children", [])
            )
        )

    def _enrollment(
        self, context: OperationContext, repo: dict[str, Any]
    ) -> dict[str, Any] | None:
        value = self.woodpecker.request(
            f"/api/repos/lookup/{context.plan.project.repository}", missing=True
        )
        if value is None:
            if (
                context.inputs.get("enrollment_id") is not None
                or self.journal.allocation(context.request_id, "enrollment") is not None
            ):
                raise ProtocolError(Code.PLAN_STALE)
            return None
        if value.get("full_name") != context.plan.project.repository or str(
            value.get("forge_remote_id")
        ) != str(repo["id"]):
            raise ProtocolError(Code.RESOURCE_CONFLICT)
        number(value.get("id"))
        if (
            "enrollment_id" in context.inputs
            and context.inputs["enrollment_id"] != value["id"]
        ):
            raise ProtocolError(Code.RESOURCE_CONFLICT)
        return value

    def _status(self, context: OperationContext) -> dict[str, Any]:
        return self.journal.status(context.request_id, context.plan.principal)

    def _enrollment_ready(self, enrollment: dict[str, Any], private: bool) -> bool:
        return (
            enrollment.get("active") is True
            and enrollment.get("private") is private
            and enrollment.get("visibility") == ("private" if private else "public")
            and all(
                (
                    key == "netrc_trusted"
                    and key in enrollment
                    and enrollment[key] is None
                )
                or enrollment.get(key) == value
                for key, value in DEVELOPMENT_SETTINGS.items()
            )
        )

    def _policy(
        self, context: OperationContext, enrollment_id: int
    ) -> tuple[str, str] | None:
        approval, revision, baseline, current = self.authority.baseline(
            self._status(context)
        )
        if approval is None:
            raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
        expected = desired(context.plan.project, baseline, enrollment_id)
        if entry(current, context.plan.project) != expected:
            return None
        try:
            info = self.config.deployed_policy_file.stat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid not in {0, os.geteuid()}
                or info.st_mode & 0o022
            ):
                raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
            artifact = loads(self.config.deployed_policy_file.read_bytes())
        except OSError:
            return None
        if (
            artifact.get("schema_version") != 1
            or artifact.get("exclusive") is not True
            or artifact.get("contract_digest") != self.config.expected_ci_digest
        ):
            return None
        if entry(artifact.get("catalog"), context.plan.project) != expected:
            return None
        return revision, string(artifact.get("package_digest"), r"sha256:[0-9a-f]{64}")

    def _configuration_matches(self, enrollment_id: int, pipeline_number: int) -> bool:
        configs = self.woodpecker.request(
            f"/api/repos/{enrollment_id}/pipelines/{pipeline_number}/config"
        )
        if not isinstance(configs, list):
            raise ProtocolError(Code.DEPENDENCY_UNAVAILABLE)
        for config in configs:
            if config.get("name") not in {WORKFLOW, WORKFLOW + ".yaml"}:
                continue
            try:
                raw = base64.b64decode(config["data"], validate=True)
                if len(raw) > 65536:
                    raise ProtocolError(Code.DEPENDENCY_UNAVAILABLE)
                workflow = yaml.safe_load(raw)
            except (ValueError, KeyError, yaml.YAMLError):
                raise ProtocolError(Code.DEPENDENCY_UNAVAILABLE) from None
            if (
                isinstance(workflow, dict)
                and workflow.get("environment", {}).get("PROJECTCTL_CONTRACT_DIGEST")
                == self.config.expected_ci_digest
            ):
                return True
        return False

    def _pipeline(
        self, context: OperationContext, enrollment_id: int
    ) -> dict[str, Any] | None:
        allocation = context.inputs.get(
            "pipeline_id", self.journal.allocation(context.request_id, "ci")
        )
        if allocation is not None:
            candidates = [
                self.woodpecker.request(
                    f"/api/repos/{enrollment_id}/pipelines/{allocation}"
                )
            ]
        else:
            candidates = self.woodpecker.pages(
                f"/api/repos/{enrollment_id}/pipelines?branch={segment(context.plan.project.default_branch)}"
            )
        # Readiness delivers push events, so preferring manual runs keeps the
        # discovered ci pipeline stable after that delivery.
        for pipeline in sorted(
            candidates,
            key=lambda row: (row.get("event") == "manual", number(row.get("number"))),
            reverse=True,
        ):
            if (
                pipeline.get("commit") != context.plan.project.source_sha
                or pipeline.get("branch") != context.plan.project.default_branch
                or pipeline.get("ref")
                != f"refs/heads/{context.plan.project.default_branch}"
                or pipeline.get("event") not in {"push", "manual"}
            ):
                if allocation is not None:
                    raise ProtocolError(Code.SOURCE_MOVED)
                continue
            detail = self.woodpecker.request(
                f"/api/repos/{enrollment_id}/pipelines/{pipeline['number']}"
            )
            if self._configuration_matches(enrollment_id, pipeline["number"]):
                # A failed run this request did not start may predate a fix,
                # so ci triggers its own instead of adopting it.
                if allocation is None and detail.get("status") in {
                    "failure",
                    "error",
                    "killed",
                }:
                    continue
                return detail
            if detail.get("status") in {"pending", "running", "blocked"}:
                raise ProtocolError(Code.VERIFICATION_PENDING)
        return None

    def _commit_status(self, repository: str, sha: str) -> str | None:
        rows = self.forgejo.pages(f"/api/v1/repos/{repository}/statuses/{sha}")
        selected = [row for row in rows if row.get("context") == CONTEXT]
        return (
            max(selected, key=lambda row: number(row.get("id")))["status"]
            if selected
            else None
        )

    def _protection(self, context: OperationContext) -> dict[str, Any] | None:
        project = context.plan.project
        rule = self.repository_admin_api.request(
            f"/api/v1/repos/{project.repository}/branch_protections/{segment(project.default_branch)}",
            missing=True,
        )
        if (
            rule is None
            or rule.get("branch_name") != project.default_branch
            or rule.get("enable_push") is not False
            or rule.get("enable_push_whitelist") is not False
            or rule.get("enable_status_check") is not True
            or rule.get("apply_to_admins") is not True
            or CONTEXT not in rule.get("status_check_contexts", [])
        ):
            return None
        return {key: rule[key] for key in PROTECTION_FIELDS}

    def _probe(
        self, context: OperationContext, enrollment_id: int, protection: dict[str, Any]
    ) -> bool:
        revision = self.authority.main_revision()
        raw = self.authority.contents(
            f"config/projectctl/probes/{context.request_id}.json",
            revision,
            missing=True,
        )
        if raw is None:
            return False
        payload = verify_probe(
            raw, self.config.trusted_keys, datetime.now(timezone.utc)
        )
        project = context.plan.project
        if any(
            payload[key] != value
            for key, value in {
                "request_id": context.request_id,
                "repository": project.repository,
                "branch": project.default_branch,
                "source_sha": project.source_sha,
                "protection_digest": digest(protection),
            }.items()
        ):
            raise ProtocolError(Code.PLAN_STALE)
        pr = self.forgejo.request(
            f"/api/v1/repos/{project.repository}/pulls/{payload['probe_pr']}"
        )
        failed = self.woodpecker.request(
            f"/api/repos/{enrollment_id}/pipelines/{payload['probe_pipeline']}"
        )
        return (
            pr.get("merged") is False
            and pr.get("state") == "closed"
            and pr.get("head", {}).get("sha") == payload["probe_sha"]
            and pr.get("base", {}).get("ref") == project.default_branch
            and failed.get("commit") == payload["probe_sha"]
            and failed.get("status") == "failure"
            and self._configuration_matches(enrollment_id, payload["probe_pipeline"])
            and self._commit_status(project.repository, payload["probe_sha"])
            in {"failure", "error"}
        )

    def inspect(self, context: OperationContext) -> Observation:
        project, operation = context.plan.project, context.operation.operation_id
        evidence: dict[str, Any] = {"observed_at": stamp(datetime.now(timezone.utc))}
        repo = self._repo(context)
        if repo is None:
            return Observation(
                "absent" if operation == "repository" else "pending", evidence
            )
        evidence.update(
            repository=project.repository,
            repository_id=repo["id"],
            branch=repo["default_branch"],
            visibility="private" if repo.get("private") else "public",
        )
        if operation == "repository":
            if (
                context.plan.discovery.repository_id is None
                and self.journal.allocation(context.request_id, "repository")
                != repo["id"]
            ):
                return Observation("conflict", evidence)
            ready = (
                repo.get("private") is (project.visibility == "private")
                and repo.get("default_branch") == project.default_branch
            )
            return Observation(
                "verified" if ready else "absent",
                evidence,
                {"repository_id": repo["id"]} if ready else {},
                self.journal.allocation(context.request_id, "repository") == repo["id"],
            )
        if operation == "clone":
            permission = self.repository_admin_api.request(
                f"/api/v1/repos/{project.repository}/collaborators/{segment(self.config.clone_identity)}/permission",
                missing=True,
            )
            ready = permission is not None and permission.get("permission") == "read"
            evidence["clone_read_only"] = ready
            return Observation("verified" if ready else "absent", evidence)
        if operation == "source":
            branch = self.forgejo.request(
                f"/api/v1/repos/{project.repository}/branches/{segment(project.default_branch)}",
                missing=True,
            )
            if branch is None:
                return Observation("pending", evidence)
            sha = string(branch["commit"]["id"], SHA)
            evidence["source_sha"] = sha
            return Observation("verified", evidence, {"source_sha": sha})
        enrollment = self._enrollment(context, repo)
        if enrollment is None:
            return Observation(
                "absent" if operation == "enrollment" else "pending", evidence
            )
        enrollment_id = enrollment["id"]
        evidence["enrollment_id"] = enrollment_id
        if operation == "enrollment":
            ready = self._enrollment_ready(enrollment, project.visibility == "private")
            return Observation(
                "verified" if ready else "absent",
                evidence,
                {"enrollment_id": enrollment_id} if ready else {},
            )
        if operation == "pull":
            registry = self.registry_api.request(
                f"/api/repos/{enrollment_id}/registries/{segment(self.config.registry_address)}",
                missing=True,
            )
            ready = (
                registry is not None
                and registry.get("address") == self.config.registry_address
                and registry.get("username") == self.config.registry_username
            )
            evidence["pull_read_only"] = ready
            if registry is not None and registry.get("username") not in {
                "node",
                self.config.registry_username,
            }:
                return Observation("conflict", evidence)
            return Observation("verified" if ready else "absent", evidence)
        if operation == "catalog":
            approval, revision, baseline, catalog = self.authority.baseline(
                self._status(context)
            )
            if approval is None:
                raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
            path = (
                self.config.state_directory
                / "artifacts"
                / (context.request_id + ".json")
            )
            if path.exists():
                previous = loads(path.read_bytes())
                revision = string(previous.get("base_revision"), SHA)
                catalog = self.authority.catalog(revision)
            result = patch(catalog, project, baseline, enrollment_id)
            artifact = {
                "schema_version": 1,
                "request_id": context.request_id,
                "base_revision": revision,
                "path": "config/ci/projects.json",
                "catalog": result,
            }
            self._save_artifact(context.request_id, artifact)
            return Observation(
                "verified", evidence, {"artifact_digest": digest(artifact)}
            )
        deployed = self._policy(context, enrollment_id)
        if deployed is None:
            return Observation("pending", evidence)
        evidence.update(
            policy_digest=context.plan.policy_digest,
            enrollment_revision=deployed[0],
            deployed_package_digest=deployed[1],
        )
        if operation == "policy":
            return Observation(
                "verified", evidence, {"policy_digest": context.plan.policy_digest}
            )
        if operation == "protection":
            protected = self._protection(context) is not None
            evidence["branch_protection"] = protected
            return Observation("verified" if protected else "absent", evidence)
        pipeline = self._pipeline(context, enrollment_id)
        if pipeline is None:
            return Observation("absent" if operation == "ci" else "pending", evidence)
        evidence.update(
            source_sha=pipeline["commit"],
            pipeline_id=pipeline["number"],
            event=pipeline["event"],
            context=CONTEXT,
            workflow=WORKFLOW,
            ci_status=pipeline["status"]
            if pipeline["status"] in {"success", "failure", "error", "killed"}
            else "pending",
        )
        if operation == "ci":
            return Observation(
                "verified", evidence, {"pipeline_id": pipeline["number"]}
            )
        if evidence["ci_status"] in {"failure", "error", "killed"}:
            raise ProtocolError(Code.CI_FAILED, operation)
        if (
            not self.validation_passed(pipeline, project.ci_contract)
            or self._commit_status(project.repository, project.source_sha) != "success"
        ):
            return Observation("pending", evidence)
        protection = self._protection(context)
        webhook = self._push_delivered(enrollment_id, project)
        evidence.update(
            webhook_delivered=webhook,
            branch_protection=protection is not None,
            protection_probe=self._probe(context, enrollment_id, protection)
            if protection
            and not self.config.policy(
                context.plan.principal, project
            ).routine_onboarding
            else False,
        )
        if protection is not None and not webhook:
            return Observation("absent", evidence)
        return Observation("verified", evidence)

    def _push_delivered(self, enrollment_id: int, project: Project) -> bool:
        pushes = self.woodpecker.pages(
            f"/api/repos/{enrollment_id}/pipelines?branch={segment(project.default_branch)}&event=push"
        )
        return any(
            row.get("commit") == project.source_sha
            and row.get("event") == "push"
            and row.get("ref") == f"refs/heads/{project.default_branch}"
            and self._configuration_matches(enrollment_id, number(row["number"]))
            for row in pushes
        )

    def _push_hook(self, project: Project, repository_id: int) -> int:
        hooks = self.repository_admin_api.request(
            f"/api/v1/repos/{project.repository}/hooks"
        )
        if not isinstance(hooks, list):
            raise ProtocolError(Code.DEPENDENCY_UNAVAILABLE)
        matches = []
        for hook in hooks:
            config = hook.get("config") if isinstance(hook, dict) else None
            if (
                not isinstance(config, dict)
                or hook.get("active") is not True
                or "push" not in hook.get("events", [])
            ):
                continue
            url = urllib.parse.urlsplit(str(config.get("url", "")))
            token = urllib.parse.parse_qs(url.query).get("access_token", [""])[0]
            try:
                claims = json.loads(
                    base64.urlsafe_b64decode(token.split(".")[1] + "==")
                )
            except (IndexError, ValueError):
                continue
            if (
                url.path == "/api/hook"
                and isinstance(claims, dict)
                and claims.get("type") == "hook"
                and claims.get("repo-forge-remote-id") == str(repository_id)
            ):
                matches.append(number(hook.get("id")))
        if len(matches) != 1:
            raise ProtocolError(Code.RESOURCE_CONFLICT)
        return matches[0]

    def _save_artifact(self, request_id: str, artifact: dict[str, Any]) -> None:
        directory = self.config.state_directory / "artifacts"
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / (request_id + ".json")
        encoded = canonical(artifact)
        if path.exists():
            if path.read_bytes() != encoded:
                raise ProtocolError(Code.PLAN_STALE)
            return
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())

    def mutate(self, context: OperationContext) -> None:
        project, operation = context.plan.project, context.operation.operation_id
        repo = self._repo(context)
        if operation == "repository":
            if repo is None:
                owner, name = project.repository.split("/")
                organization = self.forgejo.request(
                    f"/api/v1/orgs/{segment(owner)}", missing=True
                )
                if organization is not None:
                    endpoint = f"/api/v1/orgs/{segment(owner)}/repos"
                elif self.forgejo.request("/api/v1/user").get("login") == owner:
                    endpoint = "/api/v1/user/repos"
                else:
                    raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
                result = self._write(
                    context,
                    "forgejo",
                    endpoint,
                    method="POST",
                    body={
                        "name": name,
                        "private": project.visibility == "private",
                        "default_branch": project.default_branch,
                        "auto_init": False,
                        "object_format_name": project.object_format,
                    },
                )
                self.journal.allocate(
                    context.request_id, operation, context.attempt, number(result["id"])
                )
            else:
                if context.plan.discovery.repository_id != repo["id"]:
                    raise ProtocolError(Code.RESOURCE_CONFLICT)
                self._write(
                    context,
                    "forgejo",
                    f"/api/v1/repos/{project.repository}",
                    method="PATCH",
                    body={
                        "private": project.visibility == "private",
                        "default_branch": project.default_branch,
                    },
                )
            return
        if repo is None:
            raise ProtocolError(Code.PLAN_STALE)
        if operation == "clone":
            self._write(
                context,
                "forgejo",
                f"/api/v1/repos/{project.repository}/collaborators/{segment(self.config.clone_identity)}",
                method="PUT",
                body={"permission": "read"},
            )
            return
        enrollment = self._enrollment(context, repo)
        if operation == "enrollment":
            if enrollment is None or not enrollment.get("active"):
                enrollment = self._write(
                    context,
                    "woodpecker",
                    f"/api/repos?forge_remote_id={repo['id']}",
                    method="POST",
                )
                self.journal.allocate(
                    context.request_id,
                    operation,
                    context.attempt,
                    number(enrollment["id"]),
                )
            self._write(
                context,
                "woodpecker",
                f"/api/repos/{enrollment['id']}",
                method="PATCH",
                body=DEVELOPMENT_SETTINGS,
            )
            self._write(
                context,
                "woodpecker",
                f"/api/repos/{enrollment['id']}/repair",
                method="POST",
            )
            return
        if enrollment is None:
            raise ProtocolError(Code.PLAN_STALE)
        if operation == "pull":
            path = f"/api/repos/{enrollment['id']}/registries"
            existing = self.registry_api.request(
                path + "/" + segment(self.config.registry_address), missing=True
            )
            if existing is not None and existing.get("username") not in {
                "node",
                self.config.registry_username,
            }:
                raise ProtocolError(Code.RESOURCE_CONFLICT)
            if (
                existing is None
                or existing.get("username") != self.config.registry_username
            ):
                self._write(
                    context,
                    "woodpecker",
                    path
                    if existing is None
                    else path + "/" + segment(self.config.registry_address),
                    method="POST" if existing is None else "PATCH",
                    body={
                        "address": self.config.registry_address,
                        "username": self.config.registry_username,
                        "password": self.config.secret("registry-password", write=True),
                    },
                )
            return
        if operation == "ci":
            branch = self.forgejo.request(
                f"/api/v1/repos/{project.repository}/branches/{segment(project.default_branch)}"
            )
            if branch["commit"]["id"] != project.source_sha:
                raise ProtocolError(Code.SOURCE_MOVED)
            result = self._write(
                context,
                "woodpecker",
                f"/api/repos/{enrollment['id']}/pipelines",
                method="POST",
                body={"branch": project.default_branch},
            )
            self.journal.allocate(
                context.request_id, operation, context.attempt, number(result["number"])
            )
            return
        if operation == "readiness":
            branch = self.forgejo.request(
                f"/api/v1/repos/{project.repository}/branches/{segment(project.default_branch)}"
            )
            if branch["commit"]["id"] != project.source_sha:
                raise ProtocolError(Code.SOURCE_MOVED)
            hook = self._push_hook(project, repo["id"])
            self._write(
                context,
                "forgejo",
                f"/api/v1/repos/{project.repository}/hooks/{hook}/tests?ref="
                + segment(f"refs/heads/{project.default_branch}"),
                method="POST",
            )
            deadline = time.monotonic() + self.delivery_timeout
            while not self._push_delivered(enrollment["id"], project):
                if time.monotonic() >= deadline:
                    return
                time.sleep(1)
            return
        if operation == "protection":
            path = f"/api/v1/repos/{project.repository}/branch_protections"
            existing = self.repository_admin_api.request(
                path + "/" + segment(project.default_branch), missing=True
            )
            body = {
                "enable_push": False,
                "enable_push_whitelist": False,
                "enable_status_check": True,
                "apply_to_admins": True,
                "status_check_contexts": sorted(
                    set((existing or {}).get("status_check_contexts", [])) | {CONTEXT}
                ),
            }
            if existing is None:
                body["branch_name"] = project.default_branch
            self._write(
                context,
                "forgejo",
                path
                if existing is None
                else path + "/" + segment(project.default_branch),
                method="POST" if existing is None else "PATCH",
                body=body,
            )
            return
        raise ProtocolError(Code.INVALID_SPEC)
