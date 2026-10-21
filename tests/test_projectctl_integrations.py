from __future__ import annotations

import base64
import contextlib
import copy
import io
import json
import os
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.ci.development_projects import contract_digest
from scripts.projectctl.__main__ import SshClient
from scripts.projectctl.__main__ import main as cli_main
from scripts.projectctl.catalog import (
    desired,
    entry,
    verify_transition,
)
from scripts.projectctl.catalog import (
    patch as catalog_patch,
)
from scripts.projectctl.codec import canonical, digest, document, loads
from scripts.projectctl.config import Configuration, read_secret
from scripts.projectctl.errors import Code, ProtocolError, success
from scripts.projectctl.http import Api
from scripts.projectctl.journal import Journal
from scripts.projectctl.models import CAPABILITIES, Project, stamp
from scripts.projectctl.operator import main as operator_main
from scripts.projectctl.proofs import PROBE_DOMAIN, PROTECTION_FIELDS
from scripts.projectctl.provision import (
    collect,
    credentials,
    install,
    verify_identities,
)
from scripts.projectctl.runtime import Runtime
from scripts.projectctl.services import DEVELOPMENT_SETTINGS
from scripts.projectctl.worker import CONTEXT, WORKFLOW

ROOT = Path(__file__).resolve().parents[1]


class CredentialFileTests(unittest.TestCase):
    def test_systemd_acl_is_accepted_only_for_loaded_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "forgejo-write"
            path.write_text("scoped-test-token")
            path.chmod(0o440)
            info = SimpleNamespace(st_mode=stat.S_IFREG | 0o440, st_uid=0, st_nlink=1)
            entries = [
                (1, 4, 0xFFFFFFFF),
                (2, 4, os.geteuid()),
                (4, 0, 0xFFFFFFFF),
                (16, 4, 0xFFFFFFFF),
                (32, 0, 0xFFFFFFFF),
            ]

            def acl(rows: list[tuple[int, int, int]]) -> bytes:
                return struct.pack("<I", 2) + b"".join(
                    struct.pack("<HHI", *row) for row in rows
                )

            with (
                patch("scripts.projectctl.config.os.fstat", return_value=info),
                patch(
                    "scripts.projectctl.config.os.fstatvfs",
                    return_value=SimpleNamespace(f_flag=os.ST_RDONLY),
                ) as filesystem,
                patch(
                    "scripts.projectctl.config.os.getxattr", return_value=acl(entries)
                ) as attributes,
            ):
                self.assertEqual(
                    read_secret(path, systemd_credential=True), "scoped-test-token"
                )
                with self.assertRaises(ProtocolError):
                    read_secret(path)
                invalid = [b"", acl(entries + [(2, 4, os.geteuid() + 1)])]
                for index, replacement in (
                    (0, (1, 6, 0xFFFFFFFF)),
                    (1, (2, 4, os.geteuid() + 1)),
                    (2, (4, 4, 0xFFFFFFFF)),
                    (3, (16, 6, 0xFFFFFFFF)),
                    (4, (32, 4, 0xFFFFFFFF)),
                ):
                    changed = entries.copy()
                    changed[index] = replacement
                    invalid.append(acl(changed))
                for value in invalid:
                    attributes.return_value = value
                    with self.subTest(acl=value), self.assertRaises(ProtocolError):
                        read_secret(path, systemd_credential=True)
                attributes.return_value = acl(entries)
                filesystem.return_value = SimpleNamespace(f_flag=0)
                with self.assertRaises(ProtocolError):
                    read_secret(path, systemd_credential=True)
                filesystem.return_value = SimpleNamespace(f_flag=os.ST_RDONLY)
                attributes.side_effect = OSError("ACL unavailable")
                with self.assertRaises(ProtocolError):
                    read_secret(path, systemd_credential=True)

    def test_credentials_reject_links_and_group_readable_plain_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secret"
            path.write_text("scoped-test-token")
            path.chmod(0o600)
            for loaded in (False, True):
                self.assertEqual(
                    read_secret(path, systemd_credential=loaded), "scoped-test-token"
                )
            path.chmod(0o640)
            for loaded in (False, True):
                with self.subTest(loaded=loaded), self.assertRaises(ProtocolError):
                    read_secret(path, systemd_credential=loaded)
            path.chmod(0o600)
            alias = path.with_name("alias")
            alias.symlink_to(path)
            for loaded in (False, True):
                with self.subTest(loaded=loaded), self.assertRaises(ProtocolError):
                    read_secret(alias, systemd_credential=loaded)
            os.link(path, path.with_name("hardlink"))
            for loaded in (False, True):
                with self.subTest(loaded=loaded), self.assertRaises(ProtocolError):
                    read_secret(path, systemd_credential=loaded)


