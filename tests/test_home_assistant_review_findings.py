from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.home_assistant.__main__ import (
    build_parser,
    cmd_adopt,
    cmd_apply,
    cmd_capture,
    cmd_diff,
    cmd_plan,
    cmd_verify,
)
from scripts.home_assistant.adapters import get_adapter
from scripts.home_assistant.adapters.core import (
    CoreConfigurationAdapter,
    sync_core_to_gitops,
)
from scripts.home_assistant.adapters.dashboard import DashboardAdapter
from scripts.home_assistant.adapters.helper import HelperAdapter
from scripts.home_assistant.adapters.integration import IntegrationAdapter
from scripts.home_assistant.canonical import (
    IncludeTag,
    SecretTag,
    canonical_hash,
    compute_baseline_hash,
    detect_secrets,
    sanitize_error,
    strip_volatile,
    to_json_compatible,
)
from scripts.home_assistant.client import MockHomeAssistantClient
from scripts.home_assistant.compare import compare_three_way
from scripts.home_assistant.models import (
    ActionType,
    ApplyPlan,
    BaselineRecord,
    DiffStatus,
    ExitCode,
    PlanAction,
    ResourceDocument,
)
from scripts.home_assistant.planner import LockError, Planner, StalePlanError
from scripts.home_assistant.source import write_resource_atomic


