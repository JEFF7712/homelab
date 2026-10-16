import base64
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from opnsense_reconciler.agent_acl import (
    ACL,
    PRIVILEGE,
    SshRouter,
    apply_plan,
    main,
    make_plan,
    validate_source,
)

CONTENT = (ACL / "ACL.xml").read_bytes()


class AclSourceTests(unittest.TestCase):
    def test_only_the_two_diagnostic_paths_are_accepted(self) -> None:
        self.assertEqual(validate_source(CONTENT), hashlib.sha256(CONTENT).hexdigest())
        invalid = [
            CONTENT.replace(b"api/quagga/diagnostics/bgpsummary", b"api/quagga/*"),
            CONTENT.replace(
                b"</acl>",
                b"<page-all><name>All</name><patterns><pattern>*</pattern></patterns></page-all></acl>",
            ),
            b'<!DOCTYPE acl [<!ENTITY grant "*">]>' + CONTENT,
            CONTENT.replace(b"<acl>", b'<acl unexpected="yes">'),
        ]
        for content in invalid:
            with self.subTest(content=content), self.assertRaises(ValueError):
                validate_source(content)

    def test_changed_source_cannot_reuse_an_old_plan(self) -> None:
        calls = []
        plan = {
            "schema_version": 1,
            "desired_sha256": "0" * 64,
            "before": {"fingerprint": "a" * 64},
        }
        with self.assertRaisesRegex(ValueError, "current source"):
            apply_plan(CONTENT, plan, lambda payload: calls.append(payload))
        self.assertEqual(calls, [])

    def test_missing_prior_state_cannot_be_applied(self) -> None:
        with self.assertRaisesRegex(ValueError, "prior state"):
            apply_plan(
                CONTENT,
                {
                    "schema_version": 1,
                    "desired_sha256": validate_source(CONTENT),
                    "before": {},
                },
                lambda payload: {},
            )

    def test_apply_must_verify_convergence(self) -> None:
        before = {"fingerprint": "a" * 64, "state": {}, "drift": ["file_content"]}
        plan = make_plan(CONTENT, lambda payload: before)
        with self.assertRaisesRegex(RuntimeError, "verify convergence"):
            apply_plan(
                CONTENT,
                plan,
                lambda payload: {"status": "pass", "drift": ["cached_privilege"]},
            )

    def test_ssh_pins_host_and_uses_only_explicit_private_key_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key"
            key.write_text("private fixture")
            key.chmod(0o600)
            router = SshRouter(key)
            with patch(
                "opnsense_reconciler.agent_acl.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    [], 0, '{"status":"pass"}', ""
                ),
            ) as run:
                router(
                    {"action": "inspect", "content": base64.b64encode(CONTENT).decode()}
                )
            command = run.call_args.args[0]
            self.assertIn("StrictHostKeyChecking=yes", command)
            self.assertIn(f"UserKnownHostsFile={ACL / 'known_hosts'}", command)
            self.assertIn("IdentitiesOnly=yes", command)
            self.assertIn(str(key), command)
            self.assertNotIn("private fixture", str(command))
            key.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "private file"):
                router({"action": "inspect"})

    def test_worker_errors_do_not_echo_ssh_stderr(self) -> None:
        with patch(
            "opnsense_reconciler.agent_acl.subprocess.run",
            return_value=subprocess.CompletedProcess(
                [], 1, "", "sensitive failure text"
            ),
        ):
            with self.assertRaises(RuntimeError) as error:
                SshRouter(None)({"action": "inspect"})
        self.assertNotIn("sensitive failure text", str(error.exception))


class AclFilesystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.php = shutil.which("php")
        self.assertIsNotNone(
            self.php, "PHP is required from the pinned development shell"
        )
        self.base = self.root / "models/Homelab"
        self.base.parent.mkdir()
        self.destination = self.base / "AgentDiagnostics/ACL/ACL.xml"
        self.cache = self.root / "cache/opnsense_acl_cache.json"
        self.cache.parent.mkdir()
        self.cache.write_text("{}")
        self.counter = self.root / "failures"
        self.recovery = self.root / "backup/acl"
        self.recovery.parent.mkdir()
        self.lock = self.root / "run/acl.lock"
        self.lock.parent.mkdir()
        bootstrap = self.root / "bootstrap.php"
        fixture = r"""<?php
namespace OPNsense\Core;
class AppConfig {
    public $application;
    public function __construct() { $this->application = (object)['tempDir' => CACHE_DIRECTORY]; }
}
class ACL {
    public function __construct() { if (!is_file(CACHE_FILE)) { $this->persist(); } }
    public function invalidateCache() { if (is_file(CACHE_FILE)) { unlink(CACHE_FILE); } }
    public function persist($nowait = true) {
        $remaining = is_file(COUNTER_FILE) ? (int)file_get_contents(COUNTER_FILE) : 0;
        if ($remaining > 0) { file_put_contents(COUNTER_FILE, (string)($remaining - 1)); return false; }
        $entries = [];
        if (is_file(SOURCE_FILE)) {
            $xml = simplexml_load_file(SOURCE_FILE);
            foreach ($xml->children() as $id => $node) {
                $patterns = [];
                foreach ($node->patterns->pattern as $pattern) { $patterns[] = (string)$pattern; }
                $entries[$id] = ['name' => (string)$node->name, 'match' => $patterns];
            }
        }
        return file_put_contents(CACHE_FILE, json_encode($entries)) !== false;
    }
}
"""
        for marker, value in {
            "CACHE_DIRECTORY": str(self.cache.parent),
            "CACHE_FILE": str(self.cache),
            "COUNTER_FILE": str(self.counter),
            "SOURCE_FILE": str(self.destination),
        }.items():
            fixture = fixture.replace(marker, json.dumps(value))
        bootstrap.write_text(fixture)
        worker = (ACL / "reconcile.php").read_text()
        replacements = {
            "/usr/local/opnsense/mvc/script/load_phalcon.php": str(bootstrap),
            "/usr/local/opnsense/mvc/app/models/Homelab": str(self.base),
            "/conf/backup/homelab-agent-acl": str(self.recovery),
            "/var/run/homelab-agent-acl.lock": str(self.lock),
            "const OWNER_UID = 0;": f"const OWNER_UID = {os.getuid()};",
            "const OWNER_GID = 0;": f"const OWNER_GID = {os.getgid()};",
        }
        for old, new in replacements.items():
            self.assertEqual(worker.count(old), 1)
            worker = worker.replace(old, new)
        self.worker = worker

    def request(self, payload: dict) -> dict:
        program = self.worker.replace(
            "__REQUEST_BASE64__",
            base64.b64encode(json.dumps(payload).encode()).decode(),
        )
        result = subprocess.run(
            [self.php], input=program, text=True, capture_output=True, timeout=10
        )
        response = json.loads(result.stdout)
        if result.returncode:
            raise RuntimeError(response["error"])
        return response

    def seed(self, content: bytes = CONTENT) -> None:
        self.destination.parent.mkdir(parents=True)
        for directory in [
            self.base,
            self.base / "AgentDiagnostics",
            self.destination.parent,
        ]:
            directory.chmod(0o755)
        self.destination.write_bytes(content)
        self.destination.chmod(0o644)
        root = ET.fromstring(content)
        privilege = root[0]
        self.cache.write_text(
            json.dumps(
                {
                    privilege.tag: {
                        "name": privilege.findtext("name"),
                        "match": [
                            node.text for node in privilege.findall("patterns/pattern")
                        ],
                    }
                }
            )
        )

    def test_missing_file_is_installed_and_second_apply_performs_no_writes(
        self,
    ) -> None:
        plan = make_plan(CONTENT, self.request)
        self.assertIn("file_content", plan["before"]["drift"])
        self.assertFalse(self.destination.exists())
        result = apply_plan(CONTENT, plan, self.request)
        self.assertTrue(result["changed"])
        self.assertEqual(self.destination.read_bytes(), CONTENT)
        recovery = json.loads(Path(result["recovery_file"]).read_text())
        self.assertIsNone(recovery["before"]["file"])
        self.assertEqual(Path(result["recovery_file"]).stat().st_mode & 0o777, 0o600)
        before = (self.destination.stat(), self.cache.stat())
        result = apply_plan(CONTENT, make_plan(CONTENT, self.request), self.request)
        self.assertFalse(result["changed"])
        self.assertEqual((self.destination.stat(), self.cache.stat()), before)

    def test_aligned_inspection_and_apply_do_not_create_lock_or_backup(self) -> None:
        self.seed()
        plan = make_plan(CONTENT, self.request)
        self.assertEqual(plan["before"]["drift"], [])
        self.assertFalse(apply_plan(CONTENT, plan, self.request)["changed"])
        self.assertFalse(self.lock.exists())
        self.assertFalse(self.recovery.exists())

    def test_file_directory_and_cache_drift_are_repaired(self) -> None:
        self.seed()
        self.destination.chmod(0o600)
        self.destination.parent.chmod(0o700)
        self.cache.write_text("{}")
        plan = make_plan(CONTENT, self.request)
        self.assertIn("file_metadata", plan["before"]["drift"])
        self.assertIn("cached_privilege", plan["before"]["drift"])
        self.assertTrue(apply_plan(CONTENT, plan, self.request)["changed"])
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.destination.parent.stat().st_mode & 0o777, 0o755)
        self.assertEqual(make_plan(CONTENT, self.request)["before"]["drift"], [])

    def test_stale_plan_is_rejected_without_writes(self) -> None:
        self.seed()
        plan = make_plan(CONTENT, self.request)
        self.destination.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError, "changed after planning"):
            apply_plan(CONTENT, plan, self.request)
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.lock.exists())

    def test_cache_failure_restores_prior_file_and_metadata(self) -> None:
        previous = CONTENT.replace(
            b"Read-only BGP diagnostics", b"Previous BGP diagnostics"
        )
        self.seed(previous)
        self.destination.chmod(0o640)
        plan = make_plan(CONTENT, self.request)
        self.counter.write_text("2")
        with self.assertRaisesRegex(
            RuntimeError, "previous file and permissions restored"
        ):
            apply_plan(CONTENT, plan, self.request)
        self.assertEqual(self.destination.read_bytes(), previous)
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o640)
        self.assertEqual(
            json.loads(self.cache.read_text())[PRIVILEGE]["name"],
            "Homelab: Previous BGP diagnostics",
        )

    def test_symlink_is_rejected_without_touching_its_target(self) -> None:
        outside = self.root / "outside"
        outside.write_bytes(b"preserve")
        self.destination.parent.mkdir(parents=True)
        self.destination.symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            make_plan(CONTENT, self.request)
        self.assertEqual(outside.read_bytes(), b"preserve")

    def test_failed_apply_can_retry_with_a_new_source_revision(self) -> None:
        previous = CONTENT.replace(
            b"Read-only BGP diagnostics", b"Previous BGP diagnostics"
        )
        self.seed(previous)
        plan = make_plan(CONTENT, self.request)
        self.counter.write_text("2")
        with self.assertRaises(RuntimeError):
            apply_plan(CONTENT, plan, self.request)
        revised = CONTENT + b"\n"
        result = apply_plan(revised, make_plan(revised, self.request), self.request)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(self.destination.read_bytes(), revised)
        self.assertEqual(len(list(self.recovery.glob("*.json"))), 2)

    def test_failed_first_install_removes_only_its_new_file_and_directories(
        self,
    ) -> None:
        plan = make_plan(CONTENT, self.request)
        self.counter.write_text("2")
        outside = self.root / "preserve"
        outside.write_text("unrelated")
        with self.assertRaisesRegex(
            RuntimeError, "previous file and permissions restored"
        ):
            apply_plan(CONTENT, plan, self.request)
        self.assertFalse(self.base.exists())
        self.assertEqual(outside.read_text(), "unrelated")
        self.assertEqual(json.loads(self.cache.read_text()), [])
        self.assertEqual(len(list(self.recovery.glob("*.json"))), 1)

    def test_worker_rejects_broader_privileges_before_mutation(self) -> None:
        content = CONTENT.replace(b"api/quagga/diagnostics/bgpsummary", b"api/quagga/*")
        with self.assertRaisesRegex(RuntimeError, "unexpected access"):
            self.request(
                {
                    "action": "apply",
                    "content": base64.b64encode(content).decode(),
                    "expected_fingerprint": "0" * 64,
                }
            )
        self.assertFalse(self.base.exists())

    def test_cli_distinguishes_drift_from_plan_and_protects_output(self) -> None:
        for action, expected_exit in [("check", 2), ("plan", 0)]:
            output = self.root / f"{action}.json"
            with (
                patch("sys.argv", ["agent-acl", action, "--output", str(output)]),
                patch(
                    "opnsense_reconciler.agent_acl.SshRouter", return_value=self.request
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(main(), expected_exit)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
