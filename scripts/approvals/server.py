"""Approvals dashboard: one passkey-confirmed click per held CI operation.

Agents file requests with their own Woodpecker token. The page is served on
the public approvals origin, and an approval needs a WebAuthn assertion with
user verification from an enrolled passkey, so an agent driving the
operator's browser cannot approve on its own. The service then triggers the
deployment with the operator token it alone holds; CI's deployment policy
still checks the parent pipeline and current main.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from scripts.approvals.core import ApprovalError, Request, Store, held

PAGE = (Path(__file__).parent / "page.html").read_text(encoding="utf-8")
REQUEST_PATH = re.compile(
    r"^/api/requests/([A-Za-z0-9_-]+)(?:/(options|approve|decline))?$"
)
IDENTITY_CACHE_SECONDS = 300
BODY_LIMIT = 64 * 1024


class UpstreamError(RuntimeError):
    pass


def http(
    method: str, url: str, headers: dict[str, str], body: bytes | None = None
) -> Any:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace").strip()[:500]
        raise UpstreamError(f"HTTP {error.code} {detail}".strip()) from error
    except OSError as error:
        raise UpstreamError(str(error)) from error
    return json.loads(payload) if payload else None


@dataclass(frozen=True)
class Config:
    catalog: Path
    state: Path
    public_url: str
    woodpecker: str
    repository: int
    operator_token: Path
    ntfy_url: str
    ntfy_topic: str
    ntfy_token: str

    @property
    def rp_id(self) -> str:
        return urllib.parse.urlsplit(self.public_url).hostname or ""


class Woodpecker:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.identities: dict[str, tuple[str, float]] = {}

    def call(self, method: str, path: str, token: str) -> Any:
        return http(
            method,
            f"{self.config.woodpecker}/api{path}",
            {"Authorization": f"Bearer {token}"},
        )

    def operator_token(self) -> str:
        return self.config.operator_token.read_text().strip()

    def identity(self, token: str, now: float) -> str:
        """The Woodpecker login a bearer token belongs to."""
        key = hashlib.sha256(token.encode()).hexdigest()
        cached = self.identities.get(key)
        if cached and cached[1] > now:
            return cached[0]
        try:
            login = self.call("GET", "/user", token)["login"]
        except (UpstreamError, KeyError, TypeError) as error:
            raise ApprovalError("Woodpecker did not accept the bearer token") from error
        self.identities[key] = (login, now + IDENTITY_CACHE_SECONDS)
        return login

    def parent(self, number: int) -> dict[str, Any]:
        repo = self.config.repository
        return self.call(
            "GET", f"/repos/{repo}/pipelines/{number}", self.operator_token()
        )

    def deploy(self, parent: int, operation: str) -> int:
        repo = self.config.repository
        query = urllib.parse.urlencode({"event": "deployment", "deploy_to": operation})
        created = self.call(
            "POST", f"/repos/{repo}/pipelines/{parent}?{query}", self.operator_token()
        )
        return int(created["number"])


def notify(config: Config, request: Request) -> None:
    if not config.ntfy_topic:
        return
    link = f"{config.public_url}/r/{request.id}"
    headers = {
        "Title": f"Approval needed: {request.operation}",
        "Click": link,
        "Actions": f"view, Review, {link}",
        "Priority": "high",
        "Tags": "lock",
    }
    if config.ntfy_token:
        headers["Authorization"] = f"Bearer {config.ntfy_token}"
    body = f"{request.requester} at {request.commit_sha[:12]}: {request.reason}"
    try:
        http(
            "POST",
            f"{config.ntfy_url}/{config.ntfy_topic}",
            headers,
            body.encode()[:4000],
        )
    except UpstreamError as error:
        print(f"ntfy delivery failed: {error}", file=sys.stderr, flush=True)


class Service:
    def __init__(self, config: Config, store: Store, woodpecker: Woodpecker) -> None:
        self.config = config
        self.store = store
        self.woodpecker = woodpecker
        self.catalog = json.loads(config.catalog.read_text())

    def describe(self, request: Request) -> dict[str, Any]:
        return {
            **request.public(),
            "url": f"{self.config.public_url}/r/{request.id}",
        }

    def file(self, token: str, body: dict[str, Any], now: float) -> dict[str, Any]:
        requester = self.woodpecker.identity(token, now)
        operation = str(body.get("operation", ""))
        if not held(self.catalog, operation):
            raise ApprovalError(
                f"{operation} runs without an operator; no approval needed"
            )
        try:
            parent_number = int(body["parent"])
            evidence = [int(item) for item in body.get("evidence") or []]
        except (KeyError, TypeError, ValueError) as error:
            raise ApprovalError(
                "parent and evidence must be pipeline numbers"
            ) from error
        try:
            parent = self.woodpecker.parent(parent_number)
        except UpstreamError as error:
            raise ApprovalError(
                f"Cannot read pipeline #{parent_number}: {error}"
            ) from error
        if parent.get("branch") != "main" or parent.get("status") != "success":
            raise ApprovalError(
                f"Pipeline #{parent_number} is not a successful main pipeline"
            )
        request, created = self.store.create(
            operation,
            parent_number,
            str(parent.get("commit", "")),
            requester,
            str(body.get("reason") or ""),
            evidence,
            now,
        )
        if created:
            notify(self.config, request)
        return self.describe(request)

    def request(self, identifier: str, now: float) -> Request:
        request = self.store.get(identifier, now)
        if request is None:
            raise LookupError(identifier)
        return request

    def approval_options(self, identifier: str, now: float) -> str:
        credentials = self.store.credentials()
        if not credentials:
            raise ApprovalError(
                "No passkey is enrolled; run approvals-enroll on the host"
            )
        challenge = secrets.token_bytes(32)
        self.store.set_challenge(identifier, challenge, now)
        return options_to_json(
            generate_authentication_options(
                rp_id=self.config.rp_id,
                challenge=challenge,
                allow_credentials=[
                    PublicKeyCredentialDescriptor(id=item.id) for item in credentials
                ],
                user_verification=UserVerificationRequirement.REQUIRED,
            )
        )

    def approve(
        self, identifier: str, assertion: dict[str, Any], now: float
    ) -> Request:
        try:
            credential_id = base64url_to_bytes(str(assertion["rawId"]))
        except (KeyError, ValueError) as error:
            raise ApprovalError("Malformed passkey assertion") from error
        stored = next(
            (item for item in self.store.credentials() if item.id == credential_id),
            None,
        )
        if stored is None:
            raise ApprovalError("Passkey is not enrolled")
        challenge = self.store.claim(identifier, now)
        try:
            verified = verify_authentication_response(
                credential=assertion,
                expected_challenge=challenge,
                expected_rp_id=self.config.rp_id,
                expected_origin=self.config.public_url,
                credential_public_key=stored.public_key,
                credential_current_sign_count=stored.sign_count,
                require_user_verification=True,
            )
        except Exception as error:
            self.store.release(identifier)
            raise ApprovalError(f"Passkey verification failed: {error}") from error
        self.store.update_sign_count(stored.id, verified.new_sign_count)
        request = self.request(identifier, now)
        try:
            pipeline = self.woodpecker.deploy(request.parent, request.operation)
        except (UpstreamError, KeyError, TypeError, ValueError) as error:
            self.store.finish(identifier, "failed", now, "passkey", error=str(error))
        else:
            self.store.finish(identifier, "approved", now, "passkey", pipeline=pipeline)
        return self.request(identifier, now)

    def decline(self, identifier: str, now: float) -> Request:
        self.store.finish(identifier, "declined", now, "dashboard")
        return self.request(identifier, now)

    def enrollment_options(self, token: str, now: float) -> str:
        challenge = secrets.token_bytes(32)
        self.store.set_enrollment_challenge(token, challenge, now)
        return options_to_json(
            generate_registration_options(
                rp_id=self.config.rp_id,
                rp_name="Homelab approvals",
                user_name="operator",
                user_id=b"homelab-operator",
                challenge=challenge,
                exclude_credentials=[
                    PublicKeyCredentialDescriptor(id=item.id)
                    for item in self.store.credentials()
                ],
                authenticator_selection=AuthenticatorSelectionCriteria(
                    resident_key=ResidentKeyRequirement.PREFERRED,
                    user_verification=UserVerificationRequirement.REQUIRED,
                ),
            )
        )

    def enroll(
        self, token: str, credential: dict[str, Any], label: str, now: float
    ) -> None:
        challenge = self.store.take_enrollment(token, now)
        try:
            verified = verify_registration_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=self.config.rp_id,
                expected_origin=self.config.public_url,
                require_user_verification=True,
            )
        except Exception as error:
            raise ApprovalError(f"Passkey registration failed: {error}") from error
        self.store.add_credential(
            verified.credential_id,
            verified.credential_public_key,
            verified.sign_count,
            label[:100] or "passkey",
            now,
        )


def handler(service: Service) -> type[BaseHTTPRequestHandler]:
    public_host = urllib.parse.urlsplit(service.config.public_url).netloc

    class Handler(BaseHTTPRequestHandler):
        server_version = "approvals"

        def send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def json(self, status: int, payload: Any) -> None:
            body = payload if isinstance(payload, str) else json.dumps(payload)
            self.send(status, body.encode(), "application/json")

        def body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length > BODY_LIMIT:
                raise ApprovalError("Request body too large")
            try:
                parsed = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError as error:
                raise ApprovalError("Body is not JSON") from error
            if not isinstance(parsed, dict):
                raise ApprovalError("Body must be a JSON object")
            return parsed

        def same_origin(self) -> None:
            if self.headers.get("Origin") != service.config.public_url:
                raise ApprovalError(
                    "Dashboard actions must come from the approvals page"
                )

        def bearer(self) -> str:
            value = self.headers.get("Authorization", "")
            if not value.startswith("Bearer "):
                raise ApprovalError("Missing Woodpecker bearer token")
            return value.removeprefix("Bearer ").strip()

        def dispatch(self, method: str) -> None:
            now = time.time()
            path = urllib.parse.urlsplit(self.path).path
            try:
                if method == "GET" and (
                    path in {"/", "/enroll"} or path.startswith("/r/")
                ):
                    if self.headers.get("Host") != public_host:
                        self.send_response(302)
                        self.send_header(
                            "Location", service.config.public_url + self.path
                        )
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    self.send(200, PAGE.encode(), "text/html; charset=utf-8")
                    return
                if method == "GET" and path == "/healthz":
                    self.json(200, {"ok": True})
                    return
                if method == "GET" and path == "/api/requests":
                    self.json(
                        200,
                        {
                            "requests": [
                                service.describe(item)
                                for item in service.store.recent(now)
                            ],
                            "enrolled": bool(service.store.credentials()),
                        },
                    )
                    return
                if method == "POST" and path == "/api/requests":
                    self.json(201, service.file(self.bearer(), self.body(), now))
                    return
                if method == "POST" and path == "/api/enroll/options":
                    self.same_origin()
                    self.json(
                        200,
                        service.enrollment_options(
                            str(self.body().get("token", "")), now
                        ),
                    )
                    return
                if method == "POST" and path == "/api/enroll":
                    self.same_origin()
                    body = self.body()
                    service.enroll(
                        str(body.get("token", "")),
                        body.get("credential") or {},
                        str(body.get("label", "")),
                        now,
                    )
                    self.json(200, {"enrolled": True})
                    return
                match = REQUEST_PATH.match(path)
                if match is None:
                    self.json(404, {"error": "Not found"})
                    return
                identifier, action = match.groups()
                if method == "GET" and action is None:
                    self.json(200, service.describe(service.request(identifier, now)))
                    return
                if method != "POST" or action is None:
                    self.json(405, {"error": "Method not allowed"})
                    return
                self.same_origin()
                service.request(identifier, now)
                if action == "options":
                    self.json(200, service.approval_options(identifier, now))
                elif action == "approve":
                    request = service.approve(identifier, self.body(), now)
                    self.json(200, service.describe(request))
                else:
                    self.json(200, service.describe(service.decline(identifier, now)))
            except LookupError:
                self.json(404, {"error": "No such request"})
            except ApprovalError as error:
                self.json(400, {"error": str(error)})

        def do_GET(self) -> None:
            self.dispatch("GET")

        def do_POST(self) -> None:
            self.dispatch("POST")

        def log_message(self, format: str, *args: Any) -> None:
            print(
                f"{self.address_string()} {format % args}", file=sys.stderr, flush=True
            )

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="approvals")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--public-url", default="https://approvals.rupan.dev")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--catalog", type=Path, required=True)
    serve.add_argument("--listen", default="0.0.0.0:8020")
    serve.add_argument("--woodpecker", default="http://127.0.0.1:8000")
    serve.add_argument("--repository", type=int, default=1)
    serve.add_argument("--operator-token", type=Path, required=True)
    sub.add_parser("enroll")
    args = parser.parse_args(argv)
    store = Store(args.state / "approvals.sqlite")
    public_url = args.public_url.rstrip("/")
    if args.command == "enroll":
        token = store.create_enrollment(time.time())
        print(f"{public_url}/enroll?token={token}  (valid 15 minutes, single use)")
        return 0
    config = Config(
        catalog=args.catalog,
        state=args.state,
        public_url=public_url,
        woodpecker=args.woodpecker.rstrip("/"),
        repository=args.repository,
        operator_token=args.operator_token,
        ntfy_url=os.environ.get("NTFY_URL", "https://ntfy.rupan.dev").rstrip("/"),
        ntfy_topic=os.environ.get("NTFY_TOPIC", ""),
        ntfy_token=os.environ.get("NTFY_TOKEN", ""),
    )
    service = Service(config, store, Woodpecker(config))
    host, port = args.listen.rsplit(":", 1)
    server = ThreadingHTTPServer((host, int(port)), handler(service))
    print(f"approvals listening on {args.listen} for {public_url}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
