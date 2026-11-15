from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.ci import kube_credentials as credentials
from tests.test_kube_credentials import token


class RegistryCiCredentials(unittest.TestCase):
    def test_registry_capability_requests_only_existing_read_only_identity(
        self,
    ) -> None:
        fake = subprocess.CompletedProcess(
            [], 0, token(sub=credentials.subject("registry-advance")), ""
        )
        with (
            patch.object(credentials.os, "geteuid", return_value=0),
            patch.object(credentials.subprocess, "run", return_value=fake) as run,
            patch.object(
                Path, "read_bytes", return_value=b"-----BEGIN CERTIFICATE-----\nca"
            ),
        ):
            config = credentials.issue(
                "https://10.0.30.11:6443", profile="registry-advance"
            )
        args = run.call_args.args[0]
        self.assertEqual(
            args[-5:],
            [
                "--namespace=automation",
                "create",
                "token",
                "registry-advance",
                "--duration=1h",
            ],
        )
        self.assertEqual(config["contexts"][0]["context"]["namespace"], "automation")
        self.assertEqual(config["users"][0]["name"], "registry-advance")

    def test_profiles_cannot_accept_each_others_tokens(self) -> None:
        ha = token()
        registry = token(sub=credentials.subject("registry-advance"))
        for profile, wrong in (("ha", registry), ("registry-advance", ha)):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                credentials.validate_token(wrong, profile=profile)
        with self.assertRaises(ValueError):
            credentials.profile_settings("kube-system/admin")

    def test_fetch_uses_separate_account_and_rejects_a_different_profile(self) -> None:
        ha = credentials.configuration(
            token(), b"-----BEGIN CERTIFICATE-----\nca", "https://10.0.30.11:6443"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key, known, output = root / "key", root / "known-hosts", root / "kubeconfig"
            key.touch(mode=0o600)
            known.touch()
            output.write_text("previous-config")
            with (
                patch.object(
                    credentials.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0, json.dumps(ha), ""),
                ) as run,
                self.assertRaises(ValueError),
            ):
                credentials.fetch(
                    key,
                    known,
                    output,
                    "https://10.0.30.11:6443",
                    profile="registry-advance",
                )
            self.assertEqual(run.call_args.args[0][-1], "kube-ci-registry@10.0.30.11")
            self.assertEqual(output.read_text(), "previous-config")

    def test_provision_plan_preserves_existing_registry_event_and_image_scope(
        self,
    ) -> None:
        old = {
            "repo_id": 1,
            "name": "registry_advance_kubeconfig",
            "events": ["cron", "deployment"],
            "images": [],
        }
        with patch.object(
            credentials, "request", side_effect=[{"full_name": "JEFF7712/homelab"}, old]
        ) as request:
            plan = credentials.provisioning_plan("auth", profile="registry-advance")
        self.assertEqual(
            request.call_args.args[:2], ("GET", "/secrets/registry_advance_kubeconfig")
        )
        self.assertEqual(plan["metadata"]["name"], "registry_issuer_ssh_key")
        self.assertEqual(plan["metadata"]["events"], old["events"])
        self.assertEqual(plan["metadata"]["images"], old["images"])

    def test_new_private_identity_has_only_existing_operator_and_ci_recipients(
        self,
    ) -> None:
        root = credentials.ROOT
        ha = json.loads((root / "secrets/ha-ci-issuer.sops.json").read_text())
        registry = json.loads(
            (root / "secrets/registry-ci-issuer.sops.json").read_text()
        )
        self.assertTrue(registry["private_key"].startswith("ENC["))
        self.assertEqual(
            {e["recipient"] for e in registry["sops"]["age"]},
            {e["recipient"] for e in ha["sops"]["age"]},
        )
        self.assertNotEqual(
            (root / "config/ci/registry-issuer.pub").read_text(),
            (root / "config/ci/ha-issuer.pub").read_text(),
        )
