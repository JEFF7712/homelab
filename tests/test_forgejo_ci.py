from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import shutil
import sqlite3
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.ci.authority import select
from scripts.ci.dr_mirror import mirror, refs
from scripts.ci.operation import pack, unpack
from scripts.ci.platform_backup import snapshot_database
from scripts.ci.policy import VALIDATION, configuration, verify_request
from scripts.ci.prepare_platform import certificate
from scripts.ci.state import Store, handler

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


class GitLabRecoveryGraphTest(unittest.TestCase):
    def test_apply_waits_for_both_plans_in_its_own_pipeline(self) -> None:
        workflow = yaml.safe_load((ROOT / "config/ci/gitlab-dr.yml").read_text())
        self.assertNotIn("environment", workflow["validate"])
        self.assertEqual(
            set(workflow["operate"]["needs"]),
            {"validate", "opnsense-plan", "cloudflare-plan"},
        )
        for name in ["opnsense-plan", "cloudflare-plan"]:
            job = workflow[name]
            self.assertEqual(job["stage"], "plan")
            self.assertEqual(job["extends"], ".production-operation")
            self.assertTrue(job["script"][0].startswith(f"CI_OPERATION={name} "))
            self.assertEqual(job["rules"][-1], {"when": "never"})
            self.assertIn("CI_COMMIT_REF_PROTECTED", job["rules"][0]["if"])


class PolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = json.loads((ROOT / "config/ci/operations.json").read_text())
        self.request = {
            "repo": {"id": 1, "owner": "JEFF7712", "name": "homelab"},
            "pipeline": {
                "event": "push",
                "ref": "refs/heads/main",
                "commit": SHA,
                "author": "rupan",
                "parent": 1,
            },
        }
        self.parent = {
            "number": 1,
            "event": "push",
            "ref": "refs/heads/main",
            "status": "success",
            "commit": SHA,
            "finished": int(time.time()),
            "workflows": [{"name": VALIDATION, "state": "success"}],
        }

    def test_repository_yaml_cannot_select_host_or_privileged_execution(self) -> None:
        self.request["configuration"] = [
            {
                "name": "attack",
                "data": "labels: {tier: deploy}\nsteps: [{privileged: true}]",
            }
        ]
        for event in ["push", "pull_request", "tag", "manual", "cron"]:
            self.request["pipeline"]["event"] = event
            workflow = yaml.safe_load(
                configuration(self.request, self.catalog)["configs"][0]["data"]
            )
            self.assertEqual(workflow["labels"], {"tier": "sandbox", "type": "docker"})
            self.assertNotIn("CI_LINT_EXTERNAL", workflow["steps"][0]["environment"])
            self.assertEqual(
                workflow["steps"][0]["commands"][-1],
                "nix develop ./flake -c just check",
            )
            self.assertNotIn("from_secret", json.dumps(workflow))

    def deployment(self) -> dict:
        self.request["pipeline"].update(event="deployment", deploy_to="opnsense-apply")
        return configuration(self.request, self.catalog, self.parent, SHA)

    def test_pr_lifecycle_does_not_replace_mandatory_source_validation(self) -> None:
        for event in ["pull_request_closed", "pull_request_metadata"]:
            self.request["pipeline"]["event"] = event
            result = configuration(self.request, self.catalog, current=SHA)
            self.assertEqual(
                [item["name"] for item in result["configs"]], ["pr-lifecycle.yaml"]
            )
            workflow = yaml.safe_load(result["configs"][0]["data"])
            self.assertEqual(workflow["labels"], {"tier": "sandbox", "type": "docker"})
            self.assertNotEqual(workflow["steps"][0]["name"], VALIDATION)
            self.assertNotIn("from_secret", json.dumps(workflow))

    def test_only_current_main_push_gets_automatic_secret_workflows(self) -> None:
        result = configuration(self.request, self.catalog, current=SHA)
        self.assertEqual(
            [item["name"] for item in result["configs"]],
            [
                VALIDATION + ".yaml",
                "opnsense-plan.yaml",
                "cloudflare-plan.yaml",
                "sync-to-github.yaml",
                "cache-publish.yaml",
            ],
        )
        for item in result["configs"][1:]:
            workflow = yaml.safe_load(item["data"])
            self.assertEqual(workflow["depends_on"], [VALIDATION])
            self.assertEqual(workflow["concurrency"]["limit"], 1)
        for event in ["pull_request", "tag", "manual"]:
            self.request["pipeline"]["event"] = event
            self.assertEqual(
                len(configuration(self.request, self.catalog, current=SHA)["configs"]),
                1,
            )
        self.request["pipeline"].update(event="push", from_fork=True)
        self.assertEqual(
            len(configuration(self.request, self.catalog, current=SHA)["configs"]), 1
        )

    def test_deployment_requires_fresh_current_main_and_operator(self) -> None:
        result = self.deployment()
        workflow = yaml.safe_load(result["configs"][0]["data"])
        self.assertEqual(workflow["labels"], {"tier": "deploy", "type": "docker"})
        self.assertEqual(workflow["concurrency"]["group"], "production-authority")
        for field, value in [
            ("ref", "refs/heads/feature"),
            ("author", "attacker"),
            ("from_fork", True),
            ("deploy_to", "arbitrary-command"),
            ("variables", {"NIX_CONFIG": "sandbox = false"}),
        ]:
            saved = copy.deepcopy(self.request)
            self.request["pipeline"][field] = value
            with self.assertRaises(ValueError):
                configuration(self.request, self.catalog, self.parent, SHA)
            self.request = saved

    def test_old_or_incomplete_validation_cannot_authorize_deployment(self) -> None:
        for field, value in [
            ("status", "failure"),
            ("commit", "b" * 40),
            ("finished", 1),
            ("workflows", [{"name": "old-offline-checks", "state": "success"}]),
            ("event", "pull_request"),
        ]:
            parent = self.parent.copy()
            self.parent[field] = value
            with self.assertRaises(ValueError):
                self.deployment()
            self.parent = parent
        self.request["pipeline"].update(event="deployment", deploy_to="opnsense-apply")
        with self.assertRaises(ValueError):
            configuration(self.request, self.catalog, self.parent, "b" * 40)

    def test_catalog_commands_parse_and_predecessors_exist(self) -> None:
        import subprocess

        for operation in self.catalog.values():
            subprocess.run(
                ["bash", "-n"],
                input="\n".join(operation["commands"]),
                text=True,
                check=True,
            )
            for predecessor in operation["needs"]:
                self.assertIn(predecessor, self.catalog)
        for name in ["opnsense-apply", "cloudflare-apply"]:
            self.assertIn(
                self.catalog[name]["prerequisite"], self.catalog[name]["needs"]
            )
            self.assertNotIn("plan -out", "\n".join(self.catalog[name]["commands"]))

    def test_signatures_bind_body_target_and_freshness(self) -> None:
        private = Ed25519PrivateKey.generate()
        public = private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        body = json.dumps(self.request).encode()
        digest = (
            "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode() + ":"
        )
        parameters = '("@request-target" "content-digest");created=1000;alg="ed25519"'
        signed = f'"@request-target": /config\n"content-digest": {digest}\n"@signature-params": {parameters}'
        signature = base64.b64encode(private.sign(signed.encode())).decode()
        headers = {
            "Signature-Input": "woodpecker-ci-extensions=" + parameters,
            "Content-Digest": digest,
            "Signature": f"woodpecker-ci-extensions=:{signature}:",
        }
        verify_request(headers, body, public, 1000)
        for altered, now in [(body + b" ", 1000), (body, 1100)]:
            with self.assertRaises(ValueError):
                verify_request(headers, altered, public, now)


class StateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / "state.sqlite")
        self.original = json.dumps(
            {"serial": 3, "lineage": "preserved", "resources": []}
        ).encode()
        self.store.import_state("opnsense", self.original)
        self.lock = b'{"ID":"lock-1","Operation":"test"}'

    def request(
        self,
        method: str,
        *,
        lock: bool = False,
        user: str = "deploy",
        body: bytes = b"",
        lock_id: str = "",
    ) -> tuple[int, bytes]:
        return self.store.request(method, "opnsense", lock, user, user, body, lock_id)

    def test_plan_reads_and_locks_but_cannot_write_state(self) -> None:
        self.assertEqual(self.request("GET", user="plan"), (200, self.original))
        self.assertEqual(
            self.request("LOCK", user="plan", lock=True, body=self.lock)[0], 200
        )
        self.assertEqual(
            self.request("POST", user="plan", body=self.original, lock_id="lock-1")[0],
            403,
        )
        self.assertEqual(
            self.request("UNLOCK", user="plan", lock=True, body=self.lock)[0], 200
        )

    def test_state_writes_require_matching_owner_and_preserve_lineage(self) -> None:
        self.assertEqual(self.request("POST", body=self.original)[0], 409)
        self.request("LOCK", lock=True, body=self.lock)
        self.assertEqual(
            self.request("POST", body=self.original, lock_id="wrong")[0], 409
        )
        self.assertEqual(
            self.request("UNLOCK", user="plan", lock=True, body=self.lock)[0], 409
        )
        altered = json.dumps({"serial": 4, "lineage": "different"}).encode()
        self.assertEqual(self.request("POST", body=altered, lock_id="lock-1")[0], 409)
        updated = json.dumps(
            {"serial": 4, "lineage": "preserved", "resources": ["new"]}
        ).encode()
        self.assertEqual(self.request("POST", body=updated, lock_id="lock-1")[0], 200)
        self.assertEqual(self.request("GET")[1], updated)

    def test_concurrent_locks_have_exactly_one_winner_and_survive_restart(self) -> None:
        def lock(index: int) -> int:
            body = json.dumps({"ID": str(index)}).encode()
            return self.store.request(
                "LOCK", "opnsense", True, "deploy", "deploy", body, ""
            )[0]

        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(lock, range(8)))
        self.assertEqual(statuses.count(200), 1)
        self.assertEqual(statuses.count(423), 7)
        restored = Store(self.store.path)
        self.assertEqual(
            restored.request("LOCK", "opnsense", True, "plan", "plan", self.lock, "")[
                0
            ],
            423,
        )

    def test_artifacts_are_immutable_and_expire(self) -> None:
        self.assertEqual(
            self.store.artifact("POST", "source/plan", b"saved-plan")[0], 201
        )
        self.assertEqual(
            self.store.artifact("POST", "source/plan", b"tampered")[0], 409
        )
        self.assertEqual(
            self.store.artifact("GET", "source/plan", b"")[1], b"saved-plan"
        )
        with self.store.connection() as db:
            db.execute("UPDATE artifacts SET created=0")
        self.assertEqual(self.store.artifact("GET", "source/plan", b"")[0], 410)

    def test_sqlite_restore_preserves_exact_state_and_versions(self) -> None:
        target = Path(self.temporary.name) / "restore/state.sqlite"
        snapshot_database(self.store.path, target)
        restored = Store(target)
        self.assertEqual(
            restored.request("GET", "opnsense", False, "plan", "plan", b"", "")[1],
            self.original,
        )
        with closing(sqlite3.connect(target)) as db:
            self.assertEqual(
                db.execute("SELECT body FROM versions").fetchone()[0], self.original
            )
        with self.assertRaises(sqlite3.IntegrityError):
            restored.import_state("opnsense", self.original)


class ArtifactTest(unittest.TestCase):
    def test_roundtrip_and_reject_traversal_or_wrong_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tofu").mkdir()
            (root / "tofu/desired.tfplan").write_bytes(b"binary-plan")
            body = pack(root, ["tofu/desired.tfplan"], SHA, "plan")
            destination = root / "restored"
            destination.mkdir()
            unpack(destination, body, SHA, "plan", ["tofu/desired.tfplan"])
            self.assertEqual(
                (destination / "tofu/desired.tfplan").read_bytes(), b"binary-plan"
            )
            with self.assertRaises(ValueError):
                unpack(destination, body, "b" * 40, "plan", ["tofu/desired.tfplan"])
            attack = json.dumps(
                {"sha": SHA, "operation": "plan", "files": {"../escape": "eA=="}}
            ).encode()
            with self.assertRaises(ValueError):
                unpack(destination, attack, SHA, "plan", ["tofu/"])
            self.assertFalse((root / "escape").exists())