class DummyArgs:
    def __init__(self, **kwargs: object) -> None:
        self.instance = "test-instance"
        self.json = True
        self.mock = True
        self.url = None
        self.token = None
        self.token_file = None
        self.select = None
        self.all = False
        self.allow_delete = False
        self.force = False
        self.sync_gitops = False
        self.plan_file = None
        self.checkpoint = False
        self.bootstrap = False
        self.yes = False
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestHomeAssistantReviewFindings(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "home-assistant" / "core").mkdir(parents=True)
        (self.root / "home-assistant" / "automations").mkdir(parents=True)
        (self.root / "home-assistant" / "scripts").mkdir(parents=True)
        (self.root / "home-assistant" / "dashboards").mkdir(parents=True)
        (self.root / "gitops" / "home-assistant").mkdir(parents=True)
        self.mock_client = MockHomeAssistantClient()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # ------------------------------------------------------------------
    # R1: cmd_adopt rejects conflicts and dirty files
    # ------------------------------------------------------------------
    @patch("scripts.home_assistant.__main__.get_client")
    def test_r1_adopt_blocks_unresolved_conflicts(
        self, mock_get_client: object
    ) -> None:
        mock_get_client.return_value = self.mock_client  # type: ignore[attr-defined]

        # 1. Baseline has alias "Base Alias"
        planner = Planner(self.root, "test-instance")
        base_desired = {
            "id": "conflicted_auto",
            "alias": "Base Alias",
            "triggers": [],
            "actions": [],
        }
        base_rec = BaselineRecord(
            instance="test-instance",
            source_commit="commit1",
            content_hash="basehash",
            timestamp=datetime.now(UTC).isoformat(),
            resources={
                "automation/conflicted_auto": {
                    "canonical_hash": canonical_hash(base_desired),
                    "desired": base_desired,
                }
            },
        )
        planner.save_baseline(base_rec)

        # 2. Git has "Git Divergence"
        git_doc = ResourceDocument(
            kind="automation",
            key="conflicted_auto",
            desired={
                "id": "conflicted_auto",
                "alias": "Git Divergence",
                "triggers": [],
                "actions": [],
            },
        )
        write_resource_atomic(self.root, git_doc)

        # 3. Live HA has "Live Divergence"
        self.mock_client.automations["conflicted_auto"] = {
            "id": "conflicted_auto",
            "alias": "Live Divergence",
            "triggers": [],
            "actions": [],
        }

        # 4. Attempting to adopt must fail with conflict and preserve Git source
        adopt_args = DummyArgs(select=["automation/conflicted_auto"])
        exit_code = cmd_adopt(adopt_args, self.root)  # type: ignore[arg-type]
        self.assertEqual(exit_code, ExitCode.CONFLICT_OR_INVALID.value)

        # Verify Git file was NOT overwritten
        auto_file = (
            self.root / "home-assistant" / "automations" / "conflicted_auto.yaml"
        )
        self.assertIn("Git Divergence", auto_file.read_text(encoding="utf-8"))
        self.assertNotIn("Live Divergence", auto_file.read_text(encoding="utf-8"))

    @patch("subprocess.run")
    @patch("scripts.home_assistant.__main__.get_client")
    def test_r1_adopt_blocks_dirty_target_file(
        self, mock_get_client: object, mock_subproc: object
    ) -> None:
        mock_get_client.return_value = self.mock_client  # type: ignore[attr-defined]

        # Live experiment available for adoption
        self.mock_client.automations["new_auto"] = {
            "id": "new_auto",
            "alias": "New Auto",
            "triggers": [],
            "actions": [],
        }

        # Mock git status reporting dirty working tree file
        mock_res = unittest.mock.MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = " M home-assistant/automations/new_auto.yaml\n"
        mock_subproc.return_value = mock_res  # type: ignore[attr-defined]

        adopt_args = DummyArgs(select=["automation/new_auto"])
        exit_code = cmd_adopt(adopt_args, self.root)  # type: ignore[arg-type]
        self.assertEqual(exit_code, ExitCode.CONFLICT_OR_INVALID.value)

    # ------------------------------------------------------------------
    # R2: Preflight secret checks, file permissions, error sanitization
    # ------------------------------------------------------------------
    @patch("scripts.home_assistant.__main__.get_client")
    def test_r2_capture_and_adopt_reject_plaintext_secrets_before_write(
        self, mock_get_client: object
    ) -> None:
        mock_get_client.return_value = self.mock_client  # type: ignore[attr-defined]

        # Live state has plaintext credential
        self.mock_client.automations["leaky_auto"] = {
            "id": "leaky_auto",
            "alias": "Leaky Automation",
            "triggers": [],
            "actions": [
                {
                    "action": "notify.telegram",
                    "data": {"password": "super_secret_plaintext_password_123"},
                }
            ],
        }

        # 1. Adoption must be rejected before any file write
        adopt_args = DummyArgs(select=["automation/leaky_auto"])
        adopt_code = cmd_adopt(adopt_args, self.root)  # type: ignore[arg-type]
        self.assertEqual(adopt_code, ExitCode.CONFLICT_OR_INVALID.value)
        self.assertFalse(
            (self.root / "home-assistant" / "automations" / "leaky_auto.yaml").exists()
        )

        # 2. Capture must also reject before writing capture.json
        capture_args = DummyArgs()
        capture_code = cmd_capture(capture_args, self.root)  # type: ignore[arg-type]
        self.assertEqual(capture_code, ExitCode.CONFLICT_OR_INVALID.value)

    def test_r2_error_sanitization(self) -> None:
        sensitive_err = "Failed connection to https://user:secretpass123@ha.example.com with Bearer eyJhbGciOiJIUzI1NiJ9.abc"
        cleaned = sanitize_error(sensitive_err)
        self.assertNotIn("secretpass123", cleaned)
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9.abc", cleaned)
        self.assertIn("[REDACTED]", cleaned)

    @patch("scripts.home_assistant.__main__.get_client")
    def test_r2_capture_file_permissions(self, mock_get_client: object) -> None:
        mock_get_client.return_value = self.mock_client  # type: ignore[attr-defined]
        capture_args = DummyArgs()
        code = cmd_capture(capture_args, self.root)  # type: ignore[arg-type]
        self.assertEqual(code, ExitCode.CLEAN.value)

        captures_base = (
            self.root / ".agent-state" / "home-assistant" / "test-instance" / "captures"
        )
        cap_dirs = list(captures_base.iterdir())
        self.assertTrue(len(cap_dirs) > 0)
        cap_dir = cap_dirs[0]
        dir_mode = cap_dir.stat().st_mode & 0o777
        self.assertEqual(dir_mode, 0o700)
        cap_file = cap_dir / "capture.json"
        file_mode = cap_file.stat().st_mode & 0o777
        self.assertEqual(file_mode, 0o600)

    # ------------------------------------------------------------------
    # R3: Plan bindings (target, version, baseline, source hash, TTL)
    # ------------------------------------------------------------------
    def test_r3_plan_bindings_validation(self) -> None:
        planner = Planner(self.root, "target-01")
        base_rec = BaselineRecord(
            instance="target-01",
            source_commit="rev1",
            content_hash="correct_hash",
            timestamp=datetime.now(UTC).isoformat(),
            resources={},
        )
        planner.save_baseline(base_rec)

        now = datetime.now(UTC).isoformat()
        plan = ApplyPlan(
            plan_id="plan-1",
            timestamp=now,
            instance="target-01",
            git_revision="rev1",
            baseline_hash="correct_hash",
            actions=[
                PlanAction(
                    kind="automation",
                    key="foo",
                    action=ActionType.CREATE,
                    before=None,
                    after={"id": "foo", "alias": "Foo", "triggers": [], "actions": []},
                    expected_live_hash=None,
                )
            ],
            ha_version="2026.9.1",
            source_hash="source123",
        )

        git_docs = {
            "automation/foo": ResourceDocument(
                kind="automation",
                key="foo",
                desired={"id": "foo", "alias": "Foo", "triggers": [], "actions": []},
            )
        }

        # 1. Wrong instance must be rejected
        bad_inst_plan = ApplyPlan.from_dict(
            {**plan.to_dict(), "instance": "other-instance"}
        )
        with self.assertRaises(StalePlanError):
            planner.execute_plan(bad_inst_plan, self.mock_client, git_docs)

        # 2. Expired plan (> 1800s TTL) must be rejected
        expired_plan = ApplyPlan.from_dict(
            {**plan.to_dict(), "timestamp": "2020-01-01T00:00:00+00:00"}
        )
        with self.assertRaises(StalePlanError):
            planner.execute_plan(expired_plan, self.mock_client, git_docs)

        # 3. Wrong baseline hash must be rejected
        bad_base_plan = ApplyPlan.from_dict(
            {**plan.to_dict(), "baseline_hash": "wrong_hash"}
        )
        with self.assertRaises(StalePlanError):
            planner.execute_plan(bad_base_plan, self.mock_client, git_docs)

        # 4. Mismatched HA version must be rejected
        bad_ver_plan = ApplyPlan.from_dict({**plan.to_dict(), "ha_version": "2024.1.0"})
        with self.assertRaises(StalePlanError):
            planner.execute_plan(bad_ver_plan, self.mock_client, git_docs)

        # 5. Modified Git source must be rejected
        with self.assertRaises(StalePlanError):
            planner.execute_plan(plan, self.mock_client, {})  # foo missing from source

    # ------------------------------------------------------------------
    # R4: Failed reads propagate as UNKNOWN, never deleted or created
    # ------------------------------------------------------------------
    def test_r4_failed_reads_propagate_unknown(self) -> None:
        git_doc = ResourceDocument(
            kind="automation",
            key="lights",
            desired={"id": "lights", "alias": "Lights"},
        )
        # Kind-wide collection error for automations
        diff_rep = compare_three_way(
            instance="test-instance",
            baseline={"automation/lights": git_doc},
            git={"automation/lights": git_doc},
            live={},  # live returned empty due to error!
            errors={"automation/all": "Connection reset by peer"},
        )
        item = diff_rep.items[0]
        self.assertEqual(item.status, DiffStatus.UNKNOWN)
        self.assertIn("Connection reset by peer", item.details)

        # Planner refuses to plan unknown
        planner = Planner(self.root, "test-instance")
        with self.assertRaises(ValueError):
            planner.create_plan(diff_rep, git_revision="rev1", baseline_hash="base1")

    # ------------------------------------------------------------------
    # R5: Observe-only adapters reject mutation and perform real readback
    # ------------------------------------------------------------------
    def test_r5_observe_only_adapters_reject_mutation(self) -> None:
        helper = HelperAdapter()
        integration = IntegrationAdapter()
        core = CoreConfigurationAdapter()

        # Helpers are ui-editable for name/icon; integrations and core stay
        # observe-only.
        self.assertTrue(helper.supports_mutation)
        self.assertFalse(integration.supports_mutation)
        self.assertFalse(core.supports_mutation)

        action = PlanAction(
            kind="helper",
            key="input_boolean.test",
            action=ActionType.CREATE,
            before=None,
            after={},
            expected_live_hash=None,
        )
        # The registry API cannot provision helpers, so CREATE still raises
        # with guidance to use the UI.
        with self.assertRaises(NotImplementedError):
            helper.apply(self.mock_client, action)
        with self.assertRaises(NotImplementedError):
            integration.apply(self.mock_client, action)
        with self.assertRaises(NotImplementedError):
            core.apply(self.mock_client, action)

    # ------------------------------------------------------------------
    # R6: Cluster-shared baseline
    # ------------------------------------------------------------------
    @patch("scripts.home_assistant.__main__.get_client")
    def test_r6_cluster_shared_baseline(self, mock_get_client: object) -> None:
        mock_get_client.return_value = self.mock_client  # type: ignore[attr-defined]

        # Verify command updates cluster baseline
        doc = ResourceDocument(
            kind="automation",
            key="night_light",
            desired={
                "id": "night_light",
                "alias": "Night Light",
                "triggers": [],
                "actions": [],
            },
        )
        write_resource_atomic(self.root, doc)
        self.mock_client.automations["night_light"] = doc.desired

        verify_args = DummyArgs(checkpoint=True)
        code = cmd_verify(verify_args, self.root)  # type: ignore[arg-type]

        self.assertEqual(code, ExitCode.CLEAN.value)

        # Baseline should be saved in cluster_baselines on client
        self.assertIn("test-instance", self.mock_client.cluster_baselines)
        cluster_base = self.mock_client.cluster_baselines["test-instance"]
        self.assertIn("automation/night_light", cluster_base["resources"])

    # ------------------------------------------------------------------
    # R7: Atomic locking, renewal, release, crash recovery
    # ------------------------------------------------------------------
    def test_r7_atomic_locking(self) -> None:
        planner = Planner(self.root, "test-instance")
        planner.acquire_lock(owner="agent-1", plan_id="p1")

        # Second acquire by another agent must fail with LockError
        with self.assertRaises(LockError):
            planner.acquire_lock(owner="agent-2", plan_id="p2")

        # Lock file permissions must be 0o600
        lock_mode = planner.lock_file.stat().st_mode & 0o777
        self.assertEqual(lock_mode, 0o600)

        # Owner-checked release: wrong owner cannot release
        planner.release_lock(plan_id="p1", owner="agent-wrong")
        self.assertTrue(planner.lock_file.exists())

        # Correct owner releases
        planner.release_lock(plan_id="p1", owner="agent-1")
        self.assertFalse(planner.lock_file.exists())

    # ------------------------------------------------------------------
    # R8: Path-aware volatile filtering preserves nested user configuration
    # ------------------------------------------------------------------
    def test_r8_path_aware_volatile_filtering(self) -> None:
        user_script = {
            "alias": "My Script",
            "last_triggered": "2026-09-09T12:00:00Z",
            "sequence": [
                {
                    "variables": {
                        "context": "kitchen_motion",
                        "created_at": "custom_timestamp_param",
                    }
                }
            ],
        }

        cleaned = strip_volatile(user_script)
        self.assertNotIn("last_triggered", cleaned)
        self.assertEqual(
            cleaned["sequence"][0]["variables"]["context"], "kitchen_motion"
        )
        self.assertEqual(
            cleaned["sequence"][0]["variables"]["created_at"], "custom_timestamp_param"
        )

        script_a = {"sequence": [{"variables": {"context": "door_open"}}]}
        script_b = {"sequence": [{"variables": {"context": "window_open"}}]}
        self.assertNotEqual(canonical_hash(script_a), canonical_hash(script_b))

    # ------------------------------------------------------------------
    # R9: Adopting UI deletions with --allow-delete and dependency checks
    # ------------------------------------------------------------------
    @patch("scripts.home_assistant.__main__.get_client")
    def test_r9_adopt_ui_deletions(self, mock_get_client: object) -> None:
        mock_get_client.return_value = self.mock_client  # type: ignore[attr-defined]

        doc = ResourceDocument(
            kind="automation",
            key="deleted_auto",
            desired={
                "id": "deleted_auto",
                "alias": "To Delete",
                "triggers": [],
                "actions": [],
            },
        )
        write_resource_atomic(self.root, doc)

        planner = Planner(self.root, "test-instance", client=self.mock_client)
        base_rec = BaselineRecord(
            instance="test-instance",
            source_commit="c1",
            content_hash="h1",
            timestamp=datetime.now(UTC).isoformat(),
            resources={
                "automation/deleted_auto": {
                    "canonical_hash": canonical_hash(doc.desired),
                    "desired": doc.desired,
                }
            },
        )
        planner.save_baseline(base_rec)

        # 2. Adopt without --allow-delete must be rejected
        adopt_args_no_del = DummyArgs(
            select=["automation/deleted_auto"], allow_delete=False
        )
        code = cmd_adopt(adopt_args_no_del, self.root)  # type: ignore[arg-type]
        self.assertEqual(code, ExitCode.CONFLICT_OR_INVALID.value)
        self.assertTrue(
            (
                self.root / "home-assistant" / "automations" / "deleted_auto.yaml"
            ).exists()
        )

        # 3. Adopt with --allow-delete succeeds and deletes file
        adopt_args_with_del = DummyArgs(
            select=["automation/deleted_auto"], allow_delete=True
        )
        code2 = cmd_adopt(adopt_args_with_del, self.root)  # type: ignore[arg-type]
        self.assertEqual(code2, ExitCode.CLEAN.value)
        self.assertFalse(
            (
                self.root / "home-assistant" / "automations" / "deleted_auto.yaml"
            ).exists()
        )

    @patch("scripts.home_assistant.__main__.get_client")
    def test_r9_adopt_ui_deletion_blocked_by_dependency(
        self, mock_get_client: object
    ) -> None:
        mock_get_client.return_value = self.mock_client  # type: ignore[attr-defined]

        script_doc = ResourceDocument(
            kind="script",
            key="turn_off_fan",
            desired={"alias": "Turn Off Fan", "sequence": []},
        )
        write_resource_atomic(self.root, script_doc)

        auto_doc = ResourceDocument(
            kind="automation",
            key="evening",
            desired={
                "id": "evening",
                "alias": "Evening",
                "triggers": [],
                "actions": [{"action": "script.turn_off_fan"}],
            },
        )
        write_resource_atomic(self.root, auto_doc)

        planner = Planner(self.root, "test-instance", client=self.mock_client)
        planner.save_baseline(
            BaselineRecord(
                instance="test-instance",
                source_commit="c1",
                content_hash="h1",
                timestamp=datetime.now(UTC).isoformat(),
                resources={
                    "script/turn_off_fan": {
                        "canonical_hash": canonical_hash(script_doc.desired),
                        "desired": script_doc.desired,
                    }
                },
            )
        )

        adopt_args = DummyArgs(select=["script/turn_off_fan"], allow_delete=True)
        code = cmd_adopt(adopt_args, self.root)  # type: ignore[arg-type]
        self.assertEqual(code, ExitCode.CONFLICT_OR_INVALID.value)
        self.assertTrue(
            (self.root / "home-assistant" / "scripts" / "turn_off_fan.yaml").exists()
        )

    # ------------------------------------------------------------------
    # R10: Dashboard metadata reconciliation and verification
    # ------------------------------------------------------------------
    def test_r10_dashboard_metadata_reconciliation(self) -> None:
        adapter = DashboardAdapter()
        self.mock_client.create_dashboard(
            url_path="analytics",
            title="Old Analytics Title",
            icon="mdi:chart-line",
            show_in_sidebar=True,
        )

        desired_doc = ResourceDocument(
            kind="dashboard",
            key="analytics",
            desired={
                "title": "New Analytics Title",
                "icon": "mdi:chart-bar",
                "show_in_sidebar": True,
                "require_admin": False,
                "views": [],
            },
        )

        action = PlanAction(
            kind="dashboard",
            key="analytics",
            action=ActionType.UPDATE,
            before={"title": "Old Analytics Title"},
            after=desired_doc.desired,
            expected_live_hash=None,
        )
        adapter.apply(self.mock_client, action)

        meta_after = next(
            d for d in self.mock_client.dashboard_list if d.get("id") == "analytics"
        )
        self.assertEqual(meta_after["title"], "New Analytics Title")
        self.assertEqual(meta_after["icon"], "mdi:chart-bar")
        self.assertTrue(adapter.verify(self.mock_client, desired_doc))

    # ------------------------------------------------------------------
    # R11: Core source synchronization to GitOps ConfigMap
    # ------------------------------------------------------------------
    def test_r11_core_gitops_synchronization(self) -> None:
        core_yaml = "default_config:\nrecorder:\n  db_url: !secret recorder_db_url\n"
        core_file = self.root / "home-assistant" / "core" / "configuration.yaml"
        core_file.write_text(core_yaml, encoding="utf-8")

        dep_yaml = (
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  annotations:\n"
            "    checksum/config: 1111111111111111111111111111111111111111111111111111111111111111\n"
        )
        dep_file = self.root / "gitops" / "home-assistant" / "deployment.yaml"
        dep_file.write_text(dep_yaml, encoding="utf-8")

        sync_core_to_gitops(self.root)

        cm_file = self.root / "gitops" / "home-assistant" / "config.yaml"
        self.assertTrue(cm_file.is_file())
        self.assertIn("default_config:", cm_file.read_text(encoding="utf-8"))

        new_dep_text = dep_file.read_text(encoding="utf-8")
        self.assertNotIn(
            "1111111111111111111111111111111111111111111111111111111111111111",
            new_dep_text,
        )

    # ------------------------------------------------------------------
    # R12: Safe serialization of SecretTag and IncludeTag
    # ------------------------------------------------------------------
    def test_r12_tag_serialization(self) -> None:
        data = {
            "recorder": {"db_url": SecretTag("my_secret")},
            "automations": IncludeTag("automations.yaml"),
        }
        json_ready = to_json_compatible(data)
        self.assertEqual(json_ready["recorder"]["db_url"], {"!secret": "my_secret"})
        self.assertEqual(json_ready["automations"], {"!include": "automations.yaml"})

        doc = ResourceDocument(kind="core", key="configuration", desired=data)
        doc_dict = doc.to_dict()
        self.assertEqual(
            doc_dict["desired"]["recorder"]["db_url"], {"!secret": "my_secret"}
        )

        action = PlanAction(
            kind="core",
            key="configuration",
            action=ActionType.UPDATE,
            before=None,
            after=data,
            expected_live_hash=None,
        )
        act_dict = action.to_dict()
        self.assertEqual(
            act_dict["after"]["recorder"]["db_url"], {"!secret": "my_secret"}
        )

    # ------------------------------------------------------------------
    # F1: CLI argument ordering and CI invocation
    # ------------------------------------------------------------------
    def test_f1_cli_argument_ordering_and_ci_invocation(self) -> None:
        parser = build_parser()
        # Verify exact CI command invocations can be parsed cleanly without error
        args_plan = parser.parse_args(["plan", "--instance", "homelab-01", "--json"])
        self.assertEqual(args_plan.command, "plan")
        self.assertEqual(args_plan.instance, "homelab-01")
        self.assertTrue(args_plan.json)

        args_plan_prefix = parser.parse_args(
            ["--instance", "homelab-01", "plan", "--json"]
        )
        self.assertEqual(args_plan_prefix.command, "plan")
        self.assertEqual(args_plan_prefix.instance, "homelab-01")

        args_apply = parser.parse_args(
            [
                "apply",
                "--instance",
                "homelab-01",
                "--plan-file",
                "plan.json",
                "--yes",
            ]
        )
        self.assertEqual(args_apply.command, "apply")
        self.assertEqual(args_apply.plan_file, "plan.json")
        self.assertTrue(args_apply.yes)

        args_verify = parser.parse_args(
            ["verify", "--instance", "homelab-01", "--checkpoint"]
        )
        self.assertEqual(args_verify.command, "verify")
        self.assertTrue(args_verify.checkpoint)

        # Test full deployment sequence using mock client
        doc = ResourceDocument(
            kind="automation",
            key="ci_test",
            desired={
                "id": "ci_test",
                "alias": "CI Test",
                "triggers": [],
                "actions": [],
            },
        )
        write_resource_atomic(self.root, doc)

        with patch(
            "scripts.home_assistant.__main__.get_client",
            return_value=self.mock_client,
        ):
            # 1. Plan
            plan_args = DummyArgs(instance="test-instance", json=True)
            plan_code = cmd_plan(plan_args, self.root)
            self.assertEqual(plan_code, ExitCode.CLEAN.value)

            plans_dir = (
                self.root
                / ".agent-state"
                / "home-assistant"
                / "test-instance"
                / "plans"
            )
            plan_files = list(plans_dir.glob("*.json"))
            self.assertEqual(len(plan_files), 1)
            plan_file_path = str(plan_files[0])

            # 2. Apply with plan-file
            apply_args = DummyArgs(
                instance="test-instance", plan_file=plan_file_path, yes=True
            )
            apply_code = cmd_apply(apply_args, self.root)
            self.assertEqual(apply_code, ExitCode.CLEAN.value)
            self.assertIn("ci_test", self.mock_client.automations)

            # 3. Verify
            verify_args = DummyArgs(instance="test-instance")
            verify_code = cmd_verify(verify_args, self.root)
            self.assertEqual(verify_code, ExitCode.CLEAN.value)

    # ------------------------------------------------------------------
    # F2: Shared cluster baseline reading across checkouts
    # ------------------------------------------------------------------
    def test_f2_shared_cluster_baseline_diff_and_adopt(self) -> None:
        checkout2_dir = tempfile.TemporaryDirectory()
        self.addCleanup(checkout2_dir.cleanup)
        checkout2 = Path(checkout2_dir.name)
        (checkout2 / "home-assistant" / "automations").mkdir(parents=True)

        doc_base = ResourceDocument(
            kind="automation",
            key="shared_auto",
            desired={
                "id": "shared_auto",
                "alias": "Baseline Alias",
                "triggers": [],
                "actions": [],
            },
        )
        # Write to both checkouts
        write_resource_atomic(self.root, doc_base)
        write_resource_atomic(checkout2, doc_base)

        # Set live and cluster baseline to Baseline Alias
        self.mock_client.automations["shared_auto"] = doc_base.desired
        rec = BaselineRecord(
            schema_version="1.0",
            instance="test-instance",
            source_commit="commit-1",
            content_hash=compute_baseline_hash(
                {"automation/shared_auto": {"desired": doc_base.desired}}
            ),
            timestamp=datetime.now(UTC).isoformat(),
            resources={
                "automation/shared_auto": {
                    "canonical_hash": canonical_hash(doc_base.desired),
                    "desired": doc_base.desired,
                    "verified_at": datetime.now(UTC).isoformat(),
                }
            },
        )
        self.mock_client.save_cluster_baseline("test-instance", rec.to_dict())

        # Live changes in UI to experiment
        self.mock_client.automations["shared_auto"]["alias"] = "UI Experiment Alias"

        # Checkout 2 has NO local cache. Diff in checkout 2 must read cluster baseline!
        with patch(
            "scripts.home_assistant.__main__.get_client",
            return_value=self.mock_client,
        ):
            diff_code = cmd_diff(
                DummyArgs(select=["automation/shared_auto"]), checkout2
            )
            # Drift detected (EXPERIMENT), not CONFLICT (2)
            self.assertEqual(diff_code, ExitCode.DRIFT_OR_PENDING.value)

            # Adopt in checkout 2
            adopt_code = cmd_adopt(
                DummyArgs(select=["automation/shared_auto"]), checkout2
            )
            self.assertEqual(adopt_code, ExitCode.CLEAN.value)

            # Verify file in checkout 2 was updated with UI Experiment Alias
            dest = checkout2 / "home-assistant" / "automations" / "shared_auto.yaml"
            self.assertTrue(dest.is_file())
            self.assertIn("UI Experiment Alias", dest.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # F3: Adoption target scope (blocks committed git_change, all skips it)
    # ------------------------------------------------------------------
    def test_f3_adopt_scope_blocks_git_change(self) -> None:
        doc_git = ResourceDocument(
            kind="automation",
            key="work_light",
            desired={
                "id": "work_light",
                "alias": "Committed Git Change",
                "triggers": [],
                "actions": [],
            },
        )
        write_resource_atomic(self.root, doc_git)

        live_desired = {
            "id": "work_light",
            "alias": "Old Baseline Alias",
            "triggers": [],
            "actions": [],
        }
        self.mock_client.automations["work_light"] = live_desired

        # Baseline equals live
        rec = BaselineRecord(
            schema_version="1.0",
            instance="test-instance",
            source_commit="commit-1",
            content_hash=compute_baseline_hash(
                {"automation/work_light": {"desired": live_desired}}
            ),
            timestamp=datetime.now(UTC).isoformat(),
            resources={
                "automation/work_light": {
                    "canonical_hash": canonical_hash(live_desired),
                    "desired": live_desired,
                    "verified_at": datetime.now(UTC).isoformat(),
                }
            },
        )
        self.mock_client.save_cluster_baseline("test-instance", rec.to_dict())

        with patch(
            "scripts.home_assistant.__main__.get_client",
            return_value=self.mock_client,
        ):
            # 1. Selected adoption of git_change must be explicitly rejected
            code_select = cmd_adopt(
                DummyArgs(select=["automation/work_light"]), self.root
            )
            self.assertEqual(code_select, ExitCode.CONFLICT_OR_INVALID.value)

            dest = self.root / "home-assistant" / "automations" / "work_light.yaml"
            self.assertIn("Committed Git Change", dest.read_text(encoding="utf-8"))

            # 2. Mixed --all adoption: add a real UI experiment
            self.mock_client.automations["ui_experiment"] = {
                "id": "ui_experiment",
                "alias": "UI Experiment Auto",
                "triggers": [],
                "actions": [],
            }
            code_all = cmd_adopt(DummyArgs(all=True), self.root)
            self.assertEqual(code_all, ExitCode.CLEAN.value)

            # UI experiment adopted
            ui_dest = (
                self.root / "home-assistant" / "automations" / "ui_experiment.yaml"
            )
            self.assertTrue(ui_dest.is_file())
            self.assertIn("UI Experiment Auto", ui_dest.read_text(encoding="utf-8"))

            # Git change STILL preserved
            self.assertIn("Committed Git Change", dest.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # F4: Concurrent lock race and multi-checkout locking
    # ------------------------------------------------------------------
    def test_f4_locking_concurrency_and_cross_checkout(self) -> None:
        planner1 = Planner(self.root, "test-instance", client=self.mock_client)
        planner1.acquire_lock(owner="agent-1", plan_id="p1")

        # Second acquire in same checkout must raise LockError
        planner2_same = Planner(self.root, "test-instance", client=self.mock_client)
        with self.assertRaises(LockError):
            planner2_same.acquire_lock(owner="agent-2", plan_id="p2")

        # Second acquire in separate checkout must also raise LockError via cluster lock
        checkout2_dir = tempfile.TemporaryDirectory()
        self.addCleanup(checkout2_dir.cleanup)
        checkout2 = Path(checkout2_dir.name)
        planner_other = Planner(checkout2, "test-instance", client=self.mock_client)
        with self.assertRaises(LockError):
            planner_other.acquire_lock(owner="ci-job-other", plan_id="p3")

        # Renewal works atomically
        planner1.renew_lock(plan_id="p1", owner="agent-1")

        # Release lock
        planner1.release_lock(plan_id="p1", owner="agent-1")

        # Other checkout can now acquire lock
        planner_other.acquire_lock(owner="ci-job-other", plan_id="p3")
        planner_other.release_lock(plan_id="p3", owner="ci-job-other")

    # ------------------------------------------------------------------
    # F5: Authoritative cluster checkpoint error propagation
    # ------------------------------------------------------------------
    def test_f5_cluster_checkpoint_error_propagation(self) -> None:
        doc = ResourceDocument(
            kind="automation",
            key="f5_auto",
            desired={
                "id": "f5_auto",
                "alias": "F5 Auto",
                "triggers": [],
                "actions": [],
            },
        )
        write_resource_atomic(self.root, doc)
        self.mock_client.automations["f5_auto"] = doc.desired

        # Fail cluster writes
        self.mock_client.save_cluster_baseline = MagicMock(
            side_effect=RuntimeError("Cluster write rejected by policy")
        )

        with patch(
            "scripts.home_assistant.__main__.get_client",
            return_value=self.mock_client,
        ):
            # Verify with checkpoint must report error and exit non-zero
            code = cmd_verify(DummyArgs(checkpoint=True), self.root)
            self.assertEqual(code, ExitCode.CONFLICT_OR_INVALID.value)

    # ------------------------------------------------------------------
    # F6: Per-resource collection timeout and capture status
    # ------------------------------------------------------------------
    def test_f6_per_resource_collection_errors_and_capture_status(self) -> None:
        # 1. get_automation timeout raises instead of silently becoming empty list
        self.mock_client.entities = [
            {"entity_id": "automation.review", "unique_id": "review"}
        ]
        self.mock_client.get_automation = MagicMock(
            side_effect=TimeoutError("HTTP GET /api/config/automation timed out")
        )
        adapter = get_adapter("automation")
        with self.assertRaises(TimeoutError):
            adapter.export_from_live(self.mock_client)

        # 2. Capture returns ExitCode.UNAVAILABLE and status incomplete when adapter fails
        with patch(
            "scripts.home_assistant.__main__.get_client",
            return_value=self.mock_client,
        ):
            capture_code = cmd_capture(DummyArgs(), self.root)
            self.assertEqual(capture_code, ExitCode.UNAVAILABLE.value)

    # ------------------------------------------------------------------
    # F7: Sensitive API key and URL credential detection
    # ------------------------------------------------------------------
    def test_f7_sensitive_api_key_detection(self) -> None:
        payload = {
            "id": "telegram_alert",
            "alias": "Telegram Alert",
            "actions": [
                {
                    "action": "notify.telegram",
                    "data": {"api_key": "synthetic-example-key-1234"},
                }
            ],
        }
        findings = detect_secrets(payload, path="automation/telegram_alert")
        self.assertTrue(len(findings) > 0)
        self.assertTrue(any("api_key" in f for f in findings))

        url_payload = (
            "https://hooks.slack.com/services?api_key=synthetic_slack_key_1234"
        )
        url_findings = detect_secrets(url_payload, path="url_test")
        self.assertTrue(len(url_findings) > 0)

        # Preflight reject in adopt
        self.mock_client.automations["telegram_alert"] = payload
        with patch(
            "scripts.home_assistant.__main__.get_client",
            return_value=self.mock_client,
        ):
            adopt_code = cmd_adopt(
                DummyArgs(select=["automation/telegram_alert"]), self.root
            )
            self.assertEqual(adopt_code, ExitCode.CONFLICT_OR_INVALID.value)

    # ------------------------------------------------------------------
    # F8: Baseline content hash computation and uncommitted verify guard
    # ------------------------------------------------------------------
    def test_f8_baseline_content_hash_and_uncommitted_verify_guard(self) -> None:
        # 1. Deterministic content hash computation
        res_v1 = {"automation/a": {"desired": {"id": "a", "alias": "A1"}}}
        res_v2 = {"automation/a": {"desired": {"id": "a", "alias": "A2"}}}
        hash_v1 = compute_baseline_hash(res_v1)
        hash_v2 = compute_baseline_hash(res_v2)
        self.assertTrue(len(hash_v1) == 64)
        self.assertNotEqual(hash_v1, hash_v2)

        # 2. cmd_verify with uncommitted git modifications rejects checkpointing
        doc = ResourceDocument(
            kind="automation",
            key="dirty_auto",
            desired={
                "id": "dirty_auto",
                "alias": "Dirty Auto",
                "triggers": [],
                "actions": [],
            },
        )
        write_resource_atomic(self.root, doc)
        self.mock_client.automations["dirty_auto"] = doc.desired

        with (
            patch(
                "scripts.home_assistant.__main__.get_client",
                return_value=self.mock_client,
            ),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=" M home-assistant/automations/dirty_auto.yaml\n",
            )
            code = cmd_verify(DummyArgs(checkpoint=True), self.root)
            self.assertEqual(code, ExitCode.CONFLICT_OR_INVALID.value)


if __name__ == "__main__":
    unittest.main()
