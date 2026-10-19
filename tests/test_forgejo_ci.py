from __future__ import annotations

import base64
import copy
import hashlib
import io
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
from scripts.ci.operation import SupersededSource, current_source, pack, unpack
from scripts.ci.operation import main as operation_main
from scripts.ci.platform_backup import snapshot_database
from scripts.ci.policy import (
    DEPLOY_AUTHORS,
    SCHEDULES,
    VALIDATION,
    VALIDATION_COMMAND,
    configuration,
    defers_to_branch_push,
    find_reusable_validation,
    schedule_state,
    verify_request,
)
from scripts.ci.prepare_platform import certificate, clone_environment
from scripts.ci.state import Store, handler
from scripts.ci.woodpecker_settings import cron_changes

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


class CloneIdentityPreparationTest(unittest.TestCase):
    def test_clone_environment_uses_the_read_only_identity(self) -> None:
        environment = dict(
            line.split("=", 1)
            for line in clone_environment("repository-read-token").decode().splitlines()
        )
        self.assertEqual(
            environment["HOMELAB_FORGEJO_CLONE_USERNAME"], "homelab-ci-source"
        )
        self.assertEqual(
            environment["HOMELAB_FORGEJO_CLONE_TOKEN"], "repository-read-token"
        )

    def test_missing_or_injected_clone_credentials_fail_closed(self) -> None:
        for token in ["", "\nOTHER=value", "token\rOTHER=value", "token value"]:
            with self.subTest(token=token), self.assertRaises(ValueError):
                clone_environment(token)


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


class CurrentSourceTest(unittest.TestCase):
    def test_forgejo_tip_uses_read_only_api_identity(self) -> None:
        environment = {
            "CI_COMMIT_SHA": SHA,
            "FORGEJO_SOURCE_READ_TOKEN": "read-only-fixture",
        }
        response = io.BytesIO(json.dumps({"commit": {"id": SHA}}).encode())
        with (
            patch(
                "scripts.ci.operation.subprocess.check_output", return_value=SHA
            ) as git,
            patch(
                "scripts.ci.operation.urllib.request.urlopen", return_value=response
            ) as request,
        ):
            current_source(environment)
        self.assertEqual(git.call_count, 1)
        self.assertEqual(
            request.call_args.args[0].get_header("Authorization"),
            "token read-only-fixture",
        )

    def test_forgejo_tip_change_rejects_operation(self) -> None:
        environment = {
            "CI_COMMIT_SHA": SHA,
            "FORGEJO_SOURCE_READ_TOKEN": "read-only-fixture",
        }
        response = io.BytesIO(json.dumps({"commit": {"id": "b" * 40}}).encode())
        with (
            patch("scripts.ci.operation.subprocess.check_output", return_value=SHA),
            patch("scripts.ci.operation.urllib.request.urlopen", return_value=response),
            self.assertRaisesRegex(ValueError, "current main"),
        ):
            current_source(environment)

    def test_recovery_tip_uses_native_gitlab_clone_authentication(self) -> None:
        environment = {"CI_COMMIT_SHA": SHA, "CI_OPERATION_AUTHORITY": "gitlab"}
        with patch(
            "scripts.ci.operation.subprocess.check_output",
            side_effect=[SHA, SHA + " refs/heads/main\n"],
        ):
            current_source(environment)