class NativeBackendTest(unittest.TestCase):
    def test_opentofu_plan_apply_tls_locking_and_fallback_authority(self) -> None:
        if not shutil.which("tofu"):
            self.skipTest("OpenTofu is required for native backend integration")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = Store(root / "state.sqlite")
            original = {
                "version": 4,
                "terraform_version": "1.9.0",
                "serial": 1,
                "lineage": "acb90877-7cff-4cc1-95b9-230654773655",
                "outputs": {},
                "resources": [],
            }
            store.import_state("opnsense", json.dumps(original).encode())
            select(store.path, "woodpecker")
            credentials = {
                "plan": {
                    "password": "plan-fixture",
                    "role": "plan",
                    "authority": "woodpecker",
                },
                "deploy": {
                    "password": "deploy-fixture",
                    "role": "deploy",
                    "authority": "woodpecker",
                },
                "gitlab-deploy": {
                    "password": "dr-fixture",
                    "role": "deploy",
                    "authority": "gitlab",
                },
            }
            cert, key = certificate("localhost")
            (root / "cert.pem").write_bytes(cert)
            (root / "key.pem").write_bytes(key)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler(store, credentials))
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(root / "cert.pem", root / "key.pem")
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                endpoint = f"https://localhost:{server.server_port}/state/opnsense"
                (root / "main.tf").write_text(
                    'terraform {\n  backend "http" {}\n}\nresource "terraform_data" "proof" { input = "verified" }\n'
                )
                environment = {
                    key: value
                    for key, value in os.environ.items()
                    if not key.startswith("TF_")
                }
                environment.update(
                    TF_HTTP_ADDRESS=endpoint,
                    TF_HTTP_LOCK_ADDRESS=endpoint + "/lock",
                    TF_HTTP_UNLOCK_ADDRESS=endpoint + "/lock",
                    TF_HTTP_USERNAME="plan",
                    TF_HTTP_PASSWORD="plan-fixture",
                    TF_HTTP_CLIENT_CA_CERTIFICATE_PEM=cert.decode(),
                    TF_HTTP_RETRY_MAX="0",
                )

                def tofu(
                    *args: str, success: bool = True
                ) -> subprocess.CompletedProcess[str]:
                    result = subprocess.run(
                        ["tofu", *args],
                        cwd=root,
                        env=environment,
                        text=True,
                        capture_output=True,
                    )
                    if success:
                        self.assertEqual(
                            result.returncode, 0, result.stdout + result.stderr
                        )
                    else:
                        self.assertNotEqual(result.returncode, 0)
                    return result

                tofu("init", "-input=false")
                tofu("plan", "-input=false", "-out=saved.tfplan")
                environment.update(
                    TF_HTTP_USERNAME="deploy", TF_HTTP_PASSWORD="deploy-fixture"
                )
                tofu("apply", "-input=false", "saved.tfplan")
                updated = json.loads(
                    store.request(
                        "GET", "opnsense", False, "deploy", "deploy", b"", ""
                    )[1]
                )
                self.assertEqual(updated["lineage"], original["lineage"])
                self.assertGreater(updated["serial"], original["serial"])
                self.assertEqual(updated["resources"][0]["type"], "terraform_data")
                select(store.path, "gitlab")
                tofu("plan", "-input=false", success=False)
                environment.update(
                    TF_HTTP_USERNAME="gitlab-deploy", TF_HTTP_PASSWORD="dr-fixture"
                )
                tofu("plan", "-input=false")
                lock = b'{"ID":"active-dr-lock"}'
                store.request(
                    "LOCK", "opnsense", True, "gitlab-deploy", "deploy", lock, ""
                )
                with self.assertRaises(ValueError):
                    select(store.path, "woodpecker")
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


class RecoveryMirrorTest(unittest.TestCase):
    def test_owned_source_can_be_read_by_root_without_global_git_trust(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            with patch.dict(os.environ, {"GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}):
                rejected = subprocess.run(
                    ["git", "-C", str(source), "status"],
                    capture_output=True,
                )
                self.assertNotEqual(rejected.returncode, 0)
                self.assertEqual(refs(source), {})

    def test_normal_mirroring_rejects_divergence_without_deleting_recovery_commits(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, destination = root / "source", root / "mirror.git"
            source.mkdir()

            def git(*args: str, cwd: Path = source) -> str:
                return subprocess.check_output(
                    ["git", *args], cwd=cwd, text=True, stderr=subprocess.DEVNULL
                ).strip()

            git("init", "-q", "-b", "main")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.invalid")
            (source / "file").write_text("initial")
            git("add", "file")
            git("commit", "-qm", "initial")
            git("init", "--bare", "-q", str(destination))
            mirror(source, str(destination))
            base = git("rev-parse", "HEAD")
            git("commit", "--allow-empty", "-qm", "recovery-only")
            git("push", "-q", str(destination), "main")
            recovery = git("rev-parse", "HEAD")
            git("reset", "--hard", "-q", base)
            git("commit", "--allow-empty", "-qm", "primary-only")
            with self.assertRaises(subprocess.CalledProcessError):
                mirror(source, str(destination))
            self.assertEqual(
                git("rev-parse", "refs/heads/main", cwd=destination), recovery
            )


if __name__ == "__main__":
    unittest.main()
