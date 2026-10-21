import base64
import hashlib
import json
import struct
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import encode_cbor

from scripts.approvals import core, server
from scripts.approvals.core import ApprovalError, Store, held

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = "https://approvals.example.test"
CATALOG = json.loads((ROOT / "config/ci/operations.json").read_text())


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Passkey:
    """A software authenticator producing real ES256 WebAuthn assertions."""

    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.id = b"passkey-credential-id"
        self.count = 0
        numbers = self.key.public_key().public_numbers()
        self.cose = encode_cbor(
            {
                1: 2,
                3: -7,
                -1: 1,
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )

    def assert_(
        self,
        options: dict,
        origin: str = PUBLIC,
        rp_id: str = "approvals.example.test",
        verified: bool = True,
    ) -> dict:
        self.count += 1
        flags = 0x01 | (0x04 if verified else 0)
        auth_data = (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([flags])
            + struct.pack(">I", self.count)
        )
        client_data = json.dumps(
            {
                "type": "webauthn.get",
                "challenge": options["challenge"],
                "origin": origin,
            }
        ).encode()
        signature = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": b64(self.id),
            "rawId": b64(self.id),
            "type": "public-key",
            "response": {
                "authenticatorData": b64(auth_data),
                "clientDataJSON": b64(client_data),
                "signature": b64(signature),
            },
        }


class Harness:
    def __init__(self, directory: Path) -> None:
        self.config = server.Config(
            catalog=ROOT / "config/ci/operations.json",
            state=directory,
            public_url=PUBLIC,
            woodpecker="http://woodpecker.invalid",
            repository=1,
            operator_token=directory / "operator-token",
            ntfy_url="https://ntfy.invalid",
            ntfy_topic="",
            ntfy_token="",
        )
        self.store = Store(directory / "approvals.sqlite")
        self.woodpecker = mock.Mock()
        self.woodpecker.identity.return_value = "homelab-agent"
        self.woodpecker.parent.return_value = {
            "branch": "main",
            "status": "success",
            "commit": "a" * 40,
        }
        self.woodpecker.deploy.return_value = 501
        self.service = server.Service(self.config, self.store, self.woodpecker)
        self.passkey = Passkey()
        self.store.add_credential(self.passkey.id, self.passkey.cose, 0, "test", 0)

    def file(self, operation: str = "deploy-ci-host", now: float = 100) -> dict:
        return self.service.file(
            "agent-token",
            {"operation": operation, "parent": 40, "reason": "ship", "evidence": [39]},
            now,
        )


class HeldOperationTest(unittest.TestCase):
    def test_only_operator_mutations_are_held(self) -> None:
        self.assertTrue(held(CATALOG, "deploy-ci-host"))
        self.assertTrue(held(CATALOG, "deploy-fleet"))
        self.assertFalse(held(CATALOG, "deploy-ci-host-dry-run"))
        self.assertFalse(held(CATALOG, "matrix-publish"))
        with self.assertRaisesRegex(ApprovalError, "Unknown operation"):
            held(CATALOG, "no-such-operation")


class ApprovalFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.h = Harness(Path(self.directory.name))

    def tearDown(self) -> None:
        self.h.store.close()
        self.directory.cleanup()

    def approve(self, identifier: str, now: float = 110, **kwargs) -> core.Request:
        options = json.loads(self.h.service.approval_options(identifier, now))
        return self.h.service.approve(
            identifier, self.h.passkey.assert_(options, **kwargs), now
        )

    def test_filing_records_the_parent_commit_and_deduplicates(self) -> None:
        first = self.h.file()
        again = self.h.file()
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(first["state"], "pending")
        self.assertEqual(first["commit_sha"], "a" * 40)
        self.assertEqual(first["requester"], "homelab-agent")
        self.assertEqual(first["evidence"], [39])
        self.assertEqual(first["url"], f"{PUBLIC}/r/{first['id']}")

    def test_filing_refuses_unheld_operations_and_bad_parents(self) -> None:
        with self.assertRaisesRegex(ApprovalError, "no approval needed"):
            self.h.file("matrix-publish")
        self.h.woodpecker.parent.return_value = {
            "branch": "feat/x",
            "status": "success",
        }
        with self.assertRaisesRegex(ApprovalError, "not a successful main pipeline"):
            self.h.file()
        self.h.woodpecker.parent.return_value = {"branch": "main", "status": "failure"}
        with self.assertRaisesRegex(ApprovalError, "not a successful main pipeline"):
            self.h.file()

    def test_new_requests_notify_once(self) -> None:
        with mock.patch.object(server, "notify") as notify:
            self.h.file()
            self.h.file()
        notify.assert_called_once()

    def test_passkey_approval_triggers_the_deployment_once(self) -> None:
        identifier = self.h.file()["id"]
        request = self.approve(identifier)
        self.assertEqual(request.state, "approved")
        self.assertEqual(request.pipeline, 501)
        self.h.woodpecker.deploy.assert_called_once_with(40, "deploy-ci-host")
        with self.assertRaisesRegex(ApprovalError, "not pending"):
            self.approve(identifier)
        self.h.woodpecker.deploy.assert_called_once()

    def test_an_assertion_cannot_be_replayed(self) -> None:
        identifier = self.h.file()["id"]
        options = json.loads(self.h.service.approval_options(identifier, 110))
        assertion = self.h.passkey.assert_(options)
        self.h.service.approve(identifier, assertion, 110)
        second = self.h.file("deploy-fleet")["id"]
        self.h.service.approval_options(second, 111)
        with self.assertRaisesRegex(ApprovalError, "verification failed"):
            self.h.service.approve(second, assertion, 111)
        self.assertEqual(self.h.store.get(second, 111).state, "pending")

    def test_assertions_without_user_verification_are_refused(self) -> None:
        identifier = self.h.file()["id"]
        with self.assertRaisesRegex(ApprovalError, "verification failed"):
            self.approve(identifier, verified=False)
        self.assertEqual(self.h.store.get(identifier, 110).state, "pending")
        self.h.woodpecker.deploy.assert_not_called()

    def test_assertions_from_another_origin_are_refused(self) -> None:
        identifier = self.h.file()["id"]
        with self.assertRaisesRegex(ApprovalError, "verification failed"):
            self.approve(identifier, origin="http://ci.internal:8020")
        self.h.woodpecker.deploy.assert_not_called()

    def test_unenrolled_passkeys_are_refused(self) -> None:
        identifier = self.h.file()["id"]
        options = json.loads(self.h.service.approval_options(identifier, 110))
        stranger = Passkey()
        stranger.id = b"someone-else"
        with self.assertRaisesRegex(ApprovalError, "not enrolled"):
            self.h.service.approve(identifier, stranger.assert_(options), 110)

    def test_a_rejected_deployment_is_recorded_as_failed(self) -> None:
        self.h.woodpecker.deploy.side_effect = server.UpstreamError(
            "HTTP 403 CI policy rejected request"
        )
        request = self.approve(self.h.file()["id"])
        self.assertEqual(request.state, "failed")
        self.assertIn("CI policy rejected", request.error)

    def test_declined_and_expired_requests_cannot_be_approved(self) -> None:
        declined = self.h.file()["id"]
        self.h.service.decline(declined, 105)
        with self.assertRaisesRegex(ApprovalError, "not pending"):
            self.h.service.approval_options(declined, 106)
        stale = self.h.file("deploy-fleet", now=100)["id"]
        later = 100 + core.REQUEST_TTL_SECONDS + 1
        self.assertEqual(self.h.store.get(stale, later).state, "expired")
        with self.assertRaisesRegex(ApprovalError, "not pending"):
            self.h.service.approval_options(stale, later)

    def test_enrollment_tokens_are_single_use_and_expire(self) -> None:
        token = self.h.store.create_enrollment(0)
        self.h.store.set_enrollment_challenge(token, b"c", 1)
        self.assertEqual(self.h.store.take_enrollment(token, 2), b"c")
        with self.assertRaisesRegex(ApprovalError, "invalid or expired"):
            self.h.store.take_enrollment(token, 3)
        late = self.h.store.create_enrollment(0)
        with self.assertRaisesRegex(ApprovalError, "invalid or expired"):
            self.h.store.set_enrollment_challenge(
                late, b"c", core.ENROLLMENT_TTL_SECONDS + 1
            )


class HandlerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.h = Harness(Path(self.directory.name))
        handler = server.handler(self.h.service)
        handler.log_message = lambda *args: None  # type: ignore[method-assign]
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.h.store.close()
        self.directory.cleanup()

    def call(self, method: str, path: str, headers: dict | None = None, body=None):
        request = urllib.request.Request(
            self.base + path,
            method=method,
            headers=headers or {},
            data=None if body is None else json.dumps(body).encode(),
        )
        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(request) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()

    def test_the_page_redirects_to_the_public_origin(self) -> None:
        status, headers, _ = self.call("GET", "/r/abc")
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], f"{PUBLIC}/r/abc")
        host = PUBLIC.removeprefix("https://")
        status, _, body = self.call("GET", "/", {"Host": host})
        self.assertEqual(status, 200)
        self.assertIn(b"Homelab approvals", body)

    def test_agents_file_with_a_bearer_token(self) -> None:
        status, _, body = self.call(
            "POST", "/api/requests", body={"operation": "deploy-ci-host", "parent": 40}
        )
        self.assertEqual(status, 400)
        self.assertIn(b"bearer", body)
        status, _, body = self.call(
            "POST",
            "/api/requests",
            {"Authorization": "Bearer agent-token"},
            {"operation": "deploy-ci-host", "parent": 40},
        )
        self.assertEqual(status, 201)
        identifier = json.loads(body)["id"]
        status, _, body = self.call("GET", f"/api/requests/{identifier}")
        self.assertEqual(json.loads(body)["state"], "pending")

    def test_dashboard_actions_require_the_public_origin(self) -> None:
        identifier = self.h.file(now=time.time())["id"]
        for origin in ({}, {"Origin": "http://ci.internal:8020"}):
            status, _, body = self.call(
                "POST", f"/api/requests/{identifier}/decline", origin, {}
            )
            self.assertEqual(status, 400, origin)
            self.assertIn(b"approvals page", body)
        status, _, body = self.call(
            "POST", f"/api/requests/{identifier}/decline", {"Origin": PUBLIC}, {}
        )
        self.assertEqual(json.loads(body)["state"], "declined")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


if __name__ == "__main__":
    unittest.main()
