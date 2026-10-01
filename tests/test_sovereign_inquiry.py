"""Offline tests for the Sovereign pilot inquiry receiver.

Extracts inquiry.py from gitops/websites/sovereign/inquiry-script.yaml,
executes it, then drives the real HTTP handler over loopback TCP with a
temporary SQLite database. No cluster, image build, or ntfy access required.
"""

from __future__ import annotations

import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_MAP = (
    REPO_ROOT / "gitops" / "websites" / "sovereign" / "inquiry-script.yaml"
)
KUSTOMIZATION = REPO_ROOT / "gitops" / "websites" / "sovereign" / "kustomization.yaml"
INGRESS_CONFIG = REPO_ROOT / "gitops" / "cloudflare" / "ingress-config.yaml"

TMP = tempfile.TemporaryDirectory(prefix="sovereign-inquiry-test-")
os.environ["DB_PATH"] = os.path.join(TMP.name, "inquiries.db")
os.environ.setdefault("NTFY_TOKEN", "")


def load_receiver() -> types.ModuleType:
    manifest = yaml.safe_load(SCRIPT_MAP.read_text())
    code = manifest["data"]["inquiry.py"]
    module = types.ModuleType("sovereign_inquiry_under_test")
    module.__file__ = str(SCRIPT_MAP)
    sys.modules[module.__name__] = module
    exec(compile(code, str(SCRIPT_MAP), "exec"), module.__dict__)  # noqa: S102
    return module


RECEIVER = load_receiver()
RECEIVER.init_db(os.environ["DB_PATH"])
SERVER = ThreadingHTTPServer(("127.0.0.1", 0), RECEIVER.Handler)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
PORT = SERVER.server_address[1]


def request(
    method: str,
    path: str,
    body: object = None,
    content_type: str = "application/json",
    forwarded_for: str | None = None,
) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    headers: dict[str, str] = {}
    if forwarded_for:
        headers["X-Forwarded-For"] = forwarded_for
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = content_type
        headers["Content-Length"] = str(len(data))
    connection.request(method, path, body=data, headers=headers)
    response = connection.getresponse()
    raw = response.read().decode("utf-8")
    return response.status, json.loads(raw) if raw else {}


def valid_payload(**overrides: object) -> dict:
    payload: dict = {
        "business": "Acme Engineering",
        "role": "Business owner / decision maker",
        "workload": "Private AI over our docs.",
    }
    payload.update(overrides)
    return payload


def row_count() -> int:
    with sqlite3.connect(os.environ["DB_PATH"]) as db:
        return int(db.execute("SELECT COUNT(*) FROM inquiries").fetchone()[0])


class InquiryReceiverTest(unittest.TestCase):
    def test_healthz(self) -> None:
        status, payload = request("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"ok": True})

    def test_unknown_paths_404(self) -> None:
        status, _ = request("GET", "/api/nope")
        self.assertEqual(status, 404)
        status, _ = request("POST", "/api/nope", valid_payload())
        self.assertEqual(status, 404)

    def test_valid_submission_is_stored(self) -> None:
        before = row_count()
        status, payload = request(
            "POST", "/api/inquiries", valid_payload(), forwarded_for="test-store-1"
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(row_count(), before + 1)

    def test_missing_and_oversize_fields_rejected(self) -> None:
        status, payload = request(
            "POST", "/api/inquiries", {"business": "Acme"}, forwarded_for="test-400-1"
        )
        self.assertEqual(status, 400)
        self.assertIn("error", payload)
        oversize = valid_payload(workload="x" * 1501)
        status, _ = request(
            "POST", "/api/inquiries", oversize, forwarded_for="test-400-2"
        )
        self.assertEqual(status, 400)

    def test_rejects_non_json(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
        connection.request(
            "POST",
            "/api/inquiries",
            body=b"not json",
            headers={"Content-Type": "application/json", "Content-Length": "8"},
        )
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 400)
        status, _ = request(
            "POST", "/api/inquiries", valid_payload(), content_type="text/plain",
            forwarded_for="test-415-1",
        )
        self.assertEqual(status, 415)

    def test_honeypot_gets_fake_success_without_storage(self) -> None:
        before = row_count()
        status, payload = request(
            "POST",
            "/api/inquiries",
            valid_payload(website="http://spam.example"),
            forwarded_for="test-honeypot-1",
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertNotIn("id", payload)
        self.assertEqual(row_count(), before)

    def test_rate_limit(self) -> None:
        for _ in range(5):
            status, _ = request(
                "POST", "/api/inquiries", valid_payload(), forwarded_for="test-limit-1"
            )
            self.assertEqual(status, 200)
        status, payload = request(
            "POST", "/api/inquiries", valid_payload(), forwarded_for="test-limit-1"
        )
        self.assertEqual(status, 429)
        self.assertIn("error", payload)


class InquiryWiringTest(unittest.TestCase):
    def test_kustomization_includes_receiver(self) -> None:
        kust = yaml.safe_load(KUSTOMIZATION.read_text())
        self.assertIn("inquiry-script.yaml", kust["resources"])
        self.assertIn("inquiry.yaml", kust["resources"])

    def test_tunnel_routes_api_path_before_site(self) -> None:
        config = yaml.safe_load(INGRESS_CONFIG.read_text())
        ingress = yaml.safe_load(config["data"]["config.yaml"])["ingress"]
        api_index = site_index = -1
        for index, rule in enumerate(ingress):
            if rule.get("hostname") != "sovereign.rupan.dev":
                continue
            if "path" in rule:
                self.assertIn("inquiries", rule["path"])
                self.assertEqual(
                    rule["service"], "http://sovereign-inquiry-svc.sovereign:80"
                )
                api_index = index
            else:
                site_index = index
        self.assertNotEqual(api_index, -1, "no API path rule for sovereign.rupan.dev")
        self.assertNotEqual(site_index, -1, "site rule for sovereign.rupan.dev is gone")
        self.assertLess(api_index, site_index)


if __name__ == "__main__":
    unittest.main()
