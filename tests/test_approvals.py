import base64
import hashlib
import json
import sqlite3
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
from scripts.ci.deployment_risk import authority_digest

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
        self.woodpecker.main_commit.return_value = "a" * 40
        self.forgejo = mock.Mock()
        self.forgejo.identity.return_value = "homelab-policy"
        self.service = server.Service(
            self.config, self.store, self.woodpecker, self.forgejo
        )
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
        self.assertTrue(held(CATALOG, "matrix-publish"))
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
            self.h.file("deploy-ci-host-dry-run")
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

    def test_requests_for_an_old_main_are_superseded(self) -> None:
        identifier = self.h.file()["id"]
        self.h.woodpecker.main_commit.return_value = "b" * 40
        [request] = self.h.service.recent(110)
        self.assertEqual(request.state, "superseded")
        self.assertIn("bbbbbbbbbbbb", request.error)
        with self.assertRaisesRegex(ApprovalError, "not pending"):
            self.h.service.request(identifier, 111)
            self.h.service.approval_options(identifier, 111)
        self.h.woodpecker.deploy.assert_not_called()

    def test_an_unknown_main_leaves_requests_pending(self) -> None:
        identifier = self.h.file()["id"]
        self.h.woodpecker.main_commit.return_value = None
        self.assertEqual(self.h.service.request(identifier, 110).state, "pending")

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


class GrantFlowTest(unittest.TestCase):
    DIGEST = "d" * 64

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.h = Harness(Path(self.directory.name))

    def tearDown(self) -> None:
        self.h.store.close()
        self.directory.cleanup()

    def file(self, digest: str = DIGEST, now: float = 100) -> dict:
        return self.h.service.file(
            "agent-token",
            {
                "operation": "deploy-ci-host",
                "parent": 40,
                "reason": "ship",
                "digest": digest,
                "paths": ["scripts/ci/policy.py", ".sops.yaml"],
            },
            now,
        )

    def test_grant_approval_triggers_nothing_and_survives_main_moving(self) -> None:
        filed = self.file()
        self.assertEqual(filed["paths"], ["scripts/ci/policy.py", ".sops.yaml"])
        self.assertEqual(self.file()["id"], filed["id"])
        self.h.woodpecker.main_commit.return_value = "b" * 40
        self.assertEqual(self.h.service.request(filed["id"], 105).state, "pending")
        options = json.loads(self.h.service.approval_options(filed["id"], 110))
        approved = self.h.service.approve(
            filed["id"], self.h.passkey.assert_(options), 110
        )
        self.assertEqual(approved.state, "approved")
        self.assertIsNone(approved.pipeline)
        self.h.woodpecker.deploy.assert_not_called()
        self.assertTrue(self.h.store.granted(self.DIGEST, 120))
        self.assertFalse(self.h.store.granted("e" * 64, 120))
        self.assertFalse(
            self.h.store.granted(self.DIGEST, 110 + core.GRANT_TTL_SECONDS + 1)
        )

    def test_pending_or_declined_grants_do_not_count(self) -> None:
        filed = self.file()
        self.assertFalse(self.h.store.granted(self.DIGEST, 101))
        self.h.service.decline(filed["id"], 102)
        self.assertFalse(self.h.store.granted(self.DIGEST, 103))

    def test_malformed_grants_are_refused(self) -> None:
        for digest in ["short", "D" * 64, 7]:
            with self.subTest(digest=digest), self.assertRaises(ApprovalError):
                self.file(digest)  # type: ignore[arg-type]


POLICY_BLOB = "100644 " + "1" * 40
OTHER_BLOB = "100644 " + "2" * 40