class ApiState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.revision = "b" * 40
        self.catalogs: dict[str, dict[str, Any]] = {
            self.revision: {"schema_version": 1, "projects": []}
        }
        self.files: dict[str, Any] = {}
        self.repository: dict[str, Any] | None = None
        self.enrollment: dict[str, Any] | None = None
        self.clone_permission: str | None = None
        self.registry: dict[str, Any] | None = None
        self.source_sha: str | None = None
        self.protection: dict[str, Any] | None = None
        self.pipelines: dict[int, dict[str, Any]] = {}
        self.configs: dict[int, list[dict[str, Any]]] = {}
        self.statuses: dict[str, list[dict[str, Any]]] = {}
        self.writes: list[tuple[str, str, dict[str, Any]]] = []
        self.administrator_roles: set[str] = set()
        self.hooks: list[dict[str, Any]] = [self.hook(5, "41")]
        self.config_unavailable = 0
        self.delivery_config_delay = 0
        self.step = "python-uv-test"

    @staticmethod
    def hook(identifier: int, remote_id: str) -> dict[str, Any]:
        claims = {"forge-id": "1", "repo-forge-remote-id": remote_id, "type": "hook"}
        token = ".".join(
            base64.urlsafe_b64encode(json.dumps(part).encode()).decode().rstrip("=")
            for part in ({"alg": "HS256", "typ": "JWT"}, claims)
        )
        return {
            "id": identifier,
            "type": "gitea",
            "active": True,
            "events": ["push", "pull_request"],
            "config": {
                "url": f"http://ci.example:8000/api/hook?access_token={token}.sig"
            },
        }

    def pipeline(
        self, number: int, sha: str, event: str = "push", status: str = "success"
    ) -> None:
        self.pipelines[number] = {
            "number": number,
            "id": number + 1000,
            "commit": sha,
            "branch": "main",
            "ref": "refs/heads/main",
            "event": event,
            "status": status,
            "workflows": [
                {
                    "name": WORKFLOW,
                    "state": status,
                    "children": [{"name": self.step, "state": status}],
                }
            ],
        }
        workflow = {"environment": {"PROJECTCTL_CONTRACT_DIGEST": contract_digest()}}
        self.configs[number] = [
            {
                "name": WORKFLOW,
                "data": base64.b64encode(yaml.safe_dump(workflow).encode()).decode(),
            }
        ]
        self.statuses[sha] = [{"id": number, "context": CONTEXT, "status": status}]

    def publish_catalog(self, catalog: dict[str, Any], revision: str) -> None:
        self.catalogs[revision] = copy.deepcopy(catalog)
        self.revision = revision

    def route(
        self, method: str, path: str, query: dict[str, list[str]], body: dict[str, Any]
    ) -> tuple[int, Any]:
        if method != "GET":
            self.writes.append((method, path, copy.deepcopy(body)))
        home = "/api/v1/repos/JEFF7712/homelab"
        project = "/api/v1/repos/JEFF7712/probe-py"
        if path == home + "/branches/main":
            return 200, {"commit": {"id": self.revision}}
        if path.startswith(home + "/contents/"):
            name = path.removeprefix(home + "/contents/")
            revision = query.get("ref", [self.revision])[0]
            value = (
                self.catalogs.get(revision)
                if name == "config/ci/projects.json"
                else self.files.get(name)
            )
            if value is None:
                return 404, {}
            return 200, {
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(canonical(value)).decode(),
            }
        if path == "/api/v1/orgs/JEFF7712":
            return 200, {"username": "JEFF7712"}
        if path == "/api/v1/orgs/JEFF7712/repos" and method == "POST":
            if self.repository is not None:
                return 409, {}
            self.repository = {
                "id": 41,
                "full_name": "JEFF7712/" + body["name"],
                "private": body["private"],
                "default_branch": body["default_branch"],
            }
            return 201, self.repository
        if path == project:
            if self.repository is None:
                return 404, {}
            if method == "PATCH":
                self.repository.update(body)
            return 200, self.repository
        if path.endswith("/collaborators/clone-reader") and method == "PUT":
            self.clone_permission = body["permission"]
            return 204, None
        if path.endswith("/collaborators/clone-reader/permission"):
            return (
                (200, {"permission": self.clone_permission})
                if self.clone_permission
                else (404, {})
            )
        if path == project + "/branches/main":
            return (
                (200, {"commit": {"id": self.source_sha}})
                if self.source_sha
                else (404, {})
            )
        if path == "/api/repos/lookup/JEFF7712/probe-py":
            return (200, self.enrollment) if self.enrollment else (404, {})
        if path == "/api/repos" and method == "POST":
            self.enrollment = {
                "id": 73,
                "full_name": "JEFF7712/probe-py",
                "forge_remote_id": "41",
                "private": True,
                "visibility": "private",
                "active": True,
                **copy.deepcopy(DEVELOPMENT_SETTINGS),
            }
            return 200, self.enrollment
        if path == "/api/repos/73" and method == "PATCH":
            assert self.enrollment is not None
            self.enrollment.update(body)
            return 200, self.enrollment
        if path == "/api/repos/73/repair" and method == "POST":
            return 204, None
        if path == "/api/repos/73/registries" and method == "POST":
            self.registry = body
            return 200, {key: value for key, value in body.items() if key != "password"}
        if path == "/api/repos/73/registries/registry.internal":
            if method == "PATCH" and self.registry is not None:
                self.registry.update(body)
            return (
                (
                    200,
                    {
                        key: value
                        for key, value in self.registry.items()
                        if key != "password"
                    },
                )
                if self.registry
                else (404, {})
            )
        if path == "/api/repos/73/pipelines":
            if method == "POST":
                assert self.source_sha is not None
                number = max(self.pipelines, default=0) + 1
                self.pipeline(number, self.source_sha, "manual")
                return 200, self.pipelines[number]
            values = list(self.pipelines.values())
            if "event" in query:
                values = [row for row in values if row["event"] == query["event"][0]]
            return 200, sorted(values, key=lambda row: row["number"], reverse=True)
        if path.startswith("/api/repos/73/pipelines/"):
            suffix = path.removeprefix("/api/repos/73/pipelines/")
            if suffix.endswith("/config"):
                number = int(suffix.removesuffix("/config"))
                if number == max(self.pipelines) and self.config_unavailable:
                    self.config_unavailable -= 1
                    return 404, {}
                return 200, self.configs.get(number, [])
            return (
                (200, self.pipelines[int(suffix)])
                if int(suffix) in self.pipelines
                else (404, {})
            )
        if path == project + "/hooks":
            return 200, self.hooks
        if path.startswith(project + "/hooks/") and path.endswith("/tests"):
            identifier = int(path.split("/")[-2])
            if method != "POST" or identifier not in {
                hook["id"] for hook in self.hooks
            }:
                return 404, {}
            if self.source_sha and query.get("ref") == ["refs/heads/main"]:
                self.pipeline(max(self.pipelines, default=0) + 1, self.source_sha)
                self.config_unavailable = self.delivery_config_delay
            return 204, None
        if path == project + "/branch_protections" and method == "POST":
            self.protection = body
            return 201, self.protection
        if path == project + "/branch_protections/main":
            if method == "PATCH" and self.protection is not None:
                self.protection.update(body)
            return (200, self.protection) if self.protection else (404, {})
        if path.startswith(project + "/statuses/"):
            return 200, self.statuses.get(path.removeprefix(project + "/statuses/"), [])
        if path == project + "/pulls/2":
            return 200, {
                "merged": False,
                "state": "closed",
                "head": {"sha": "f" * 40},
                "base": {"ref": "main"},
            }
        return 404, {}


