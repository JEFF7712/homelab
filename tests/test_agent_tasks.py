from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.agent_helpers import commit, make_repository

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def creation_payload() -> dict[str, object]:
    return {
        "objective": "Persist a safe checkpoint",
        "status": "active",
        "acceptance_criteria": [
            {"description": "state is durable", "satisfied": False, "evidence": ""}
        ],
        "owning_agent": "codex",
        "session": "session-1",
        "owned_files": ["scripts/agent/tasks.py"],
        "decisions": [],
        "durable_record_links": [],
        "completed_work": [],
        "remaining_work": ["write state"],
        "unresolved_failures": [],
        "next_action": "write state",
        "verification_records": [],
    }


def run_agent(
    repository: Path, *arguments: str, document: object | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "scripts.agent", *arguments],
        cwd=repository,
        env={**os.environ, "PYTHONPATH": str(REPOSITORY_ROOT)},
        input=None if document is None else json.dumps(document),
        text=True,
        capture_output=True,
        check=False,
    )


class TaskRecordTest(unittest.TestCase):
    def test_later_verification_supersedes_old_failure_and_retains_explicit_staleness(
        self,
    ) -> None:
        from scripts.agent.tasks import inspect_verifications

        records = [
            {
                "command": "just check",
                "exit_code": 1,
                "stale": False,
                "source_fingerprint": "old",
            },
            {
                "command": "just check",
                "exit_code": 0,
                "stale": False,
                "source_fingerprint": "current",
            },
            {
                "command": "just fmt-check",
                "exit_code": 0,
                "stale": True,
                "source_fingerprint": "current",
            },
        ]
        inspection = inspect_verifications(records, "current")
        self.assertTrue(inspection[0]["superseded"])
        self.assertFalse(inspection[1]["superseded"])
        self.assertFalse(inspection[1]["stale"])
        self.assertTrue(inspection[2]["stale"])
        self.assertNotIn("superseded", records[0])

    def test_creation_template_passes_creation_validation(self) -> None:
        from scripts.agent.tasks import creation_template, validate_task_record

        template = creation_template()
        validated = validate_task_record(template, require_system=False)
        self.assertEqual(validated["status"], "active")
        self.assertTrue(validated["acceptance_criteria"])

    def test_task_id_validation_rejects_unsafe_values(self) -> None:
        from scripts.agent.tasks import TaskValidationError, validate_task_id

        self.assertEqual(validate_task_id("safe.task_1-a"), "safe.task_1-a")
        for task_id in ("", ".", "..", "UPPER", "a/child", "/absolute", "a" * 65):
            with self.subTest(task_id=task_id), self.assertRaises(TaskValidationError):
                validate_task_id(task_id)

    def test_create_refuses_overwrite_and_records_checkout_identity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-task-") as directory:
            repository = make_repository(Path(directory))
            (repository / "tracked.txt").write_text("initial\n", encoding="utf-8")
            commit(repository, "initial")
            first = run_agent(
                repository, "task-new", "state", document=creation_payload()
            )
            second = run_agent(
                repository, "task-new", "state", document=creation_payload()
            )
            record = json.loads(
                (repository / ".agent-state/tasks/state/task.json").read_text()
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("already exists", second.stderr)
            self.assertEqual(record["schema_version"], 1)
            self.assertEqual(record["record_revision"], 1)
            self.assertEqual(record["task_id"], "state")
            self.assertRegex(record["base_commit"], r"^[0-9a-f]{40}$")
            self.assertEqual(record["base_commit"], record["checkpoint_head"])
            self.assertRegex(record["dirty_fingerprint"], r"^[0-9a-f]{64}$")

    def test_creation_requires_complete_metadata_and_stdin_json(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-task-") as directory:
            repository = make_repository(Path(directory))
            (repository / "tracked.txt").write_text("initial\n", encoding="utf-8")
            commit(repository, "initial")
            missing = run_agent(
                repository, "task-new", "state", document={"objective": "x"}
            )
            invalid = subprocess.run(
                [sys.executable, "-m", "scripts.agent", "task-new", "state"],
                cwd=repository,
                env={**os.environ, "PYTHONPATH": str(REPOSITORY_ROOT)},
                input="{bad",
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("required", missing.stderr)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("invalid JSON", invalid.stderr)
        self.assertNotIn("Traceback", invalid.stderr)

    def test_creation_allows_blocked_task_with_concrete_dependency(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-task-") as directory:
            repository = make_repository(Path(directory))
            (repository / "tracked.txt").write_text("initial\n", encoding="utf-8")
            commit(repository, "initial")
            payload = {
                **creation_payload(),
                "status": "blocked",
                "blocked_on": ["review from owner"],
            }
            result = run_agent(repository, "task-new", "state", document=payload)

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_creation_rejects_empty_owned_file_boundaries(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-task-") as directory:
            repository = make_repository(Path(directory))
            (repository / "tracked.txt").write_text("initial\n", encoding="utf-8")
            commit(repository, "initial")
            result = run_agent(
                repository,
                "task-new",
                "state",
                document={**creation_payload(), "owned_files": []},
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("owned_files", result.stderr)

    def test_creation_rejects_transient_expected_revision(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-task-") as directory:
            repository = make_repository(Path(directory))
            (repository / "tracked.txt").write_text("initial\n", encoding="utf-8")
            commit(repository, "initial")
            result = run_agent(
                repository,
                "task-new",
                "state",
                document={**creation_payload(), "expected_revision": 1},
            )

            path = repository / ".agent-state/tasks/state/task.json"
            exists = path.exists()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected_revision", result.stderr)
        self.assertFalse(exists)

    def test_create_cleans_empty_task_directory_after_write_failure(self) -> None:
        from scripts.agent.tasks import create_task

        with tempfile.TemporaryDirectory(prefix="agent-task-") as directory:
            repository = make_repository(Path(directory))
            (repository / "tracked.txt").write_text("initial\n", encoding="utf-8")
            commit(repository, "initial")
            with (
                mock.patch(
                    "scripts.agent.tasks._atomic_write_json",
                    side_effect=OSError("injected"),
                ),
                self.assertRaises(OSError),
            ):
                create_task(repository, "state", creation_payload())
            task_directory = repository / ".agent-state/tasks/state"
            self.assertFalse(task_directory.exists())
            created = create_task(repository, "state", creation_payload())

        self.assertEqual(created["task"]["task_id"], "state")

    def test_checkpoint_requires_matching_revision_and_preserves_previous_record(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-task-") as directory:
            repository = make_repository(Path(directory))
            (repository / "tracked.txt").write_text("initial\n", encoding="utf-8")
            commit(repository, "initial")
            self.assertEqual(
                run_agent(
                    repository, "task-new", "state", document=creation_payload()
                ).returncode,
                0,
            )
            path = repository / ".agent-state/tasks/state/task.json"
            record = json.loads(path.read_text())
            update = {**record, "expected_revision": 1, "next_action": "run tests"}
            first = run_agent(repository, "task-checkpoint", "state", document=update)
            second = run_agent(repository, "task-checkpoint", "state", document=update)
            persisted = json.loads(path.read_text())

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("revision conflict", second.stderr)
        self.assertEqual(persisted["record_revision"], 2)
        self.assertEqual(persisted["next_action"], "run tests")

    def test_checkpoint_rejects_changed_base_commit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-task-") as directory:
            repository = make_repository(Path(directory))
            (repository / "tracked.txt").write_text("initial\n", encoding="utf-8")
            commit(repository, "initial")
            self.assertEqual(
                run_agent(
                    repository, "task-new", "state", document=creation_payload()
                ).returncode,
                0,
            )
            path = repository / ".agent-state/tasks/state/task.json"
            record = json.loads(path.read_text())
            result = run_agent(
                repository,
                "task-checkpoint",
                "state",
                document={
                    **record,
                    "expected_revision": 1,
                    "base_commit": "f" * 40,
                },
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("base_commit", result.stderr)

    def test_checkpoint_refuses_tampered_stored_task_identity(self) -> None:
        from scripts.agent.tasks import TaskValidationError, checkpoint_task

        with tempfile.TemporaryDirectory(prefix="agent-task-") as directory:
            repository = make_repository(Path(directory))
            (repository / "tracked.txt").write_text("initial\n", encoding="utf-8")
            commit(repository, "initial")
            self.assertEqual(
                run_agent(
                    repository, "task-new", "state", document=creation_payload()
                ).returncode,
                0,
            )
            path = repository / ".agent-state/tasks/state/task.json"
            stored = json.loads(path.read_text())
            path.write_text(
                json.dumps({**stored, "task_id": "different"}), encoding="utf-8"
            )
            with self.assertRaisesRegex(TaskValidationError, "task_id"):
                checkpoint_task(repository, "state", {**stored, "expected_revision": 1})

    def test_resume_refuses_record_moved_to_another_task_directory(self) -> None:
        from scripts.agent.tasks import TaskValidationError, resume_task

        with tempfile.TemporaryDirectory(prefix="agent-task-") as directory:
            repository = make_repository(Path(directory))
            (repository / "tracked.txt").write_text("initial\n", encoding="utf-8")
            commit(repository, "initial")
            self.assertEqual(
                run_agent(
                    repository, "task-new", "state", document=creation_payload()
                ).returncode,
                0,
            )
            source = repository / ".agent-state/tasks/state/task.json"
            target = repository / ".agent-state/tasks/moved/task.json"
            target.parent.mkdir()
            source.rename(target)
            with self.assertRaisesRegex(TaskValidationError, "task_id"):
                resume_task(repository, "moved")

    def test_checkpoint_rejects_blocked_without_dependency_and_incomplete_complete(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-task-") as directory:
            repository = make_repository(Path(directory))
            (repository / "tracked.txt").write_text("initial\n", encoding="utf-8")
            commit(repository, "initial")
            self.assertEqual(
                run_agent(
                    repository, "task-new", "state", document=creation_payload()
                ).returncode,
                0,
            )
            path = repository / ".agent-state/tasks/state/task.json"
            before = path.read_text()
            record = json.loads(before)
            blocked = run_agent(
                repository,
                "task-checkpoint",
                "state",
                document={**record, "expected_revision": 1, "status": "blocked"},
            )
            complete = run_agent(
                repository,
                "task-checkpoint",
                "state",
                document={**record, "expected_revision": 1, "status": "complete"},
            )
            persisted = path.read_text()

        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("dependency", blocked.stderr)
        self.assertNotEqual(complete.returncode, 0)
        self.assertIn("acceptance", complete.stderr)
        self.assertEqual(persisted, before)

    def test_lock_conflict_and_interrupted_replace_keep_valid_record(self) -> None:
        from scripts.agent.tasks import TaskLockError, checkpoint_task

        with tempfile.TemporaryDirectory(prefix="agent-task-") as directory:
            repository = make_repository(Path(directory))
            (repository / "tracked.txt").write_text("initial\n", encoding="utf-8")
            commit(repository, "initial")
            self.assertEqual(
                run_agent(
                    repository, "task-new", "state", document=creation_payload()
                ).returncode,
                0,
            )
            path = repository / ".agent-state/tasks/state/task.json"
            before = path.read_text()
            record = json.loads(before)
            lock = path.parent / ".lock"
            lock.write_text('{"pid": 1}', encoding="utf-8")
            with self.assertRaises(TaskLockError):
                checkpoint_task(repository, "state", {**record, "expected_revision": 1})
            lock.unlink()
            with (
                mock.patch(
                    "scripts.agent.tasks.os.replace", side_effect=OSError("injected")
                ),
                self.assertRaises(OSError),
            ):
                checkpoint_task(repository, "state", {**record, "expected_revision": 1})
            persisted = path.read_text()

        self.assertEqual(persisted, before)

    def test_lock_cleanup_keeps_matching_token_owned_by_another_pid(self) -> None:
        from scripts.agent.tasks import _TaskLock

        with tempfile.TemporaryDirectory(prefix="agent-task-lock-") as directory:
            task_directory = Path(directory)
            lock = _TaskLock(task_directory, "session")
            with lock:
                payload = json.loads(lock.path.read_text())
                payload["pid"] = os.getpid() + 1
                lock.path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertTrue(lock.path.exists())
            lock.path.unlink()

    def test_lock_setup_failure_removes_only_its_new_lock_and_allows_retry(
        self,
    ) -> None:
        from scripts.agent.tasks import _TaskLock

        with tempfile.TemporaryDirectory(prefix="agent-task-lock-") as directory:
            task_directory = Path(directory)
            with (
                mock.patch(
                    "scripts.agent.tasks.os.fsync", side_effect=OSError("injected")
                ),
                self.assertRaises(OSError),
                _TaskLock(task_directory, "session"),
            ):
                pass
            self.assertFalse((task_directory / ".lock").exists())
            with _TaskLock(task_directory, "session"):
                self.assertTrue((task_directory / ".lock").exists())

    def test_lock_payload_open_failure_closes_created_descriptor(self) -> None:
        from scripts.agent.tasks import _TaskLock

        with tempfile.TemporaryDirectory(prefix="agent-task-lock-") as directory:
            task_directory = Path(directory)
            with (
                mock.patch(
                    "scripts.agent.tasks.os.fdopen", side_effect=OSError("injected")
                ),
                mock.patch("scripts.agent.tasks.os.close", wraps=os.close) as close,
                self.assertRaises(OSError),
                _TaskLock(task_directory, "session"),
            ):
                pass

            self.assertTrue(close.called)
            self.assertFalse((task_directory / ".lock").exists())

    def test_resume_of_unchanged_task_has_current_verification(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-task-") as directory:
            repository = make_repository(Path(directory))
            (repository / "tracked.txt").write_text("initial\n", encoding="utf-8")
            commit(repository, "initial")
            self.assertEqual(
                run_agent(
                    repository, "task-new", "state", document=creation_payload()
                ).returncode,
                0,
            )
            path = repository / ".agent-state/tasks/state/task.json"
            record = json.loads(path.read_text())
            record["verification_records"] = [
                {
                    "command": "python -m unittest",
                    "exit_code": 0,
                    "time": record["timestamp"],
                    "source_fingerprint": record["dirty_fingerprint"],
                    "evidence_path": "evidence.txt",
                    "stale": False,
                }
            ]
            self.assertEqual(
                run_agent(
                    repository,
                    "task-checkpoint",
                    "state",
                    document={**record, "expected_revision": 1},
                ).returncode,
                0,
            )
            result = run_agent(repository, "task-resume", "state", "--json")

        payload = json.loads(result.stdout)
        self.assertFalse(payload["checkpoint"]["fingerprint_drift"])
        self.assertFalse(payload["verifications"][0]["stale"])

    def test_invalid_utf8_stored_record_is_validation_error_without_cli_traceback(
        self,
    ) -> None:
        from scripts.agent.tasks import TaskValidationError, _read_record

        with tempfile.TemporaryDirectory(prefix="agent-task-") as directory:
            repository = make_repository(Path(directory))
            (repository / "tracked.txt").write_text("initial\n", encoding="utf-8")
            commit(repository, "initial")
            self.assertEqual(
                run_agent(
                    repository, "task-new", "state", document=creation_payload()
                ).returncode,
                0,
            )
            path = repository / ".agent-state/tasks/state/task.json"
            path.write_bytes(b"\xff")
            with self.assertRaises(TaskValidationError):
                _read_record(path)
            result = run_agent(repository, "task-resume", "state")

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)

    def test_json_task_errors_are_stable_and_structured(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-task-") as directory:
            repository = make_repository(Path(directory))
            (repository / "tracked.txt").write_text("initial\n", encoding="utf-8")
            commit(repository, "initial")
            malformed = subprocess.run(
                [sys.executable, "-m", "scripts.agent", "task-new", "state", "--json"],
                cwd=repository,
                env={**os.environ, "PYTHONPATH": str(REPOSITORY_ROOT)},
                input="{bad",
                text=True,
                capture_output=True,
                check=False,
            )
            invalid = run_agent(repository, "task-resume", "bad/id", "--json")
            missing = run_agent(repository, "task-resume", "missing", "--json")
            self.assertEqual(
                run_agent(
                    repository, "task-new", "state", document=creation_payload()
                ).returncode,
                0,
            )
            record = json.loads(
                (repository / ".agent-state/tasks/state/task.json").read_text()
            )
            self.assertEqual(
                run_agent(
                    repository,
                    "task-checkpoint",
                    "state",
                    document={**record, "expected_revision": 1},
                ).returncode,
                0,
            )
            conflict = run_agent(
                repository,
                "task-checkpoint",
                "state",
                "--json",
                document={**record, "expected_revision": 1},
            )

        for command, result in (
            ("task-new", malformed),
            ("task-resume", invalid),
            ("task-resume", missing),
            ("task-checkpoint", conflict),
        ):
            with self.subTest(command=command):
                self.assertNotEqual(result.returncode, 0)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["schema_version"], 1)
                self.assertEqual(payload["command"], command)
                self.assertEqual(payload["status"], "error")
                self.assertIn("error_type", payload)
                self.assertIn("message", payload)
                self.assertNotIn("Traceback", result.stderr)

    def test_warning_checkpoint_result_round_trips_as_canonical_task(self) -> None:
        from scripts.agent.tasks import checkpoint_task

        with tempfile.TemporaryDirectory(prefix="agent-task-") as directory:
            repository = make_repository(Path(directory))
            (repository / "tracked.txt").write_text("initial\n", encoding="utf-8")
            commit(repository, "initial")
            self.assertEqual(
                run_agent(
                    repository, "task-new", "state", document=creation_payload()
                ).returncode,
                0,
            )
            path = repository / ".agent-state/tasks/state/task.json"
            record = json.loads(path.read_text())
            with mock.patch(
                "scripts.agent.tasks.os.fsync",
                side_effect=[None, None, OSError("injected")],
            ):
                warning_result = checkpoint_task(
                    repository, "state", {**record, "expected_revision": 1}
                )
            retry = checkpoint_task(
                repository,
                "state",
                {**warning_result["task"], "expected_revision": 2},
            )

        self.assertIn("durability_warning", warning_result)
        self.assertEqual(retry["task"]["record_revision"], 3)

    def test_checkpoint_reports_directory_close_warning_after_replacement(self) -> None:
        from scripts.agent.tasks import _atomic_write_json

        with tempfile.TemporaryDirectory(prefix="agent-task-") as directory:
            path = Path(directory) / "task.json"
            path.write_text(json.dumps({"record_revision": 1}), encoding="utf-8")
            with mock.patch(
                "scripts.agent.tasks.os.close", side_effect=OSError("injected")
            ):
                result = _atomic_write_json(path, {"record_revision": 2})
            persisted = json.loads(path.read_text())

        self.assertEqual(persisted["record_revision"], 2)
        self.assertIsNotNone(result)

    def test_resume_reports_drift_unavailable_base_and_stale_verification_without_rewrite(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-task-") as directory:
            repository = make_repository(Path(directory))
            tracked = repository / "tracked.txt"
            tracked.write_text("initial\n", encoding="utf-8")
            commit(repository, "initial")
            payload = creation_payload()
            self.assertEqual(
                run_agent(repository, "task-new", "state", document=payload).returncode,
                0,
            )
            path = repository / ".agent-state/tasks/state/task.json"
            record = json.loads(path.read_text())
            record["verification_records"] = [
                {
                    "command": "python -m unittest",
                    "exit_code": 0,
                    "time": record["timestamp"],
                    "source_fingerprint": record["dirty_fingerprint"],
                    "evidence_path": "evidence.txt",
                    "stale": False,
                }
            ]
            self.assertEqual(
                run_agent(
                    repository,
                    "task-checkpoint",
                    "state",
                    document={**record, "expected_revision": 1},
                ).returncode,
                0,
            )
            tracked.write_text("changed\n", encoding="utf-8")
            dirty = run_agent(repository, "task-resume", "state", "--json")
            commit(repository, "second")
            head = run_agent(repository, "task-resume", "state", "--json")
            latest = json.loads(path.read_text())
            latest["base_commit"] = "f" * 40
            path.write_text(json.dumps(latest), encoding="utf-8")
            unavailable = run_agent(repository, "task-resume", "state", "--json")

        dirty_payload = json.loads(dirty.stdout)
        head_payload = json.loads(head.stdout)
        unavailable_payload = json.loads(unavailable.stdout)
        self.assertTrue(dirty_payload["checkpoint"]["fingerprint_drift"])
        self.assertTrue(dirty_payload["verifications"][0]["stale"])
        self.assertTrue(head_payload["checkpoint"]["head_drift"])
        self.assertFalse(unavailable_payload["base"]["available"])
        self.assertIn("next_action", unavailable_payload)


class RedactionTest(unittest.TestCase):
    def test_redacts_basic_authorization_to_end_of_line(self) -> None:
        from scripts.agent.redact import redact

        value = redact("Authorization: Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ==")

        self.assertEqual(value, "Authorization: [REDACTED]")
        self.assertNotIn("QWxhZGRpbjpvcGVuIHNlc2FtZQ==", value)

    def test_redacts_machine_credential_path_forms(self) -> None:
        from scripts.agent.redact import redact

        value = redact(
            "/root/.ssh/id_ed25519 /Users/alice/.config/sops/age/keys.txt "
            "$HOME/.kube/config ${HOME}/.ssh/id_rsa ~/.config/sops/age/key.txt "
            "~/.aws/credentials /root/.docker/config.json "
            "/home/alice/.gnupg/private-keys-v1.d ${HOME}/.aws/credentials "
            "$HOME/.gnupg /Users/alice/.docker/config.json"
        )

        for secret in (
            "id_ed25519",
            "keys.txt",
            "config",
            "id_rsa",
            "key.txt",
            "credentials",
            "config.json",
            "private-keys-v1.d",
            ".gnupg",
        ):
            self.assertNotIn(secret, value)
        self.assertEqual(value.count("[REDACTED]"), 11)

    def test_redacts_secrets_headers_private_keys_environment_and_machine_paths(
        self,
    ) -> None:
        from scripts.agent.redact import redact

        value = redact(
            "token=abc123 password=hunter2 API_KEY=key-value Authorization: Bearer abc "
            "X-API-Key: header-key "
            "AWS_SECRET_ACCESS_KEY=xyz ~/.ssh/id_ed25519 ~/.config/sops/age/keys.txt "
            "/home/a/.kube/config -----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
        )

        for secret in (
            "abc123",
            "hunter2",
            "key-value",
            "Bearer abc",
            "header-key",
            "xyz",
            "id_ed25519",
            "keys.txt",
            "PRIVATE KEY",
        ):
            self.assertNotIn(secret, value)
        self.assertIn("[REDACTED]", value)
        self.assertEqual(redact("X-API-Key: header-key"), "X-API-Key: [REDACTED]")


if __name__ == "__main__":
    unittest.main()