class MergeFlowTest(unittest.TestCase):
    HEAD = "c" * 40

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.h = Harness(Path(self.directory.name))

    def tearDown(self) -> None:
        self.h.store.close()
        self.directory.cleanup()

    def file(
        self, head: str = HEAD, files: dict | None = None, now: float = 100
    ) -> dict:
        return self.h.service.file_merge(
            "policy-token",
            {
                "pull": 12,
                "head": head,
                "files": {"scripts/ci/policy.py": POLICY_BLOB}
                if files is None
                else files,
                "findings": ["[authority] policy: scripts/ci/policy.py"],
            },
            now,
        )

    def approve(self, identifier: str, now: float = 110) -> core.Request:
        options = json.loads(self.h.service.approval_options(identifier, now))
        return self.h.service.approve(identifier, self.h.passkey.assert_(options), now)

    def grant(self, files: dict, now: float = 200, digest: str | None = None) -> dict:
        return self.h.service.file(
            "agent-token",
            {
                "operation": "deploy-ci-host",
                "parent": 40,
                "digest": digest or authority_digest(list(files), files),
                "paths": list(files),
                "files": files,
            },
            now,
        )

    def test_the_policy_files_once_per_head_and_a_new_head_supersedes(self) -> None:
        with mock.patch.object(server, "notify") as notify:
            first = self.file()
            self.assertEqual(self.file()["id"], first["id"])
            notify.assert_called_once()
            newer = self.file("d" * 40)
        self.assertEqual(first["title"], "merge PR #12")
        self.assertEqual(first["kind"], "merge")
        self.assertEqual(first["files"], {"scripts/ci/policy.py": POLICY_BLOB})
        self.assertTrue(first["pull_url"].endswith("/pulls/12/files"))
        self.assertEqual(self.h.store.get(first["id"], 101).state, "superseded")
        self.assertEqual(newer["state"], "pending")

    def test_only_the_merge_policy_may_file(self) -> None:
        self.h.forgejo.identity.return_value = "homelab-agent"
        with self.assertRaisesRegex(ApprovalError, "may not file"):
            self.file()
        self.h.forgejo.identity.return_value = "homelab-policy"
        for bad in (
            {"head": "short"},
            {"pull": "12"},
            {"files": {"x": "100644 nothex"}},
            {"files": {"a\nb": POLICY_BLOB}},
            {"findings": "text"},
        ):
            body = {"pull": 12, "head": self.HEAD, **bad}
            with self.subTest(bad=bad), self.assertRaises(ApprovalError):
                self.h.service.file_merge("policy-token", body, 100)

    def test_approving_a_merge_triggers_nothing_and_lists_the_head(self) -> None:
        identifier = self.file()["id"]
        self.assertEqual(self.h.service.merges(12, 105)["approved"], [])
        self.assertEqual(self.approve(identifier).state, "approved")
        self.h.woodpecker.deploy.assert_not_called()
        self.assertEqual(self.h.service.merges(12, 120)["approved"], [self.HEAD])
        self.assertEqual(
            self.file()["id"], identifier, "an approved head is not filed again"
        )

    def test_merge_requests_outlive_deployment_expiry_and_main_moving(self) -> None:
        identifier = self.file()["id"]
        self.h.woodpecker.main_commit.return_value = "b" * 40
        later = 100 + core.REQUEST_TTL_SECONDS + 1
        self.assertEqual(self.h.service.request(identifier, later).state, "pending")
        expired = 100 + core.MERGE_TTL_SECONDS + 1
        self.assertEqual(self.h.store.get(identifier, expired).state, "expired")

    def test_an_approved_merge_approves_the_grant_for_its_exact_files(self) -> None:
        self.approve(self.file()["id"])
        with mock.patch.object(server, "notify") as notify:
            granted = self.grant({"scripts/ci/policy.py": POLICY_BLOB})
        notify.assert_not_called()
        self.assertEqual(granted["state"], "approved")
        self.assertIn(f"PR #12 {self.HEAD[:12]}", granted["decided_by"])
        self.assertTrue(self.h.store.granted(granted["digest"], 210))

    def test_grants_beyond_the_approved_files_still_need_a_passkey(self) -> None:
        self.approve(self.file()["id"])
        for files in (
            {"scripts/ci/policy.py": OTHER_BLOB},
            {"scripts/ci/policy.py": POLICY_BLOB, ".sops.yaml": OTHER_BLOB},
        ):
            with self.subTest(files=files):
                self.assertEqual(self.grant(files)["state"], "pending")

    def test_pending_or_declined_merges_cover_nothing(self) -> None:
        identifier = self.file()["id"]
        files = {"scripts/ci/policy.py": POLICY_BLOB}
        self.assertEqual(self.grant(files)["state"], "pending")
        self.h.service.decline(identifier, 150)
        self.assertEqual(self.grant(files, now=160)["state"], "pending")

    def test_files_must_reproduce_the_grant_digest(self) -> None:
        self.approve(self.file()["id"])
        with self.assertRaisesRegex(ApprovalError, "do not match"):
            self.grant({"scripts/ci/policy.py": POLICY_BLOB}, digest="e" * 64)

    def test_the_digest_matches_the_ci_policy(self) -> None:
        files = {"b": POLICY_BLOB, "a": "absent"}
        self.assertEqual(
            hashlib.sha256(core.format_files(files).encode()).hexdigest(),
            authority_digest(["b", "a"], files),
        )

    def test_existing_requests_are_classified_on_upgrade(self) -> None:
        path = Path(self.directory.name) / "old.sqlite"
        db = sqlite3.connect(path)
        db.executescript(
            core.SCHEMA.replace(",\n  kind TEXT,\n  files TEXT", "").split(
                "CREATE TABLE IF NOT EXISTS credentials"
            )[0]
        )
        for identifier, digest in (("deploy", None), ("grant", "d" * 64)):
            db.execute(
                "INSERT INTO requests (id, operation, parent, commit_sha, requester, "
                "reason, evidence, state, created, digest) "
                "VALUES (?, 'deploy-ci-host', 1, 'x', 'a', '', '', 'pending', 0, ?)",
                (identifier, digest),
            )
        db.commit()
        db.close()
        store = Store(path)
        self.addCleanup(store.close)
        self.assertEqual(store.get("deploy", 0).kind, "deploy")
        self.assertEqual(store.get("grant", 0).kind, "grant")


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

    def test_grant_lookup_reports_only_approved_digests(self) -> None:
        digest = "d" * 64
        status, _, body = self.call("GET", f"/api/grants/{digest}")
        self.assertEqual((status, json.loads(body)), (200, {"approved": False}))
        identifier, _ = self.h.store.create(
            "deploy-ci-host", 40, "a" * 40, "homelab-agent", "", [], 100, digest, ["x"]
        )
        self.h.store.set_challenge(identifier.id, b"c", 100)
        self.h.store.claim(identifier.id, 100)
        self.h.store.finish(identifier.id, "approved", time.time(), "passkey")
        status, _, body = self.call("GET", f"/api/grants/{digest}")
        self.assertEqual((status, json.loads(body)), (200, {"approved": True}))
        status, _, _ = self.call("GET", "/api/grants/not-a-digest")
        self.assertEqual(status, 404)

    def test_the_policy_files_merges_with_its_forgejo_token(self) -> None:
        body = {"pull": 12, "head": "c" * 40, "findings": ["x"]}
        status, _, response = self.call("POST", "/api/merges", body=body)
        self.assertEqual(status, 400)
        self.assertIn(b"Forgejo bearer", response)
        status, _, response = self.call(
            "POST", "/api/merges", {"Authorization": "Bearer policy-token"}, body
        )
        self.assertEqual(status, 201)
        self.h.forgejo.identity.assert_called_with("policy-token", mock.ANY)
        status, _, response = self.call("GET", "/api/merges/12")
        found = json.loads(response)
        self.assertEqual(found["approved"], [])
        self.assertEqual([r["commit_sha"] for r in found["requests"]], ["c" * 40])

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