class Handler(BaseHTTPRequestHandler):
    state: ApiState

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        self.respond("GET")

    def do_POST(self) -> None:
        self.respond("POST")

    def do_PATCH(self) -> None:
        self.respond("PATCH")

    def do_PUT(self) -> None:
        self.respond("PUT")

    def respond(self, method: str) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        body = json.loads(
            self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}"
        )
        service = "forgejo" if path.startswith("/api/v1/") else "woodpecker"
        prefix = "token" if service == "forgejo" else "Bearer"
        allowed = {f"{prefix} fixture-{service}-write"}
        if (
            method == "GET"
            and "/registries/" not in path
            and not path.endswith("/registries")
            and "/branch_protections/" not in path
            and "/collaborators/" not in path
        ):
            allowed.add(f"{prefix} fixture-{service}-read")
        if self.headers.get("Authorization") not in allowed:
            code, data = 403, {"secret": "never return this backend error"}
        elif path in {"/api/v1/user", "/api/user"}:
            credential = (
                "write"
                if self.headers.get("Authorization", "").endswith("-write")
                else "read"
            )
            administrator = credential in self.state.administrator_roles
            code, data = 200, {"login": "projectctl-" + credential}
            if service == "forgejo":
                data["is_admin"] = administrator
            elif administrator:
                data["admin"] = True
        else:
            with self.state.lock:
                code, data = self.state.route(
                    method, path, urllib.parse.parse_qs(parsed.query), body
                )
        raw = json.dumps(data).encode() if data is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class LocalClient:
    def __init__(self, runtime: Runtime):
        self.runtime = runtime

    def send(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.handle(
            "agent-test", {"schema_version": 1, "action": action, "payload": payload}
        )


class IntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = ApiState()
        handler = type("TestHandler", (Handler,), {"state": self.state})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
        self.key_path = self.root / "operator.pem"
        self.key_path.write_bytes(
            self.key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        self.key_path.chmod(0o600)
        credentials = self.root / "credentials"
        credentials.mkdir(mode=0o700)
        self.credentials = credentials
        secret_files = {}
        for name in (
            "forgejo-read",
            "forgejo-write",
            "woodpecker-read",
            "woodpecker-write",
            "registry-password",
        ):
            path = credentials / name
            path.write_text("fixture-" + name)
            path.chmod(0o600)
            secret_files[name] = str(path)
        url = f"http://127.0.0.1:{self.server.server_port}"
        self.config_document = {
            "schema_version": 1,
            "platform": {
                "forgejo": "forgejo-local",
                "woodpecker": "woodpecker-local",
                "homelab_repository": "JEFF7712/homelab",
            },
            "endpoints": {
                name: {"url": url, "allow_plaintext": False}
                for name in ("forgejo", "woodpecker")
            },
            "scopes": [
                {
                    "principal": "agent-test",
                    "project_key": "probe-py",
                    "repository": "JEFF7712/probe-py",
                    "visibility": "private",
                    "routine": None,
                }
            ],
            "trusted_keys": {
                "test-operator": base64.b64encode(
                    self.key.public_key().public_bytes(
                        serialization.Encoding.Raw, serialization.PublicFormat.Raw
                    )
                ).decode()
            },
            "secret_files": secret_files,
            "state_directory": str(self.root / "worker"),
            "clone_identity": "clone-reader",
            "registry": {
                "address": "registry.internal",
                "username": "node-read",
                "actions": ["read"],
            },
            "deployed_policy_file": str(self.root / "deployed-policy.json"),
            "expected_ci_digest": contract_digest(),
        }
        self.config_path = self.root / "config.json"
        self.config_path.write_bytes(canonical(self.config_document))
        self.runtime = Runtime(Configuration(self.config_document))
        self.client = LocalClient(self.runtime)
        self.project = Project(
            1,
            "probe-py",
            "JEFF7712/probe-py",
            "private",
            "main",
            "sha1",
            "a" * 40,
            "python-uv-v1",
            CAPABILITIES,
        )

    def call(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.client.send(action, payload)

    def submit(self) -> dict[str, Any]:
        candidate = self.call("plan", document(self.project))
        self.assertEqual(self.state.writes, [])
        return self.call("request", {**candidate, "idempotency_key": "onboard-1"})

    def approve(self, status: dict[str, Any]) -> None:
        context = self.call("approval-context", {"request_id": status["request_id"]})
        export = self.root / "request.json"
        export.write_bytes(canonical(success(status)))
        context_file = self.root / "context.json"
        context_file.write_bytes(canonical(success(context)))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = operator_main(
                [
                    "approve",
                    str(export),
                    "--context",
                    str(context_file),
                    "--key-file",
                    str(self.key_path),
                    "--key-id",
                    "test-operator",
                ]
            )
        self.assertEqual(code, 0, output.getvalue())
        approval = loads(output.getvalue())["data"]
        self.state.files[f"config/projectctl/approvals/{status['request_id']}.json"] = (
            approval
        )
        self.state.publish_catalog(self.state.catalogs[self.state.revision], "c" * 40)

    def dispatch(self, request_id: str) -> dict[str, Any]:
        with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(self.credentials)}):
            return self.runtime.worker.run(request_id, "agent-test")

    def enable_routine(self) -> None:
        scope = self.config_document["scopes"][0]
        scope["routine"] = {
            "repository_id": 41,
            "default_branch": "main",
            "ci_contract": self.project.ci_contract,
        }
        self.state.files["config/projectctl/callers.json"] = {
            "agent-test": {
                "projects": [
                    {key: value for key, value in scope.items() if key != "principal"}
                ]
            }
        }
        self.state.repository = {
            "id": 41,
            "full_name": self.project.repository,
            "private": True,
            "default_branch": "main",
        }
        self.state.source_sha = self.project.source_sha
        self.runtime = Runtime(Configuration(self.config_document))
        self.client = LocalClient(self.runtime)

    def configure_protection(self) -> None:
        self.state.protection = {
            "branch_name": "main",
            "enable_push": False,
            "enable_push_whitelist": False,
            "enable_status_check": True,
            "status_check_contexts": [CONTEXT],
            "apply_to_admins": True,
        }

    def test_routine_onboarding_reconciles_without_signature_or_dispatch(self) -> None:
        self.enable_routine()
        status = self.submit()
        self.assertEqual(status["state"], "queued")
        request_id = status["request_id"]
        with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(self.credentials)}):
            first = self.runtime.reconcile()
            self.assertEqual(first["requests"][0]["state"], "waiting", first)
            self.deploy_catalog(request_id)
            self.state.pipeline(10, self.project.source_sha)
            final = self.runtime.reconcile()
        self.assertEqual(final["requests"][0]["state"], "succeeded", final)
        self.assertIsNotNone(self.state.protection)
        self.assertTrue(self.state.protection["apply_to_admins"])
        self.assertFalse(
            any("approvals/" in name or "probes/" in name for name in self.state.files)
        )
        with self.runtime.journal.connection() as db:
            grants = [loads(row[0]) for row in db.execute("SELECT grant FROM attempts")]
        self.assertTrue(grants)
        self.assertTrue(
            all(grant["grant_id"] == "routine-onboarding" for grant in grants)
        )
        self.assertFalse(
            any(
                path.startswith("/api/v1/") and path.endswith("/repos")
                for _, path, _ in self.state.writes
            )
        )

    def test_routine_onboards_a_node_contract(self) -> None:
        from dataclasses import replace

        self.project = replace(self.project, ci_contract="node-build-v1")
        self.state.step = "node-build"
        self.enable_routine()
        status = self.submit()
        request_id = status["request_id"]
        with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(self.credentials)}):
            self.runtime.reconcile()
            self.deploy_catalog(request_id)
            self.state.pipeline(10, self.project.source_sha)
            final = self.runtime.reconcile()
        self.assertEqual(final["requests"][0]["state"], "succeeded", final)
        artifact = loads(
            (
                self.runtime.config.state_directory
                / "artifacts"
                / (request_id + ".json")
            ).read_bytes()
        )
        self.assertEqual(
            artifact["catalog"]["projects"][0]["ci"],
            {"kind": "node-build-v1", "tier": "sandbox"},
        )

    def test_routine_validation_requires_the_contract_step(self) -> None:
        self.enable_routine()
        self.state.step = "node-build"
        status = self.submit()
        request_id = status["request_id"]
        self.dispatch(request_id)
        self.deploy_catalog(request_id)
        self.state.pipeline(10, self.project.source_sha)
        self.assertNotEqual(self.dispatch(request_id)["state"], "succeeded")

    def test_routine_refuses_to_demote_a_deployed_application(self) -> None:
        self.enable_routine()
        catalog = self.state.catalogs[self.state.revision]
        catalog["projects"].append(
            {
                "name": self.project.project_key,
                "forgejo_repo": self.project.repository,
                "ci": {"kind": "node-service-v1", "tier": "deploy"},
            }
        )
        with self.assertRaises(ProtocolError) as error:
            desired(self.project, catalog["projects"][-1], 73)
        self.assertEqual(error.exception.code, Code.RESOURCE_CONFLICT)
        status = self.submit()
        result = self.dispatch(status["request_id"])
        self.assertNotEqual(result["state"], "succeeded", result)
        self.assertFalse(
            (
                self.runtime.config.state_directory
                / "artifacts"
                / (status["request_id"] + ".json")
            ).exists()
        )

    def test_routine_revocation_stops_second_write_in_enrollment(self) -> None:
        self.enable_routine()
        status = self.submit()
        original = self.runtime.config.apis["woodpecker"].request

        def revoke(path: str, **kwargs: Any) -> Any:
            result = original(path, **kwargs)
            if path.startswith("/api/repos?forge_remote_id="):
                self.state.files["config/projectctl/callers.json"] = {}
            return result

        with patch.object(
            self.runtime.config.apis["woodpecker"], "request", side_effect=revoke
        ):
            result = self.dispatch(status["request_id"])
        self.assertEqual(result["code"], "OUTCOME_UNCERTAIN", result)
        with self.assertRaises(ProtocolError) as error:
            self.runtime.authority.current(status["request_id"])
        self.assertEqual(error.exception.code, Code.AUTHORIZATION_REVOKED)
        self.assertFalse(
            any(
                method == "PATCH" and path == "/api/repos/73"
                for method, path, _ in self.state.writes
            )
        )

    def test_routine_migrates_legacy_node_registry(
        self,
    ) -> None:
        self.enable_routine()
        self.runtime.config.registry_username = "dedicated-reader"
        self.state.registry = {"address": "registry.internal", "username": "node"}
        status = self.submit()
        first = self.dispatch(status["request_id"])
        self.assertEqual(first["state"], "waiting", first)
        self.assertEqual(self.state.registry["username"], "dedicated-reader")
        self.assertEqual(
            len(
                [
                    row
                    for row in self.state.writes
                    if row[0] == "PATCH" and "/registries/" in row[1]
                ]
            ),
            1,
        )

    def test_null_trusted_plugin_list_does_not_cause_reenrollment(self) -> None:
        self.enable_routine()
        self.state.enrollment = {
            "id": 73,
            "full_name": self.project.repository,
            "forge_remote_id": "41",
            "private": True,
            "visibility": "private",
            "active": True,
            **copy.deepcopy(DEVELOPMENT_SETTINGS),
            "netrc_trusted": None,
        }
        status = self.submit()
        result = self.dispatch(status["request_id"])
        self.assertEqual(result["state"], "waiting", result)
        self.assertFalse(
            any(
                path == "/api/repos/73" or path.endswith("/repair")
                for _, path, _ in self.state.writes
            )
        )
        self.state.enrollment["netrc_trusted"] = ["unapproved-plugin"]
        self.assertFalse(
            self.runtime.adapter._enrollment_ready(self.state.enrollment, True)
        )

    def test_routine_does_not_replace_an_unrelated_registry_identity(self) -> None:
        self.enable_routine()
        self.state.registry = {
            "address": "registry.internal",
            "username": "another-project-account",
        }
        status = self.submit()
        result = self.dispatch(status["request_id"])
        self.assertEqual(result["code"], "RESOURCE_CONFLICT", result)
        self.assertEqual(self.state.registry["username"], "another-project-account")
        self.assertFalse(any("/registries" in path for _, path, _ in self.state.writes))

    def test_routine_scope_cannot_expand_or_replace_repository(self) -> None:
        from dataclasses import replace

        self.enable_routine()
        for project in (
            replace(self.project, default_branch="other"),
            replace(self.project, repository="JEFF7712/other"),
            replace(self.project, visibility="public"),
        ):
            with self.subTest(project=project), self.assertRaises(ProtocolError):
                self.call("plan", document(project))
        self.state.repository["id"] = 42
        with self.assertRaises(ProtocolError) as error:
            self.call("plan", document(self.project))
        self.assertEqual(error.exception.code, Code.RESOURCE_CONFLICT)
        self.state.repository = None
        with self.assertRaises(ProtocolError):
            self.call("plan", document(self.project))
        self.assertEqual(self.state.writes, [])

    def test_routine_context_is_immutable_and_catalog_drift_blocks_resume(self) -> None:
        self.enable_routine()
        status = self.submit()
        request_id = status["request_id"]
        original = self.call("approval-context", {"request_id": request_id})
        self.dispatch(request_id)
        self.deploy_catalog(request_id)
        self.assertEqual(
            self.call("approval-context", {"request_id": request_id}), original
        )
        self.state.catalogs[self.state.revision]["projects"][0]["ci"]["tier"] = (
            "privileged"
        )
        count = len(self.state.writes)
        result = self.dispatch(request_id)
        self.assertEqual(result["code"], "PLAN_STALE", result)
        self.assertEqual(len(self.state.writes), count)

    def hook_tests(self) -> list[tuple[str, str, dict[str, Any]]]:
        return [row for row in self.state.writes if row[1].endswith("/tests")]

    def test_routine_readiness_delivers_the_push_event_itself(self) -> None:
        self.enable_routine()
        status = self.submit()
        request_id = status["request_id"]
        self.dispatch(request_id)
        self.deploy_catalog(request_id)
        self.state.pipeline(10, self.project.source_sha, event="manual")
        self.configure_protection()
        result = self.dispatch(request_id)
        self.assertEqual(result["state"], "succeeded", result)
        self.assertEqual(len(self.hook_tests()), 1)
        self.assertEqual(self.state.pipelines[11]["event"], "push")
        count = len(self.state.writes)
        self.assertEqual(self.dispatch(request_id)["state"], "succeeded")
        self.assertEqual(len(self.state.writes), count)

    def test_routine_ci_triggers_its_own_run_over_an_older_failure(self) -> None:
        self.enable_routine()
        self.state.pipeline(10, self.project.source_sha, "manual", "failure")
        status = self.submit()
        request_id = status["request_id"]
        self.dispatch(request_id)
        self.deploy_catalog(request_id)
        self.configure_protection()
        result = self.dispatch(request_id)
        self.assertEqual(result["state"], "succeeded", result)
        ci = next(row for row in result["operations"] if row["id"] == "ci")
        self.assertEqual(ci["outputs"]["pipeline_id"], 11)
        self.assertEqual(self.state.pipelines[11]["event"], "manual")

    def test_routine_readiness_fails_when_the_pinned_ci_run_fails(self) -> None:
        self.enable_routine()
        status = self.submit()
        request_id = status["request_id"]
        self.dispatch(request_id)
        self.deploy_catalog(request_id)
        self.configure_protection()
        self.state.pipeline(10, self.project.source_sha, "manual", "running")
        self.assertEqual(self.dispatch(request_id)["state"], "waiting")
        self.state.pipeline(10, self.project.source_sha, "manual", "failure")
        self.runtime.worker.clock = lambda: datetime.now(UTC) + timedelta(seconds=10)
        result = self.dispatch(request_id)
        self.assertEqual((result["state"], result["code"]), ("failed", "CI_FAILED"))
        self.assertEqual(self.hook_tests(), [])

    def test_routine_readiness_waits_for_the_delivered_pipeline_config(
        self,
    ) -> None:
        self.enable_routine()
        status = self.submit()
        request_id = status["request_id"]
        self.dispatch(request_id)
        self.deploy_catalog(request_id)
        self.state.pipeline(10, self.project.source_sha, event="manual")
        self.configure_protection()
        self.state.delivery_config_delay = 2
        self.runtime.adapter.delivery_timeout = 10
        result = self.dispatch(request_id)
        self.assertEqual(result["state"], "succeeded", result)
        self.assertEqual(len(self.hook_tests()), 1)

    def test_routine_readiness_refuses_a_hook_for_another_repository(self) -> None:
        self.enable_routine()
        self.state.hooks = [ApiState.hook(5, "99")]
        status = self.submit()
        request_id = status["request_id"]
        self.dispatch(request_id)
        self.deploy_catalog(request_id)
        self.state.pipeline(10, self.project.source_sha, event="manual")
        self.configure_protection()
        result = self.dispatch(request_id)
        self.assertNotEqual(result["state"], "succeeded", result)
        self.assertEqual(self.hook_tests(), [])
        self.assertNotIn(11, self.state.pipelines)

    def test_routine_strengthens_protection_without_removing_extra_requirements(
        self,
    ) -> None:
        self.enable_routine()
        self.configure_protection()
        self.state.protection.update(
            apply_to_admins=False,
            required_approvals=2,
            require_signed_commits=True,
            status_check_contexts=["independent-check"],
        )
        status = self.submit()
        request_id = status["request_id"]
        self.dispatch(request_id)
        self.assertFalse(
            any("branch_protections" in path for _, path, _ in self.state.writes)
        )
        self.deploy_catalog(request_id)
        self.state.pipeline(10, self.project.source_sha)
        result = self.dispatch(request_id)
        self.assertEqual(result["state"], "succeeded", result)
        rule = self.state.protection
        self.assertTrue(rule["apply_to_admins"])
        self.assertEqual(rule["required_approvals"], 2)
        self.assertTrue(rule["require_signed_commits"])
        self.assertEqual(
            set(rule["status_check_contexts"]), {CONTEXT, "independent-check"}
        )

    def test_automatic_reconciliation_ignores_signed_requests(self) -> None:
        status = self.submit()
        with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(self.credentials)}):
            self.assertEqual(self.runtime.reconcile(), {"requests": []})
        self.assertEqual(self.state.writes, [])
        self.assertEqual(
            self.call("status", {"request_id": status["request_id"]})["state"],
            "awaiting_authorization",
        )

    def test_reconciler_does_not_change_state_when_execution_lock_is_held(self) -> None:
        self.enable_routine()
        status = self.submit()
        with self.runtime.journal.execution():
            result = self.runtime.reconcile()
        self.assertEqual(result["requests"][0]["code"], "RESOURCE_CONFLICT")
        self.assertEqual(
            self.call("status", {"request_id": status["request_id"]})["state"], "queued"
        )
        self.assertEqual(self.state.writes, [])

    def test_journal_v2_migration_preserves_pending_requests(self) -> None:
        status = self.submit()
        with self.runtime.journal.connection() as db:
            db.execute("DROP TABLE authorization_contexts")
            db.execute("PRAGMA user_version=2")
        journal = Journal(self.runtime.journal.path)
        self.assertEqual(journal.status(status["request_id"], "agent-test"), status)
        self.assertIsNone(journal.authorization_context(status["request_id"]))

    def deploy_catalog(self, request_id: str) -> None:
        artifact = loads(
            (
                self.runtime.config.state_directory
                / "artifacts"
                / (request_id + ".json")
            ).read_bytes()
        )
        self.state.publish_catalog(artifact["catalog"], "d" * 40)
        deployed = {
            "schema_version": 1,
            "exclusive": True,
            "contract_digest": contract_digest(),
            "package_digest": "sha256:" + "c" * 64,
            "catalog": artifact["catalog"],
        }
        Path(self.config_document["deployed_policy_file"]).write_bytes(
            canonical(deployed)
        )

    def probe(self, request_id: str) -> None:
        self.state.protection = {
            "branch_name": "main",
            "enable_push": False,
            "enable_push_whitelist": False,
            "enable_status_check": True,
            "status_check_contexts": [CONTEXT],
            "apply_to_admins": True,
        }
        self.state.pipeline(8, "f" * 40, "pull_request", "failure")
        now = datetime.now(UTC)
        payload = {
            "schema_version": 1,
            "request_id": request_id,
            "repository": self.project.repository,
            "branch": "main",
            "source_sha": self.project.source_sha,
            "probe_sha": "f" * 40,
            "probe_pr": 2,
            "probe_pipeline": 8,
            "protection_digest": digest(
                {key: self.state.protection[key] for key in PROTECTION_FIELDS}
            ),
            "merge_rejected": True,
            "observed_at": stamp(now),
            "expires_at": stamp(now + timedelta(hours=1)),
        }
        proof = {
            "schema_version": 1,
            "payload": payload,
            "key_id": "test-operator",
            "signature": base64.b64encode(
                self.key.sign(PROBE_DOMAIN + canonical(payload))
            ).decode(),
        }
        self.state.files[f"config/projectctl/probes/{request_id}.json"] = proof
        self.state.publish_catalog(self.state.catalogs[self.state.revision], "e" * 40)

    def test_end_to_end_http_onboarding_waits_for_source_catalog_and_probe(
        self,
    ) -> None:
        status = self.submit()
        request_id = status["request_id"]
        self.approve(status)
        first = self.dispatch(request_id)
        self.assertEqual(first["state"], "waiting", first)
        self.assertEqual(
            next(row for row in first["operations"] if row["id"] == "source")["state"],
            "waiting",
        )
        self.assertNotIn(
            "/api/repos/73/pipelines", [path for _, path, _ in self.state.writes]
        )
        self.deploy_catalog(request_id)
        self.state.source_sha = self.project.source_sha
        self.state.pipeline(10, self.project.source_sha)
        second = self.dispatch(request_id)
        self.assertEqual(second["state"], "waiting", second)
        self.assertEqual(
            next(row for row in second["operations"] if row["id"] == "readiness")[
                "state"
            ],
            "waiting",
        )
        self.probe(request_id)
        final = self.dispatch(request_id)
        self.assertEqual(final["state"], "succeeded", final)
        self.assertEqual(
            len(
                [
                    path
                    for method, path, _ in self.state.writes
                    if path == "/api/v1/orgs/JEFF7712/repos"
                ]
            ),
            1,
        )
        self.assertNotIn("fixture-registry-password", json.dumps(final))
        with self.runtime.journal.connection() as db:
            self.assertNotIn("fixture-registry-password", "\n".join(db.iterdump()))

    def test_gateway_cannot_dispatch_or_change_its_principal(self) -> None:
        for action in ("dispatch", "apply", "shell"):
            with self.subTest(action=action), self.assertRaises(ProtocolError):
                self.call(action, {})
        with self.assertRaises(ProtocolError):
            self.runtime.handle(
                "other-agent",
                {
                    "schema_version": 1,
                    "action": "plan",
                    "payload": document(self.project),
                },
            )
        self.assertEqual(self.state.writes, [])

    def test_create_response_loss_blocks_unproven_ownership(self) -> None:
        status = self.submit()
        self.approve(status)

        def lose_response(*args: Any) -> None:
            raise OSError("response lost before durable allocation")

        with patch.object(self.runtime.journal, "allocate", side_effect=lose_response):
            first = self.dispatch(status["request_id"])
        self.assertEqual(first["code"], Code.OUTCOME_UNCERTAIN)
        self.assertIsNotNone(self.state.repository)
        second = self.dispatch(status["request_id"])
        self.assertEqual(second["code"], Code.RESOURCE_CONFLICT)
        self.assertEqual(len(self.state.writes), 1)

    def test_crash_after_allocation_resumes_without_repository_creation(self) -> None:
        status = self.submit()
        self.approve(status)
        original = self.runtime.journal.allocate

        def crash(*args: Any) -> None:
            original(*args)
            raise SystemExit(17)

        with (
            patch.object(self.runtime.journal, "allocate", side_effect=crash),
            self.assertRaises(SystemExit),
        ):
            self.dispatch(status["request_id"])
        second = self.dispatch(status["request_id"])
        self.assertEqual(second["state"], "waiting", second)
        self.assertEqual(
            sum(
                path == "/api/v1/orgs/JEFF7712/repos"
                for _, path, _ in self.state.writes
            ),
            1,
        )

    def test_revocation_between_activation_and_settings_prevents_next_write(
        self,
    ) -> None:
        status = self.submit()
        self.approve(status)
        original = self.runtime.journal.allocate

        def revoke(request: str, operation: str, attempt: int, resource: int) -> None:
            original(request, operation, attempt, resource)
            if operation == "enrollment":
                del self.state.files[f"config/projectctl/approvals/{request}.json"]

        with patch.object(self.runtime.journal, "allocate", side_effect=revoke):
            result = self.dispatch(status["request_id"])
        self.assertEqual(result["code"], Code.OUTCOME_UNCERTAIN)
        self.assertFalse(
            any(
                method == "PATCH" and path == "/api/repos/73"
                for method, path, _ in self.state.writes
            )
        )

    def test_allocated_repository_deletion_does_not_recreate_it(self) -> None:
        status = self.submit()
        self.approve(status)
        self.dispatch(status["request_id"])
        self.state.repository = None
        writes = len(self.state.writes)
        result = self.dispatch(status["request_id"])
        self.assertEqual(result["code"], Code.PLAN_STALE)
        self.assertEqual(len(self.state.writes), writes)

    def test_service_bindings_change_invalidates_approved_plan(self) -> None:
        candidate = self.call("plan", document(self.project))
        config = copy.deepcopy(self.config_document)
        config["clone_identity"] = "different-clone-user"
        runtime = Runtime(Configuration(config))
        with self.assertRaises(ProtocolError) as raised:
            runtime.handle(
                "agent-test",
                {
                    "schema_version": 1,
                    "action": "request",
                    "payload": {**candidate, "idempotency_key": "changed-binding"},
                },
            )
        self.assertEqual(raised.exception.code, Code.POLICY_CHANGED)
        self.assertEqual(self.state.writes, [])

    def test_operator_accepts_openssh_private_key_format(self) -> None:
        self.key_path.write_bytes(
            self.key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.OpenSSH,
                serialization.NoEncryption(),
            )
        )
        status = self.submit()
        self.approve(status)
        result = self.dispatch(status["request_id"])
        self.assertEqual(result["state"], "waiting", result)

    def test_operator_accepts_encrypted_openssh_key_without_logging_passphrase(
        self,
    ) -> None:
        self.key_path.write_bytes(
            self.key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.OpenSSH,
                serialization.BestAvailableEncryption(b"fixture-passphrase"),
            )
        )
        status = self.submit()
        with (
            patch("scripts.projectctl.operator.sys.stdin.isatty", return_value=True),
            patch(
                "scripts.projectctl.operator.getpass.getpass",
                return_value="fixture-passphrase",
            ),
        ):
            self.approve(status)
        self.assertEqual(self.dispatch(status["request_id"])["state"], "waiting")

    def test_operator_dispatch_rejects_admin_credentials_before_mutations(self) -> None:
        status = self.submit()
        self.approve(status)
        self.state.administrator_roles.add("write")
        output = io.StringIO()
        with (
            patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(self.credentials)}),
            contextlib.redirect_stdout(output),
        ):
            code = operator_main(
                ["dispatch", status["request_id"], "--config", str(self.config_path)]
            )
        self.assertEqual(code, 3)
        self.assertEqual(
            loads(output.getvalue())["error"]["code"], Code.AUTHORIZATION_REQUIRED
        )
        self.assertEqual(self.state.writes, [])

    def test_doctor_does_not_dispatch_or_mutate(self) -> None:
        report = self.call("doctor", {"project_key": self.project.project_key})
        self.assertEqual(report["capabilities"]["repository"]["state"], "missing")
        self.assertEqual(self.state.writes, [])

    def test_identity_verification_rejects_administrator_write_tokens(self) -> None:
        with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(self.credentials)}):
            report = verify_identities(self.runtime.config)
            self.assertEqual(report["provisioner"], "projectctl-write")
            self.state.administrator_roles.add("write")
            with self.assertRaises(ProtocolError) as raised:
                verify_identities(self.runtime.config)
        self.assertEqual(raised.exception.code, Code.AUTHORIZATION_REQUIRED)
        self.assertEqual(self.state.writes, [])

    def test_identity_verification_accepts_omitted_woodpecker_false_flags(self) -> None:
        with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(self.credentials)}):
            report = verify_identities(self.runtime.config)
        self.assertFalse(report["administrators"])
        self.assertEqual(report["woodpecker_reader"], "projectctl-read")
        self.assertEqual(self.state.writes, [])

    def test_identity_verification_rejects_ambiguous_admin_flags(self) -> None:
        identities = [
            {"login": "projectctl-read", "is_admin": False},
            {"login": "projectctl-read"},
            {"login": "projectctl-write", "is_admin": False},
            {"login": "projectctl-write"},
        ]
        cases = [
            (index, flag, value)
            for index in (1, 3)
            for flag in ("admin", "admin_env")
            for value in (True, None, 0, "false")
        ]
        cases.extend((index, "is_admin", None) for index in (0, 2))
        for index, flag, value in cases:
            users = [dict(user) for user in identities]
            users[index][flag] = value
            with (
                self.subTest(index=index, flag=flag, value=value),
                patch.dict(
                    os.environ, {"CREDENTIALS_DIRECTORY": str(self.credentials)}
                ),
                patch("scripts.projectctl.provision.Api.request", side_effect=users),
                self.assertRaises(ProtocolError) as raised,
            ):
                verify_identities(self.runtime.config)
            self.assertEqual(raised.exception.code, Code.AUTHORIZATION_REQUIRED)
        self.assertEqual(self.state.writes, [])

    def test_doctor_requires_successful_validation_workflow(self) -> None:
        status = self.submit()
        self.approve(status)
        self.dispatch(status["request_id"])
        self.state.source_sha = self.project.source_sha
        self.state.pipeline(10, self.project.source_sha)
        self.state.pipelines[10]["workflows"] = []
        writes = len(self.state.writes)
        report = self.call("doctor", {"project_key": self.project.project_key})
        self.assertEqual(report["capabilities"]["ci_validation"]["state"], "degraded")
        self.assertEqual(len(self.state.writes), writes)

    def test_doctor_reports_protection_unknown_without_privileged_credentials(
        self,
    ) -> None:
        status = self.submit()
        self.approve(status)
        self.dispatch(status["request_id"])
        self.state.source_sha = self.project.source_sha
        self.state.pipeline(10, self.project.source_sha)
        writes = len(self.state.writes)
        with patch.object(
            self.runtime.config,
            "authorization",
            wraps=self.runtime.config.authorization,
        ) as authorization:
            report = self.call("doctor", {"project_key": self.project.project_key})
        protection = report["capabilities"]["branch_protection"]
        self.assertEqual(protection["state"], "unknown")
        self.assertTrue(protection["requires_operator_verification"])
        self.assertEqual(report["capabilities"]["repository"]["state"], "ready")
        self.assertEqual(len(self.state.writes), writes)
        self.assertTrue(
            all(call.args[1] is False for call in authorization.call_args_list)
        )

    def test_doctor_reports_protection_from_the_worker_readiness_receipt(
        self,
    ) -> None:
        self.enable_routine()
        status = self.submit()
        request_id = status["request_id"]
        self.dispatch(request_id)
        self.deploy_catalog(request_id)
        self.state.pipeline(10, self.project.source_sha)
        self.assertEqual(self.dispatch(request_id)["state"], "succeeded")
        writes = len(self.state.writes)
        with patch.object(
            self.runtime.config,
            "authorization",
            wraps=self.runtime.config.authorization,
        ) as authorization:
            report = self.call("doctor", {"project_key": self.project.project_key})
        protection = report["capabilities"]["branch_protection"]
        self.assertEqual(protection["state"], "configured", protection)
        self.assertEqual(protection["source"], "worker_readiness_receipt")
        self.assertEqual(protection["request_id"], request_id)
        self.assertEqual(len(self.state.writes), writes)
        self.assertTrue(
            all(call.args[1] is False for call in authorization.call_args_list)
        )

    def test_credential_collection_never_outputs_plaintext(self) -> None:
        output = self.root / "projectctl.sops.json"
        result = subprocess.CompletedProcess(
            [], 0, b'{"sops":{"fixture":"ciphertext"}}', b""
        )
        with (
            patch("scripts.projectctl.provision.sys.stdin.isatty", return_value=True),
            patch("scripts.projectctl.provision.sys.stderr.isatty", return_value=True),
            patch(
                "scripts.projectctl.provision.getpass.getpass",
                side_effect=lambda prompt: "fixture-" + prompt.split(":")[0],
            ),
            patch(
                "scripts.projectctl.provision.subprocess.run", return_value=result
            ) as run,
        ):
            report = collect(output)
        self.assertNotIn("fixture-forgejo", json.dumps(report))
        self.assertNotIn(b"fixture-forgejo", output.read_bytes())
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertIn("--encrypt", run.call_args.args[0])

    def test_credential_installation_validates_before_writing_and_never_overwrites(
        self,
    ) -> None:
        values = {
            name: "fixture-" + name for name in self.config_document["secret_files"]
        }
        invalid = {**values, "forgejo-write": values["forgejo-read"]}
        directory = self.root / "installed"
        with self.assertRaises(ProtocolError):
            install(invalid, directory, os.geteuid(), os.getegid())
        self.assertFalse(directory.exists())
        report = install(values, directory, os.geteuid(), os.getegid())
        self.assertNotIn("fixture-", json.dumps(report))
        self.assertEqual(directory.stat().st_mode & 0o777, 0o750)
        self.assertTrue(
            all(path.stat().st_mode & 0o777 == 0o600 for path in directory.iterdir())
        )
        with self.assertRaises(ProtocolError):
            install(values, directory, os.geteuid(), os.getegid())
        with self.assertRaises(ProtocolError):
            credentials({**values, "arbitrary": "credential"})

    def test_approval_tampering_and_revocation_stop_real_http_writes(self) -> None:
        status = self.submit()
        request_id = status["request_id"]
        self.approve(status)
        path = f"config/projectctl/approvals/{request_id}.json"
        approval = copy.deepcopy(self.state.files[path])
        self.state.files[path]["payload"]["grant_id"] = "tampered"
        first = self.dispatch(request_id)
        self.assertEqual(first["code"], Code.AUTHORIZATION_REQUIRED)
        self.assertEqual(self.state.writes, [])
        self.state.files[path] = approval
        second = self.dispatch(request_id)
        self.assertEqual(second["state"], "waiting", second)
        writes = len(self.state.writes)
        del self.state.files[path]
        self.dispatch(request_id)
        self.assertEqual(len(self.state.writes), writes)

    def test_approved_registry_transition_preserves_unrelated_entries(self) -> None:
        neighbor = {
            "name": "other",
            "forgejo_repo": "JEFF7712/other",
            "woodpecker_repository_id": 20,
            "ci": {"kind": "none"},
        }
        catalog = {"schema_version": 1, "projects": [neighbor]}
        result = catalog_patch(catalog, self.project, None, 73)
        self.assertEqual(result["projects"][0], neighbor)
        target = entry(result, self.project)
        self.assertEqual(target, desired(self.project, None, 73))
        assert target is not None
        with self.assertRaises(ProtocolError):
            verify_transition(
                self.project, None, {**target, "visibility": "public"}, 73
            )

    def test_changed_catalog_security_fields_block_resume(self) -> None:
        status = self.submit()
        self.approve(status)
        self.assertEqual(self.dispatch(status["request_id"])["state"], "waiting")
        self.deploy_catalog(status["request_id"])
        changed = copy.deepcopy(self.state.catalogs[self.state.revision])
        changed["projects"][0]["ci"]["tier"] = "deploy"
        self.state.publish_catalog(changed, "e" * 40)
        writes = len(self.state.writes)
        result = self.dispatch(status["request_id"])
        self.assertEqual(result["code"], Code.PLAN_STALE)
        self.assertEqual(len(self.state.writes), writes)

    def test_immutable_attestation_is_required_after_catalog_merge(self) -> None:
        status = self.submit()
        self.approve(status)
        self.dispatch(status["request_id"])
        self.deploy_catalog(status["request_id"])
        self.state.source_sha = self.project.source_sha
        attestation = loads(
            Path(self.config_document["deployed_policy_file"]).read_bytes()
        )
        attestation["contract_digest"] = "sha256:" + "f" * 64
        Path(self.config_document["deployed_policy_file"]).write_bytes(
            canonical(attestation)
        )
        result = self.dispatch(status["request_id"])
        self.assertEqual(result["state"], "waiting", result)
        self.assertEqual(
            next(row for row in result["operations"] if row["id"] == "policy")["state"],
            "waiting",
        )

    def test_wrong_contract_pipeline_cannot_satisfy_current_policy(self) -> None:
        self.state.pipeline(10, self.project.source_sha)
        self.state.configs[10] = [
            {"name": WORKFLOW, "data": base64.b64encode(b"environment: {}\n").decode()}
        ]
        self.assertFalse(self.runtime.adapter._configuration_matches(73, 10))

    def test_gateway_subprocess_returns_structured_error_without_backend_secret(
        self,
    ) -> None:
        payload = {"schema_version": 1, "action": "dispatch", "payload": {}}
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.projectctl.gateway",
                "--config",
                str(self.config_path),
                "--principal",
                "agent-test",
            ],
            input=canonical(payload),
            capture_output=True,
            cwd=ROOT,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(loads(result.stdout)["error"]["code"], Code.INVALID_SPEC)
        self.assertNotIn(b"Traceback", result.stderr)

    def test_status_can_be_read_while_worker_holds_execution_lock(self) -> None:
        status = self.submit()
        with self.runtime.journal.execution():
            other = Runtime(Configuration(self.config_document))
            result = other.handle(
                "agent-test",
                {
                    "schema_version": 1,
                    "action": "status",
                    "payload": {"request_id": status["request_id"]},
                },
            )
        self.assertEqual(result["state"], "awaiting_authorization")
        self.assertEqual(self.state.writes, [])

    def test_backup_roundtrip_and_no_overwrite(self) -> None:
        status = self.submit()
        path = self.root / "backup" / "journal.sqlite"
        self.runtime.journal.backup(path)
        restored = Journal(path)
        self.assertEqual(
            restored.status(status["request_id"], "agent-test")["plan_digest"],
            status["plan_digest"],
        )
        with self.assertRaises(ProtocolError):
            self.runtime.journal.backup(path)

    def test_write_credentials_are_not_available_to_gateway(self) -> None:
        with patch.dict(os.environ):
            os.environ.pop("CREDENTIALS_DIRECTORY", None)
            with self.assertRaises(ProtocolError):
                self.runtime.config.authorization("forgejo", True)
        self.assertTrue(
            self.runtime.config.authorization("forgejo", False).startswith("token ")
        )

    def test_registry_metadata_requires_repository_write_permission(self) -> None:
        with self.assertRaises(ProtocolError) as raised:
            self.runtime.config.apis["woodpecker"].request(
                "/api/repos/73/registries/registry.internal", missing=True
            )
        self.assertEqual(raised.exception.code, Code.AUTHORIZATION_REQUIRED)
        self.assertEqual(self.state.writes, [])

    def test_clone_permission_observation_requires_worker_credentials(self) -> None:
        self.state.repository = {
            "id": 41,
            "full_name": self.project.repository,
            "private": True,
            "default_branch": "main",
        }
        with self.assertRaises(ProtocolError) as error:
            self.runtime.config.apis["forgejo"].request(
                "/api/v1/repos/JEFF7712/probe-py/collaborators/clone-reader/permission"
            )
        self.assertEqual(error.exception.code, Code.AUTHORIZATION_REQUIRED)
        with patch.dict(os.environ):
            os.environ.pop("CREDENTIALS_DIRECTORY", None)
            with self.assertRaises(ProtocolError):
                self.runtime.adapter.repository_admin_api.request(
                    "/api/v1/repos/JEFF7712/probe-py/collaborators/clone-reader/permission"
                )
        self.assertEqual(self.state.writes, [])

    def test_registry_observation_credentials_are_unavailable_to_gateway(self) -> None:
        with patch.dict(os.environ):
            os.environ.pop("CREDENTIALS_DIRECTORY", None)
            with self.assertRaises(ProtocolError) as raised:
                self.runtime.adapter.registry_api.request(
                    "/api/repos/73/registries/registry.internal", missing=True
                )
        self.assertEqual(raised.exception.code, Code.AUTHORIZATION_REQUIRED)
        self.assertEqual(self.state.writes, [])

    def test_public_cli_plan_request_status_and_local_worktree_adoption(self) -> None:
        checkout = self.root / "probe-py"
        checkout.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main", str(checkout)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(checkout),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "--allow-empty",
                "-m",
                "Initial test commit",
            ],
            check=True,
            capture_output=True,
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli_main(
                [
                    "adopt",
                    str(checkout),
                    "--repository",
                    self.project.repository,
                    "--ci",
                    "python-uv-v1",
                    "--plan",
                    "--json",
                ],
                client=self.client,
            )
        self.assertEqual(code, 0, output.getvalue())
        candidate = loads(output.getvalue())
        self.assertFalse(candidate["data"]["local_git"]["dirty"])
        plan_file = self.root / "plan.json"
        plan_file.write_text(output.getvalue())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli_main(
                ["request", str(plan_file), "--idempotency-key", "cli-1", "--json"],
                client=self.client,
            )
        self.assertEqual(code, 0, output.getvalue())
        request_id = loads(output.getvalue())["data"]["request_id"]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli_main(["status", request_id, "--json"], client=self.client)
        self.assertEqual(code, 0)
        self.assertEqual(
            loads(output.getvalue())["data"]["state"], "awaiting_authorization"
        )
        self.assertEqual(self.state.writes, [])

    def test_ssh_transport_has_no_remote_shell_command(self) -> None:
        process = subprocess.CompletedProcess(
            [], 0, canonical(success({"state": "waiting"})), b""
        )
        with patch(
            "scripts.projectctl.__main__.subprocess.run", return_value=process
        ) as run:
            self.assertEqual(
                SshClient("projectctl-worker").send(
                    "status", {"request_id": "req-test"}
                )["state"],
                "waiting",
            )
        arguments = run.call_args.args[0]
        self.assertEqual(arguments[-2:], ["--", "projectctl-worker"])
        self.assertNotIn("shell", run.call_args.kwargs)
        for target in ("-oProxyCommand=anything", "host;command", "host command"):
            with self.assertRaises(ProtocolError):
                SshClient(target)

    def test_http_errors_are_not_interpreted_as_absence(self) -> None:
        api = Api(
            f"http://127.0.0.1:{self.server.server_port}", lambda write: "token wrong"
        )
        with self.assertRaises(ProtocolError) as raised:
            api.request("/api/v1/repos/JEFF7712/probe-py", missing=True)
        self.assertEqual(raised.exception.code, Code.AUTHORIZATION_REQUIRED)
        self.assertNotIn("backend error", json.dumps(raised.exception.envelope()))
        with self.assertRaises(ProtocolError):
            Api("http://ci.internal:8000", lambda write: "token example")


if __name__ == "__main__":
    unittest.main()
