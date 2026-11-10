from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.ci import kube_credentials as credentials


def token(**overrides: object) -> str:
    now = int(time.time())
    claims = {"sub": credentials.SUBJECT, "iat": now, "exp": now + 3600, **overrides}
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{encoded}.signature"


class KubeCredentials(unittest.TestCase):
    def test_malformed_claims_fail_closed(self) -> None:
        encoded = base64.urlsafe_b64encode(b"[]").decode().rstrip("=")
        for malformed in (
            "not-a-token",
            f"header.{encoded}.signature",
            token() + ".extra",
        ):
            with self.subTest(token=malformed), self.assertRaises(ValueError):
                credentials.validate_token(malformed)

    def test_server_url_cannot_embed_credentials_or_additional_request_parts(
        self,
    ) -> None:
        for server in (
            "http://10.0.30.11:6443",
            "https://user:password@10.0.30.11:6443",
            "https://10.0.30.11:6443/api",
            "https://10.0.30.11:6443?token=untrusted",
            "https://10.0.30.11:6443#fragment",
        ):
            with self.subTest(server=server), self.assertRaises(ValueError):
                credentials.configuration(
                    token(), b"-----BEGIN CERTIFICATE-----\nca", server
                )

    def test_private_issuer_bundle_uses_only_existing_operator_and_ci_recipients(
        self,
    ) -> None:
        encrypted = json.loads(
            (credentials.ROOT / "secrets/ha-ci-issuer.sops.json").read_text()
        )
        self.assertTrue(encrypted["private_key"].startswith("ENC["))
        self.assertEqual(
            {entry["recipient"] for entry in encrypted["sops"]["age"]},
            {
                "age1k2vhn663mmw9ancuwm2hfmtfg4xyus9xlvp8rkep4fpvxlqa9gdsms9usl",
                "age1ckzw40ndw6v98ctczzw99lv7k257h4tx7xq6k9f0f5mltq9z9gtq36achw",
                "age1z65e9nyzje93st4ms247d6604fgux2fmc5dxzt6daks40tp0ze3qqvv7jq",
            },
        )

    def test_expired_overlong_and_wrong_identity_tokens_are_rejected(self) -> None:
        now = int(time.time())
        for overrides in (
            {"exp": now - 1},
            {"exp": now + 7200},
            {"sub": "system:serviceaccount:kube-system:admin"},
            {"iat": now + 1800},
            {"exp": "tomorrow"},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                credentials.validate_token(token(**overrides))

    def test_fixed_issuer_requests_only_one_ha_identity(self) -> None:
        fake = subprocess.CompletedProcess([], 0, token(), "")
        with (
            patch.object(credentials.os, "geteuid", return_value=0),
            patch.object(credentials.subprocess, "run", return_value=fake) as run,
            patch.object(
                Path, "read_bytes", return_value=b"-----BEGIN CERTIFICATE-----\nca"
            ),
        ):
            config = credentials.issue("https://10.0.30.11:6443")
        self.assertEqual(
            run.call_args.args[0],
            [
                "kubectl",
                "--kubeconfig=/etc/rancher/k3s/k3s.yaml",
                "--request-timeout=20s",
                "--namespace=home-assistant",
                "create",
                "token",
                "ha-deployer",
                "--duration=1h",
            ],
        )
        self.assertEqual(
            config["contexts"][0]["context"]["namespace"], "home-assistant"
        )
        self.assertNotIn("client-key-data", config["users"][0]["user"])

    def test_issue_failure_does_not_expose_api_output(self) -> None:
        fake = subprocess.CompletedProcess(
            [], 1, "private-api-response", "private-diagnostic"
        )
        with (
            patch.object(credentials.os, "geteuid", return_value=0),
            patch.object(credentials.subprocess, "run", return_value=fake),
            self.assertRaisesRegex(RuntimeError, "refused the scoped") as failure,
        ):
            credentials.issue("https://10.0.30.11:6443")
        self.assertNotIn("private", str(failure.exception))

    def test_fetch_pins_server_and_writes_only_private_token_file(self) -> None:
        config = credentials.configuration(
            token(), b"-----BEGIN CERTIFICATE-----\nca", "https://10.0.30.11:6443"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            key, known, out = root / "key", root / "known", root / "config"
            key.write_text("key")
            key.chmod(0o600)
            known.write_text("pinned")
            fake = subprocess.CompletedProcess([], 0, json.dumps(config), "")
            with patch.object(credentials.subprocess, "run", return_value=fake) as run:
                credentials.fetch(key, known, out, "https://10.0.30.11:6443")
            self.assertEqual(out.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(out.read_text()), config)
            args = run.call_args.args[0]
            self.assertIn("StrictHostKeyChecking=yes", args)
            self.assertIn("IdentityAgent=none", args)
            self.assertEqual(args[-1], "kube-ci-ha@10.0.30.11")
            self.assertNotIn("issue", args)

    def test_fetch_refuses_exec_auth_and_leaves_previous_file_intact(self) -> None:
        config = credentials.configuration(
            token(), b"-----BEGIN CERTIFICATE-----\nca", "https://10.0.30.11:6443"
        )
        config["users"][0]["user"]["exec"] = {"command": "untrusted"}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            key, known, out = root / "key", root / "known", root / "config"
            key.write_text("key")
            key.chmod(0o600)
            known.touch()
            out.write_text("previous")
            fake = subprocess.CompletedProcess([], 0, json.dumps(config), "")
            with (
                patch.object(credentials.subprocess, "run", return_value=fake),
                self.assertRaises(ValueError),
            ):
                credentials.fetch(key, known, out, "https://10.0.30.11:6443")
            self.assertEqual(out.read_text(), "previous")

    def test_world_readable_key_is_rejected_before_ssh(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            key = Path(temporary) / "key"
            key.touch(mode=0o644)
            with (
                patch.object(credentials.subprocess, "run") as run,
                self.assertRaises(ValueError),
            ):
                credentials.fetch(key, key, key, "https://10.0.30.11:6443")
            run.assert_not_called()

    def test_provision_refuses_unreviewed_or_existing_identity(self) -> None:
        with (
            patch.dict(os.environ, {"HOMELAB_AUTHORIZED": "1"}),
            patch.object(
                credentials, "provisioning_plan", return_value={"sha256": "new"}
            ),
            patch.object(credentials, "request") as request,
            self.assertRaises(ValueError),
        ):
            credentials.provision("auth", Path("unused"), "old")
        request.assert_not_called()
        with (
            patch.dict(os.environ, {"HOMELAB_AUTHORIZED": "1"}),
            patch.object(
                credentials, "provisioning_plan", return_value={"sha256": "same"}
            ),
            patch.object(
                credentials, "request", return_value={"name": credentials.NAME}
            ) as request,
            self.assertRaises(ValueError),
        ):
            credentials.provision("auth", Path("unused"), "same")
        self.assertEqual(
            request.call_args.args[:2], ("GET", f"/secrets/{credentials.NAME}")
        )


if __name__ == "__main__":
    unittest.main()