class SupersededOperationTest(unittest.TestCase):
    def superseded_main(self, event: str) -> None:
        with (
            patch.dict(os.environ, {"CI_PIPELINE_EVENT": event, "CI_COMMIT_SHA": SHA}),
            patch("sys.argv", ["operation", "opnsense-plan"]),
            patch(
                "scripts.ci.operation.run",
                side_effect=SupersededSource(
                    "Operation source must be the current main commit"
                ),
            ),
        ):
            operation_main()

    def test_automatic_work_for_an_older_main_skips_instead_of_failing(self) -> None:
        for event in ["push", "cron"]:
            with self.subTest(event=event):
                self.superseded_main(event)

    def test_operator_deployment_for_an_older_main_still_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "current main"):
            self.superseded_main("deployment")


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
            self.assertEqual(
                workflow["steps"][0]["environment"]["CI_LINT_EXTERNAL"], "1"
            )
            self.assertEqual(
                workflow["steps"][0]["environment"]["TF_PLUGIN_CACHE_DIR"],
                "/tmp/tofu-plugin-cache",
            )
            self.assertTrue(VALIDATION_COMMAND.startswith("nix develop ./flake#ci "))
            self.assertEqual(
                workflow["steps"][0]["commands"],
                [VALIDATION_COMMAND],
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
                "registry-lock-import.yaml",
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

    def test_nixos_publish_is_manual_and_forgejo_sourced(self) -> None:
        operation = self.catalog["nixos-publish"]
        self.assertFalse(operation["automatic"])
        self.assertTrue(operation["mutation"])
        self.assertEqual(operation["group"], "nix-cache")
        self.assertEqual(
            operation["environment"]["ATTIC_TOKEN"],
            {"from_secret": "attic_token"},
        )
        self.assertEqual(
            operation["environment"]["FORGEJO_SOURCE_READ_TOKEN"],
            {"from_secret": "forgejo_source_read_token"},
        )
        self.assertTrue(
            all("nixos_publish.sh" in command for command in operation["commands"])
        )
        script = (ROOT / "scripts/ci/nixos_publish.sh").read_text()
        for marker in [
            "branches/main",
            "rev-parse HEAD",
            "does not match resolved tip",
            'cache.sh "$work/nixos-config" "$target_set"',
        ]:
            self.assertIn(marker, script)

    def test_acl_ssh_authority_is_exclusive_to_approved_opnsense_deployment(
        self,
    ) -> None:
        apply = self.catalog["opnsense-apply"]
        self.assertTrue(apply["mutation"])
        self.assertFalse(apply["automatic"])
        self.assertIn("OPNSENSE_ACL_SSH_KEY", apply["file_secrets"])
        for name, operation in self.catalog.items():
            if name != "opnsense-apply":
                self.assertNotIn("OPNSENSE_ACL_SSH_KEY", operation["environment"])
        result = self.deployment()
        workflow = next(
            yaml.safe_load(item["data"])
            for item in result["configs"]
            if item["name"] == "opnsense-apply.yaml"
        )
        self.assertEqual(workflow["labels"]["tier"], "deploy")
        self.assertEqual(
            workflow["steps"][0]["environment"]["OPNSENSE_ACL_SSH_KEY"],
            {"from_secret": "opnsense_acl_ssh_key"},
        )
        commands = apply["commands"]
        plan = next(
            i for i, command in enumerate(commands) if "agent_acl plan " in command
        )
        converge = next(
            i for i, command in enumerate(commands) if "agent_acl apply " in command
        )
        check = next(
            i for i, command in enumerate(commands) if "agent_acl check " in command
        )
        self.assertLess(plan, converge)
        self.assertLess(converge, check)
        self.assertLess(
            plan, commands.index("tofu -chdir=tofu/opnsense apply desired.tfplan")
        )

    def test_push_lock_import_requires_lock_change_or_unknown_diff(self) -> None:
        names = [
            item["name"]
            for item in configuration(
                self.request, self.catalog, current=SHA, changed_files=("docs/x.md",)
            )["configs"]
        ]
        self.assertNotIn("registry-lock-import.yaml", names)
        for changed in [
            ("registry/images.lock.json",),
            ("registry/images.inventory.json",),
            None,
        ]:
            names = [
                item["name"]
                for item in configuration(
                    self.request, self.catalog, current=SHA, changed_files=changed
                )["configs"]
            ]
            self.assertIn("registry-lock-import.yaml", names)

    def test_maintenance_cron_imports_the_committed_lock(self) -> None:
        self.request["pipeline"].update(event="cron", cron="maintenance")
        result = configuration(self.request, self.catalog, current=SHA)
        item = next(
            item
            for item in result["configs"]
            if item["name"] == "registry-lock-import.yaml"
        )
        workflow = yaml.safe_load(item["data"])
        self.assertEqual(workflow["depends_on"], [VALIDATION])
        self.assertEqual(workflow["labels"], {"tier": "deploy", "type": "docker"})

    def test_schedules_split_fast_maintenance_from_nightly_work(self) -> None:
        for name, targets in SCHEDULES.items():
            self.request["pipeline"].update(event="cron", cron=name)
            result = configuration(self.request, self.catalog, current=SHA)
            names = [item["name"].removesuffix(".yaml") for item in result["configs"]]
            self.assertEqual(sorted(names), sorted([VALIDATION, *targets]))
            for item in result["configs"][1:]:
                workflow = yaml.safe_load(item["data"])
                operation = self.catalog[item["name"].removesuffix(".yaml")]
                expected = (
                    "resource-" + operation["group"]
                    if operation["mutation"]
                    else "scheduled-read-only"
                )
                self.assertEqual(workflow["concurrency"]["group"], expected)
        self.assertNotIn("deploy-fleet-dry-run", SCHEDULES["maintenance"])
        self.assertIn("registry-promote-first-party", SCHEDULES["promote"])
        self.assertIn("cache-publish", SCHEDULES["maintenance"])
        self.request["pipeline"].update(event="cron", cron="unknown")
        self.assertEqual(
            len(configuration(self.request, self.catalog, current=SHA)["configs"]), 1
        )

    def test_operations_serialize_per_resource(self) -> None:
        result = configuration(self.request, self.catalog, current=SHA)
        groups = {
            item["name"]: yaml.safe_load(item["data"])["concurrency"]["group"]
            for item in result["configs"][1:]
        }
        self.assertEqual(
            groups["opnsense-plan.yaml"], "resource-opnsense-control-plane"
        )
        self.assertEqual(groups["cloudflare-plan.yaml"], "resource-cloudflare-tunnel")
        self.assertEqual(
            groups["registry-lock-import.yaml"], "resource-registry-content"
        )

    def test_cron_reuses_main_validation_and_reports_overlap(self) -> None:
        self.request["pipeline"].update(event="cron", cron="promote")
        reused = configuration(
            self.request, self.catalog, current=SHA, validated_main=True
        )
        names = [item["name"] for item in reused["configs"]]
        self.assertNotIn(VALIDATION + ".yaml", names)
        for item in reused["configs"]:
            self.assertNotIn("depends_on", yaml.safe_load(item["data"]))
        overlap = configuration(
            self.request, self.catalog, current=SHA, schedule_overlap=True
        )
        self.assertEqual(
            [item["name"] for item in overlap["configs"]], ["schedule-overlap.yaml"]
        )
        step = yaml.safe_load(overlap["configs"][0]["data"])["steps"][0]
        self.assertEqual(step["commands"][-1], "exit 1")

    def test_schedule_state_reads_sender_and_validation(self) -> None:
        request = {
            "pipeline": {"cron": "promote", "commit": SHA, "number": 9, "event": "cron"}
        }
        pipelines = [
            {
                "number": 8,
                "event": "cron",
                "sender": "promote",
                "status": "running",
                "commit": SHA,
                "branch": "main",
                "created": 990,
            },
            {
                "number": 7,
                "event": "cron",
                "sender": "maintenance",
                "status": "running",
                "commit": SHA,
                "branch": "main",
                "created": 980,
            },
            {
                "number": 6,
                "event": "push",
                "sender": "rupan",
                "status": "success",
                "commit": SHA,
                "branch": "main",
                "created": 970,
            },
        ]
        details = {
            8: {"workflows": [{"name": VALIDATION, "state": "running"}]},
            7: {"workflows": [{"name": VALIDATION, "state": "failure"}]},
            6: {"workflows": [{"name": VALIDATION, "state": "success"}]},
        }
        self.assertEqual(
            schedule_state(request, pipelines, details.__getitem__, 1000), (True, True)
        )
        idle = [dict(item, status="success") for item in pipelines]
        idle[2]["created"] = 1000 - 90000
        self.assertEqual(
            schedule_state(request, idle, details.__getitem__, 1000), (False, False)
        )

    def test_same_repository_prs_defer_to_the_branch_push(self) -> None:
        pull = {
            "state": "open",
            "base": {"ref": "main"},
            "head": {"sha": SHA, "repo": {"full_name": "JEFF7712/homelab"}},
        }
        pipeline = {"commit": SHA, "from_fork": False}
        self.assertTrue(defers_to_branch_push(pipeline, pull))
        for change in (
            {"from_fork": True},
            {"commit": "b" * 40},
        ):
            self.assertFalse(defers_to_branch_push(dict(pipeline, **change), pull))
        fork = copy.deepcopy(pull)
        fork["head"]["repo"]["full_name"] = "someone/homelab"
        self.assertFalse(defers_to_branch_push(pipeline, fork))
        closed = dict(pull, state="closed")
        self.assertFalse(defers_to_branch_push(pipeline, closed))

    def test_scheduled_crons_match_policy(self) -> None:
        crons = json.loads((ROOT / "config/ci/woodpecker-crons.json").read_text())
        self.assertEqual(set(crons), set(SCHEDULES))
        settings = json.loads(
            (ROOT / "config/ci/woodpecker-repository.json").read_text()
        )
        # Woodpecker cancels across schedules on the same branch, so overlap
        # is detected per schedule by the policy instead.
        self.assertNotIn("cron", settings["cancel_previous_pipeline_events"])

    def test_cron_reconciliation_creates_enabled_and_removes_unowned(self) -> None:
        desired = {"nightly": {"schedule": "17 8 * * *", "branch": "main"}}
        live = [
            {
                "id": 1,
                "name": "maintenance",
                "schedule": "*/30 * * * *",
                "branch": "main",
                "enabled": True,
            },
        ]
        changes = cron_changes(desired, live)
        self.assertEqual(changes[0][0:2], ("create", "nightly"))
        self.assertIs(changes[0][2]["enabled"], True)
        self.assertEqual(changes[1], ("delete", "maintenance", {"id": 1}))
        disabled = [dict(live[0], name="nightly", schedule="17 8 * * *", enabled=False)]
        self.assertEqual(cron_changes(desired, disabled)[0][0], "update")
        enabled = [dict(disabled[0], enabled=True)]
        self.assertEqual(cron_changes(desired, enabled), [])

    def test_deployment_requires_fresh_current_main_and_operator(self) -> None:
        result = self.deployment()
        workflow = yaml.safe_load(result["configs"][0]["data"])
        self.assertEqual(workflow["labels"], {"tier": "deploy", "type": "docker"})
        self.assertEqual(
            workflow["concurrency"]["group"],
            "resource-" + self.catalog["opnsense-apply"]["group"],
        )
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

    def test_machine_user_is_an_authorized_deploy_operator(self) -> None:
        self.assertEqual(DEPLOY_AUTHORS, {"rupan", "JEFF7712", "homelab-agent"})
        saved = copy.deepcopy(self.request)
        self.request["pipeline"]["author"] = "homelab-agent"
        result = self.deployment()
        workflow = yaml.safe_load(result["configs"][0]["data"])
        self.assertEqual(workflow["labels"], {"tier": "deploy", "type": "docker"})
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

    def test_pr_deduplication_reuses_passed_push_validation(self) -> None:
        pr_request = {
            "repo": {"id": 1, "owner": "JEFF7712", "name": "homelab"},
            "pipeline": {
                "id": 441,
                "number": 441,
                "event": "pull_request",
                "ref": "refs/pull/89/head",
                "commit": SHA,
                "author": "rupan",
                "from_fork": False,
            },
        }
        pr_info = {
            "state": "open",
            "base": {"ref": "main", "sha": "b" * 40},
            "head": {"ref": "feature", "sha": SHA},
            "merge_base": "b" * 40,
        }
        pipelines = [
            {
                "id": 440,
                "number": 440,
                "event": "push",
                "ref": "refs/heads/feature",
                "commit": SHA,
                "status": "success",
                "finished": 1000,
                "created": 900,
                "from_fork": False,
            }
        ]
        reusable = find_reusable_validation(
            pr_request,
            self.catalog,
            now=1050,
            pipelines=pipelines,
            pr_getter=lambda _: pr_info,
        )
        self.assertIsNotNone(reusable)
        self.assertEqual(reusable["id"], 440)

        result = configuration(pr_request, self.catalog, reusable=reusable)
        self.assertEqual(len(result["configs"]), 1)
        self.assertEqual(result["configs"][0]["name"], "pr-validation-reused.yaml")
        workflow = yaml.safe_load(result["configs"][0]["data"])
        self.assertEqual(workflow["steps"][0]["name"], "pr-validation-reused")
        self.assertIn("440", workflow["steps"][0]["commands"][0])
        self.assertNotEqual(workflow["steps"][0]["name"], VALIDATION)

    def test_pr_deduplication_reuses_active_push_validation(self) -> None:
        pr_request = {
            "repo": {"id": 1, "owner": "JEFF7712", "name": "homelab"},
            "pipeline": {
                "id": 441,
                "number": 441,
                "event": "pull_request",
                "ref": "refs/pull/89/head",
                "commit": SHA,
                "author": "rupan",
                "from_fork": False,
            },
        }
        pr_info = {
            "state": "open",
            "base": {"ref": "main"},
            "head": {"sha": SHA},
        }
        for status in ("pending", "running"):
            pipelines = [
                {
                    "id": 440,
                    "number": 440,
                    "event": "push",
                    "commit": SHA,
                    "status": status,
                    "created": 1000,
                    "from_fork": False,
                }
            ]
            reusable = find_reusable_validation(
                pr_request,
                self.catalog,
                now=1050,
                pipelines=pipelines,
                pr_getter=lambda _: pr_info,
            )
            self.assertIsNotNone(reusable)
            self.assertEqual(reusable["id"], 440)

    def test_pr_deduplication_invalidation_guards(self) -> None:
        base_pr_request = {
            "repo": {"id": 1, "owner": "JEFF7712", "name": "homelab"},
            "pipeline": {
                "id": 441,
                "number": 441,
                "event": "pull_request",
                "ref": "refs/pull/89/head",
                "commit": SHA,
                "author": "rupan",
                "from_fork": False,
            },
        }
        base_pr_info = {
            "state": "open",
            "base": {"ref": "main"},
            "head": {"sha": SHA},
        }
        base_pipeline = {
            "id": 440,
            "number": 440,
            "event": "push",
            "commit": SHA,
            "status": "success",
            "finished": 1000,
            "created": 900,
            "from_fork": False,
        }

        # Fork PR
        fork_request = copy.deepcopy(base_pr_request)
        fork_request["pipeline"]["from_fork"] = True
        self.assertIsNone(
            find_reusable_validation(
                fork_request,
                self.catalog,
                now=1050,
                pipelines=[base_pipeline],
                pr_getter=lambda _: base_pr_info,
            )
        )

        # Base branch changed (targeting non-main)
        non_main_pr = copy.deepcopy(base_pr_info)
        non_main_pr["base"]["ref"] = "develop"
        self.assertIsNone(
            find_reusable_validation(
                base_pr_request,
                self.catalog,
                now=1050,
                pipelines=[base_pipeline],
                pr_getter=lambda _: non_main_pr,
            )
        )

        # Force push / commit mismatch in PR
        out_of_sync_pr = copy.deepcopy(base_pr_info)
        out_of_sync_pr["head"]["sha"] = "c" * 40
        self.assertIsNone(
            find_reusable_validation(
                base_pr_request,
                self.catalog,
                now=1050,
                pipelines=[base_pipeline],
                pr_getter=lambda _: out_of_sync_pr,
            )
        )

        # PR closed
        closed_pr = copy.deepcopy(base_pr_info)
        closed_pr["state"] = "closed"
        self.assertIsNone(
            find_reusable_validation(
                base_pr_request,
                self.catalog,
                now=1050,
                pipelines=[base_pipeline],
                pr_getter=lambda _: closed_pr,
            )
        )

        # Expired validation (> 86400s)
        expired_pipeline = copy.deepcopy(base_pipeline)
        expired_pipeline["finished"] = 1000
        self.assertIsNone(
            find_reusable_validation(
                base_pr_request,
                self.catalog,
                now=1000 + 86401,
                pipelines=[expired_pipeline],
                pr_getter=lambda _: base_pr_info,
            )
        )

        # Failed validation
        failed_pipeline = copy.deepcopy(base_pipeline)
        failed_pipeline["status"] = "failure"
        self.assertIsNone(
            find_reusable_validation(
                base_pr_request,
                self.catalog,
                now=1050,
                pipelines=[failed_pipeline],
                pr_getter=lambda _: base_pr_info,
            )
        )

        # Self match (pipeline 441 matches candidate 441)
        self_pipeline = copy.deepcopy(base_pipeline)
        self_pipeline["id"] = 441
        self.assertIsNone(
            find_reusable_validation(
                base_pr_request,
                self.catalog,
                now=1050,
                pipelines=[self_pipeline],
                pr_getter=lambda _: base_pr_info,
            )
        )

        # Pipeline created before policy start time
        old_policy_pipeline = copy.deepcopy(base_pipeline)
        old_policy_pipeline["created"] = 800
        self.assertIsNone(
            find_reusable_validation(
                base_pr_request,
                self.catalog,
                now=1050,
                pipelines=[old_policy_pipeline],
                pr_getter=lambda _: base_pr_info,
                policy_start_time=850,
            )
        )

    def test_pr_injects_merge_base_and_main_injects_full_validation(self) -> None:
        pr_request = {
            "repo": {"id": 1, "owner": "JEFF7712", "name": "homelab"},
            "pipeline": {
                "id": 441,
                "event": "pull_request",
                "ref": "refs/pull/89/head",
                "commit": SHA,
                "author": "rupan",
            },
        }
        result = configuration(pr_request, self.catalog, merge_base="b" * 40)
        workflow = yaml.safe_load(result["configs"][0]["data"])
        self.assertEqual(workflow["steps"][0]["environment"]["CI_MERGE_BASE"], "b" * 40)

        main_request = {
            "repo": {"id": 1, "owner": "JEFF7712", "name": "homelab"},
            "pipeline": {
                "id": 442,
                "event": "push",
                "ref": "refs/heads/main",
                "commit": SHA,
                "author": "rupan",
            },
        }
        result_main = configuration(main_request, self.catalog, current=SHA)
        workflow_main = yaml.safe_load(result_main["configs"][0]["data"])
        self.assertEqual(
            workflow_main["steps"][0]["environment"]["CI_FULL_VALIDATION"], "1"
        )

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

    def test_nix_agent_promotion_is_scoped_and_auto_merges_after_required_ci(
        self,
    ) -> None:
        operation = self.catalog["registry-promote-nix-agent"]
        self.assertTrue(operation["mutation"])
        self.assertEqual(
            operation["environment"]["FORGEJO_SOURCE_READ_TOKEN"],
            {"from_secret": "forgejo_source_read_token"},
        )
        self.assertEqual(operation["environment"]["TF_HTTP_USERNAME"], "deploy")
        self.assertEqual(
            operation["environment"]["TF_HTTP_PASSWORD"],
            {"from_secret": "state_deploy_password"},
        )
        self.assertEqual(
            operation["environment"]["STATE_CA_FILE"],
            {"from_secret": "state_ca_file"},
        )
        self.assertIn("STATE_CA_FILE", operation["file_secrets"])
        self.assertEqual(
            operation["environment"]["CI_AUTO_MERGE_PROMOTION"], "nix-agent-site"
        )
        command = "\n".join(operation["commands"])
        self.assertIn("--only apps/nix-agent-site", command)
        self.assertIn("--allow-observed-drift", command)
        self.assertIn("just check-registry", command)

        self.request["pipeline"].update(
            event="cron",
            ref="refs/heads/main",
            cron="promote",
            from_fork=False,
        )
        result = configuration(self.request, self.catalog, current=SHA)
        workflows = {item["name"] for item in result["configs"]}
        self.assertIn("registry-promote-nix-agent.yaml", workflows)
        self.assertIn("registry-promote-first-party.yaml", workflows)

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
                        check=False,
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
                    check=False,
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
