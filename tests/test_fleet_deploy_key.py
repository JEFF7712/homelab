from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.ci import fleet_deploy_key as fleet


class FleetDeployKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "config/ci").mkdir(parents=True)
        (self.root / "secrets").mkdir()
        (self.root / "inventory").mkdir()
        self.key = self.root / "key"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.key)],
            check=True,
            capture_output=True,
        )
        self.private = self.key.read_bytes()
        self.public = self.key.with_suffix(".pub").read_text()
        (self.root / "config/ci/fleet-deploy.pub").write_text(self.public)
        (self.root / "secrets/ci-fleet-deploy.sops.json").write_text("ciphertext")
        (self.root / "inventory/fleet.json").write_text(
            json.dumps(
                {"hosts": {"server": {"ip": "10.0.30.11"}, "ci": {"ip": "10.0.30.14"}}}
            )
        )
        self.current = {
            "id": 27,
            "repo_id": 1,
            "org_id": 0,
            "name": fleet.SOURCE_NAME,
            "events": ["cron", "deployment"],
            "images": [],
            "note": "preserve",
        }
        self.plan = {
            "metadata_sha256": fleet.digest(self.current),
            "ciphertext_sha256": hashlib.sha256(b"ciphertext").hexdigest(),
            "public_fingerprint": fleet.public_identity(self.root)[1],
        }

    def test_plan_binds_existing_scopes_and_encrypted_identity(self) -> None:
        def request(method, path, auth, body=None, **kwargs):
            if path == fleet.REPOSITORY:
                return {"full_name": "JEFF7712/homelab"}
            if path.endswith("/" + fleet.NAME):
                return None
            return self.current

        with patch.object(fleet, "request", side_effect=request) as api:
            self.assertEqual(fleet.inspect("auth", self.root), self.plan)
        self.assertTrue(all(call.args[0] == "GET" for call in api.call_args_list))

    def test_plan_refuses_to_overwrite_existing_dedicated_secret(self) -> None:
        with (
            patch.object(fleet, "source_metadata", return_value=self.current),
            patch.object(fleet, "request", return_value={"id": 99}),
        ):
            with self.assertRaisesRegex(ValueError, "already exists"):
                fleet.inspect("auth", self.root)

    def test_metadata_refuses_untrusted_or_unscoped_secret(self) -> None:
        for changed in (
            {"repo_id": 2},
            {"name": "other"},
            {"events": []},
            {"events": ["pull_request"]},
            {"events": ["push"]},
            {"images": None},
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                fleet.metadata(dict(self.current, **changed))

    def test_key_pair_validates_in_anonymous_memory_and_uses_pinned_hosts(self) -> None:
        known = self.root / "known_hosts"
        known.write_text("pinned")
        real_run = subprocess.run

        def run(argv, **kwargs):
            self.assertNotIn("pass_fds", kwargs)
            if argv[0] == "ssh-keygen":
                self.assertTrue(argv[-1].startswith(f"/proc/{os.getpid()}/fd/"))
                return real_run(argv, **kwargs)
            key_path = argv[argv.index("-i") + 1]
            config = real_run(
                ["ssh", "-F", "/dev/null", "-G", "-i", key_path, "localhost"],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertNotIn("not accessible", config.stderr)
            self.assertIn(f"identityfile {key_path}", config.stdout)
            self.assertIn("StrictHostKeyChecking=yes", argv)
            self.assertIn("IdentityAgent=none", argv)
            self.assertIn("IdentitiesOnly=yes", argv)
            self.assertEqual(argv[-1], "sudo -n true")
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        with patch.object(fleet.subprocess, "run", side_effect=run) as execute:
            fleet.verify_hosts(self.root, self.private, known)
            self.assertEqual(execute.call_count, 3)

    def test_key_or_host_rejection_prevents_secret_creation(self) -> None:
        with (
            patch.dict(os.environ, HOMELAB_AUTHORIZED="1"),
            patch.object(fleet, "inspect", return_value=self.plan),
            patch.object(
                fleet, "verify_hosts", side_effect=RuntimeError("host refused")
            ),
            patch.object(fleet, "request") as api,
        ):
            with self.assertRaisesRegex(RuntimeError, "host refused"):
                fleet.apply("auth", self.root, self.plan, self.root / "known", self.key)
            api.assert_not_called()

    def test_create_uses_new_name_preserves_scopes_and_leaves_old_secret_untouched(
        self,
    ) -> None:
        created = dict(self.current, id=99, name=fleet.NAME)

        def request(method, path, auth, body=None, **kwargs):
            if method == "POST":
                self.assertEqual(path, fleet.REPOSITORY + "/secrets")
                self.assertEqual(body["name"], fleet.NAME)
                self.assertEqual(body["value"], self.private.decode())
                for key in ("events", "images", "note"):
                    self.assertEqual(body[key], self.current[key])
                return created
            return created

        with (
            patch.dict(os.environ, HOMELAB_AUTHORIZED="1"),
            patch.object(fleet, "inspect", return_value=self.plan),
            patch.object(fleet, "source_metadata", return_value=self.current),
            patch.object(fleet, "verify_hosts"),
            patch.object(fleet, "request", side_effect=request) as api,
        ):
            fleet.apply("auth", self.root, self.plan, self.root / "known", self.key)
        self.assertEqual([c.args[0] for c in api.call_args_list], ["POST", "GET"])
        self.assertTrue(
            all(
                not c.args[1].endswith("/" + fleet.SOURCE_NAME)
                for c in api.call_args_list
            )
        )

    def test_scope_race_prevents_creation(self) -> None:
        changed = dict(self.plan, metadata_sha256="changed")
        with (
            patch.dict(os.environ, HOMELAB_AUTHORIZED="1"),
            patch.object(fleet, "inspect", side_effect=[self.plan, changed]),
            patch.object(fleet, "verify_hosts"),
            patch.object(fleet, "request") as api,
        ):
            with self.assertRaisesRegex(ValueError, "changed during host verification"):
                fleet.apply("auth", self.root, self.plan, self.root / "known", self.key)
            api.assert_not_called()

    def test_uncertain_create_retains_old_secret_for_rollback(self) -> None:
        with (
            patch.dict(os.environ, HOMELAB_AUTHORIZED="1"),
            patch.object(fleet, "inspect", return_value=self.plan),
            patch.object(fleet, "source_metadata", return_value=self.current),
            patch.object(fleet, "verify_hosts"),
            patch.object(fleet, "request", side_effect=RuntimeError("timeout")) as api,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "Original CI secret is untouched"
            ):
                fleet.apply("auth", self.root, self.plan, self.root / "known", self.key)
            self.assertEqual([c.args[0] for c in api.call_args_list], ["POST"])

    def test_mutation_requires_authorized_environment(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "HOMELAB_AUTHORIZED"):
                fleet.apply("auth", self.root, self.plan, self.root / "known", self.key)

    def test_committed_private_material_has_only_existing_recovery_and_ci_recipients(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        bundle = json.loads((root / "secrets/ci-fleet-deploy.sops.json").read_text())
        self.assertTrue(bundle["private_key"].startswith("ENC["))
        self.assertEqual(
            {entry["recipient"] for entry in bundle["sops"]["age"]},
            {
                "age1k2vhn663mmw9ancuwm2hfmtfg4xyus9xlvp8rkep4fpvxlqa9gdsms9usl",
                "age1ckzw40ndw6v98ctczzw99lv7k257h4tx7xq6k9f0f5mltq9z9gtq36achw",
                "age1z65e9nyzje93st4ms247d6604fgux2fmc5dxzt6daks40tp0ze3qqvv7jq",
            },
        )
