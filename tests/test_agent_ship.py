from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import unittest
import unittest.mock
import urllib.error
from pathlib import Path
from unittest.mock import patch

from scripts.agent import ship
from scripts.agent.ship import (
    PATCH_BEGIN,
    PATCH_END,
    REQUIRED_CONTEXT,
    ShipError,
    extract_patch,
    kustomization_state,
    kustomizations_for,
    load_routes,
    plan_operations,
    required_status,
)

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


class ShipPlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = json.loads((ROOT / "config/ci/operations.json").read_text())
        self.routes = load_routes()

    def test_routes_name_only_catalog_operations(self) -> None:
        for route in self.routes:
            for operation in route.operations:
                with self.subTest(route=route.name, operation=operation):
                    self.assertIn(operation, self.catalog)

    def test_application_changes_run_their_auto_route(self) -> None:
        plan = plan_operations(
            ["services/led-matrix/ha_matrix_streamer.py", "home-assistant/a.yaml"],
            self.routes,
            "auto",
            self.catalog,
        )
        self.assertEqual(
            plan.run,
            [
                "matrix-publish",
                "deploy-home-assistant-dry-run",
                "deploy-home-assistant",
            ],
        )
        self.assertEqual(plan.held, {})

    def test_infrastructure_routes_run_automatically(self) -> None:
        changed = ["flake/hosts/homelab-04/default.nix", "tofu/opnsense/rules.tf"]
        plan = plan_operations(changed, self.routes, "auto", self.catalog)
        self.assertIn("deploy-ci-host", plan.run)
        self.assertIn("opnsense-apply", plan.run)
        self.assertEqual(plan.held, {})
        explicit = plan_operations(changed, self.routes, "opnsense-apply", self.catalog)
        self.assertEqual(explicit.run, ["opnsense-plan", "opnsense-apply"])
        self.assertEqual(
            plan_operations(changed, self.routes, "none", self.catalog).run, []
        )

    def test_shared_inventory_routes_both_consumers(self) -> None:
        for path in ("inventory/hosts.nix", "inventory/networks.nix"):
            with self.subTest(path=path):
                plan = plan_operations([path], self.routes, "auto", self.catalog)
                self.assertEqual(
                    set(plan.run),
                    {
                        "deploy-fleet-dry-run",
                        "deploy-fleet",
                        "opnsense-plan",
                        "opnsense-apply",
                    },
                )
                self.assertEqual(plan.held, {})

    def test_tunnel_ingress_changes_apply_the_cloudflare_stack(self) -> None:
        plan = plan_operations(
            ["gitops/cloudflare/ingress-config.yaml"], self.routes, "auto", self.catalog
        )
        self.assertEqual(plan.run, ["cloudflare-plan", "cloudflare-apply"])
        plan = plan_operations(
            ["gitops/cloudflare/approvals-service.yaml"],
            self.routes,
            "auto",
            self.catalog,
        )
        self.assertEqual(plan.run, [])

    def test_unknown_operation_is_rejected(self) -> None:
        with self.assertRaises(ShipError):
            plan_operations([], self.routes, "rm-rf", self.catalog)

    def test_docs_only_change_deploys_nothing(self) -> None:
        plan = plan_operations(["docs/a.md"], self.routes, "auto", self.catalog)
        self.assertEqual((plan.run, plan.held), ([], {}))

    def test_ci_host_changes_do_not_also_deploy_the_fleet(self) -> None:
        plan = plan_operations(
            ["flake/modules/woodpecker.nix"], self.routes, "auto", self.catalog
        )
        self.assertEqual(plan.run, ["deploy-ci-host-dry-run", "deploy-ci-host"])
        plan = plan_operations(
            ["flake/modules/woodpecker.nix", "flake/modules/k3s-server.nix"],
            self.routes,
            "auto",
            self.catalog,
        )
        self.assertEqual(plan.run, ["deploy-fleet-dry-run", "deploy-fleet"])


class ShipTimingTest(unittest.TestCase):
    def test_queue_and_execution_have_separate_measurements(self):
        self.assertEqual(
            ship.pipeline_timings({"created": 100, "started": 520, "finished": 550}),
            {"queue_seconds": 420, "execution_seconds": 30},
        )
        self.assertEqual(ship.pipeline_timings({"created": 0, "started": 0}), {})
        self.assertEqual(ship.pipeline_timings({"created": 100, "started": 99}), {})


class ShipNetworkTest(unittest.TestCase):
    def test_refused_connections_are_retried_then_reported(self) -> None:
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.status = 200
        response.__enter__.return_value.read.return_value = b'{"ok": true}'
        refused = urllib.error.URLError(ConnectionRefusedError(111, "refused"))
        with (
            patch.object(
                ship.urllib.request, "urlopen", side_effect=[refused, refused, response]
            ),
            patch.object(ship.time, "sleep"),
        ):
            self.assertEqual(ship.http("GET", "http://x", {}), (200, {"ok": True}))
        with (
            patch.object(ship.urllib.request, "urlopen", side_effect=refused),
            patch.object(ship.time, "sleep"),
            self.assertRaisesRegex(ShipError, "unreachable"),
        ):
            ship.http("GET", "http://x", {})


class ShipOperatorTest(unittest.TestCase):
    def shipper(self) -> ship.Shipper:
        shipper = ship.Shipper.__new__(ship.Shipper)
        shipper.woodpecker = unittest.mock.Mock()
        shipper.woodpecker.deploy.side_effect = ShipError("stop after deploy")
        return shipper

    def approval_shipper(self) -> ship.Shipper:
        shipper = self.shipper()
        shipper.summary = {
            "branch": "feat/x",
            "pull_request": "https://git/pulls/9",
            "operations": [{"operation": "deploy-fleet-dry-run", "pipeline": 41}],
        }
        shipper.deadline = ship.time.monotonic() + 60
        shipper.say = unittest.mock.Mock()
        shipper.woodpecker.headers = {"Authorization": "Bearer agent"}
        shipper.woodpecker.pipeline.side_effect = ShipError("following deployment")
        return shipper

    def test_held_operations_wait_for_dashboard_approval(self) -> None:
        shipper = self.approval_shipper()
        responses = [
            (201, {"id": "abc", "url": "https://approvals/r/abc"}),
            (200, {"state": "pending"}),
            (200, {"state": "approved", "pipeline": 77}),
        ]
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(ship.Path, "home", return_value=Path("/nonexistent")),
            patch.object(ship, "http", side_effect=responses) as http,
            patch.object(ship.time, "sleep"),
            self.assertRaisesRegex(ShipError, "following deployment"),
        ):
            number = shipper.approved(10, "provision-darkbit-publisher")
            shipper.woodpecker.pipeline(number)
        shipper.woodpecker.deploy.assert_not_called()
        shipper.woodpecker.pipeline.assert_called_once_with(77)
        method, url, headers, body = http.call_args_list[0].args
        self.assertEqual((method, url), ("POST", f"{ship.APPROVALS}/api/requests"))
        self.assertEqual(headers, {"Authorization": "Bearer agent"})
        self.assertEqual(body["operation"], "provision-darkbit-publisher")
        self.assertEqual(body["parent"], 10)
        self.assertEqual(body["evidence"], [41])

    def test_superseded_approvals_retry_on_the_new_main(self) -> None:
        shipper = self.approval_shipper()
        responses = [
            (201, {"id": "abc", "url": "https://approvals/r/abc"}),
            (200, {"state": "superseded"}),
        ]
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(ship.Path, "home", return_value=Path("/nonexistent")),
            patch.object(ship, "http", side_effect=responses),
            self.assertRaisesRegex(ShipError, ship.SUPERSEDED),
        ):
            shipper.approved(10, "provision-darkbit-publisher")

    def test_declined_approvals_stop_the_ship(self) -> None:
        shipper = self.approval_shipper()
        responses = [
            (201, {"id": "abc", "url": "https://approvals/r/abc"}),
            (200, {"state": "declined"}),
        ]
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(ship.Path, "home", return_value=Path("/nonexistent")),
            patch.object(ship, "http", side_effect=responses),
            self.assertRaisesRegex(ShipError, "declined on the approvals dashboard"),
        ):
            shipper.approved(10, "provision-darkbit-publisher")
        shipper.woodpecker.pipeline.assert_not_called()

    def test_previously_operator_only_operations_use_agent_identity(self) -> None:
        shipper = self.shipper()
        with (
            patch.dict("os.environ", {"WOODPECKER_OPERATOR_TOKEN": "unused"}),
            self.assertRaisesRegex(ShipError, "stop after deploy"),
        ):
            shipper.run_operation(10, "provision-darkbit-publisher")
        shipper.woodpecker.deploy.assert_called_once_with(
            10, "provision-darkbit-publisher"
        )

    GRANT = "d" * 64
    REJECTION = (
        "Woodpecker POST: HTTP 400 {'message': '... <p>Message: Human approval "
        "required: authorization or storage source changed (grant "
        + "d" * 64
        + "): scripts/ci/policy.py, .sops.yaml.</p>'}"
    )

    def test_protected_changes_are_granted_then_deployed_as_the_agent(self) -> None:
        shipper = self.approval_shipper()
        shipper.woodpecker.deploy.side_effect = [
            ShipError(self.REJECTION),
            {"number": 88},
        ]
        responses = [
            (201, {"id": "g", "url": "https://approvals/r/g"}),
            (200, {"state": "approved", "pipeline": None}),
        ]
        with (
            patch.object(ship, "http", side_effect=responses) as http,
            self.assertRaisesRegex(ShipError, "following deployment"),
        ):
            shipper.run_operation(10, "deploy-ci-host")
        body = http.call_args_list[0].args[3]
        self.assertEqual(body["digest"], self.GRANT)
        self.assertEqual(body["paths"], ["scripts/ci/policy.py", ".sops.yaml"])
        self.assertEqual(shipper.woodpecker.deploy.call_count, 2)
        shipper.woodpecker.pipeline.assert_called_once_with(88)

    def test_a_granted_digest_rejected_again_fails_instead_of_looping(self) -> None:
        shipper = self.approval_shipper()
        shipper.woodpecker.deploy.side_effect = [
            ShipError(self.REJECTION),
            ShipError(self.REJECTION),
        ]
        responses = [
            (201, {"id": "g", "url": "https://approvals/r/g"}),
            (200, {"state": "approved", "pipeline": None}),
        ]
        with (
            patch.object(ship, "http", side_effect=responses),
            self.assertRaisesRegex(ShipError, "still rejects approved grant"),
        ):
            shipper.run_operation(10, "deploy-ci-host")

    def test_agent_operations_use_the_agent_token(self) -> None:
        shipper = self.shipper()
        with self.assertRaisesRegex(ShipError, "stop after deploy"):
            shipper.run_operation(10, "matrix-publish")
        shipper.woodpecker.deploy.assert_called_once_with(10, "matrix-publish")

    def test_risky_routine_operation_falls_back_to_passkey_approval(self) -> None:
        shipper = self.approval_shipper()
        shipper.woodpecker.deploy.side_effect = ShipError(
            "Human approval required: authorization source changed"
        )
        responses = [
            (201, {"id": "abc", "url": "https://approvals/r/abc"}),
            (200, {"state": "approved", "pipeline": 77}),
        ]
        with (
            patch.object(ship, "http", side_effect=responses),
            self.assertRaisesRegex(ShipError, "following deployment"),
        ):
            shipper.run_operation(10, "deploy-fleet")
        shipper.woodpecker.deploy.assert_called_once_with(10, "deploy-fleet")
        shipper.woodpecker.pipeline.assert_called_once_with(77)


class ShipPlanRetryTest(unittest.TestCase):
    def test_superseded_operations_resume_on_the_new_main(self) -> None:
        shipper = ship.Shipper.__new__(ship.Shipper)
        shipper.summary = {"operations": [], "promotions": []}
        shipper.say = lambda message: None
        shipper.remaining = lambda: 60.0
        parents = iter([("a" * 40, 10), ("b" * 40, 11)])
        shipper.main_pipeline = lambda merged: next(parents)
        shipper.completed_operations = lambda parent: set()
        calls = []

        def run_operation(parent, operation):
            calls.append((parent, operation))
            if (parent, operation) == (10, "deploy"):
                raise ShipError("deploy failed\n" + ship.SUPERSEDED)
            return {"pipeline": len(calls), "log": ""}

        shipper.run_operation = run_operation
        catalog = {"dry-run": {}, "deploy": {"prerequisite": "dry-run"}}
        shipper.run_plan(["dry-run", "deploy"], "c" * 40, catalog)
        self.assertEqual(
            calls, [(10, "dry-run"), (10, "deploy"), (11, "dry-run"), (11, "deploy")]
        )
        self.assertEqual(shipper.summary["validation_pipeline"], 11)

    def test_other_operation_failures_are_not_retried(self) -> None:
        shipper = ship.Shipper.__new__(ship.Shipper)
        shipper.summary = {"operations": [], "promotions": []}
        shipper.say = lambda message: None
        shipper.remaining = lambda: 60.0
        shipper.main_pipeline = lambda merged: ("a" * 40, 10)
        shipper.completed_operations = lambda parent: set()

        def run_operation(parent, operation):
            raise ShipError("deploy failed: HTTP 401")

        shipper.run_operation = run_operation
        with self.assertRaisesRegex(ShipError, "401"):
            shipper.run_plan(["deploy"], "c" * 40, {"deploy": {}})

    def test_prerequisite_already_run_by_main_pipeline_is_skipped(self) -> None:
        shipper = ship.Shipper.__new__(ship.Shipper)
        shipper.summary = {"operations": [], "promotions": []}
        shipper.say = lambda message: None
        shipper.remaining = lambda: 60.0
        shipper.main_pipeline = lambda merged: ("a" * 40, 10)
        shipper.woodpecker = unittest.mock.Mock()
        shipper.woodpecker.pipelines_for.return_value = []
        shipper.woodpecker.pipeline.return_value = {
            "workflows": [
                {"name": "validation-v2", "state": "success"},
                {"name": "opnsense-plan", "state": "success"},
                {"name": "cloudflare-plan", "state": "failure"},
            ]
        }
        calls = []

        def run_operation(parent, operation):
            calls.append((parent, operation))
            return {"pipeline": 20, "log": ""}

        shipper.run_operation = run_operation
        catalog = {
            "opnsense-plan": {},
            "opnsense-apply": {"prerequisite": "opnsense-plan"},
            "cloudflare-plan": {},
        }
        shipper.run_plan(
            ["opnsense-plan", "opnsense-apply", "cloudflare-plan"], "c" * 40, catalog
        )
        self.assertEqual(calls, [(10, "opnsense-apply"), (10, "cloudflare-plan")])
        self.assertEqual(
            shipper.summary["operations"][0],
            {"operation": "opnsense-plan", "pipeline": 10},
        )

    def test_operations_another_agent_completed_are_skipped(self) -> None:
        shipper = ship.Shipper.__new__(ship.Shipper)
        shipper.woodpecker = unittest.mock.Mock()
        shipper.woodpecker.pipeline.return_value = {"commit": "c" * 40, "workflows": []}
        shipper.woodpecker.pipelines_for.return_value = [
            {
                "event": "deployment",
                "parent": 10,
                "status": "success",
                "deploy_to": "deploy-ci-host-dry-run",
            },
            {
                "event": "deployment",
                "parent": 10,
                "status": "failure",
                "deploy_to": "deploy-ci-host",
            },
            {
                "event": "deployment",
                "parent": 9,
                "status": "success",
                "deploy_to": "deploy-fleet",
            },
        ]
        self.assertEqual(shipper.completed_operations(10), {"deploy-ci-host-dry-run"})

    def test_a_concurrent_duplicate_run_counts_as_completed(self) -> None:
        shipper = ship.Shipper.__new__(ship.Shipper)
        shipper.summary = {"operations": [], "promotions": []}
        shipper.say = lambda message: None
        shipper.remaining = lambda: 60.0
        shipper.main_pipeline = lambda merged: ("a" * 40, 10)
        shipper.completed_operations = lambda parent: set()
        calls = []

        def run_operation(parent, operation):
            calls.append(operation)
            if operation == "dry-run":
                raise ShipError("dry-run failed\nValueError: " + ship.ALREADY_COMPLETED)
            return {"pipeline": 21, "log": ""}

        shipper.run_operation = run_operation
        catalog = {"dry-run": {}, "deploy": {"prerequisite": "dry-run"}}
        shipper.run_plan(["dry-run", "deploy"], "c" * 40, catalog)
        self.assertEqual(calls, ["dry-run", "deploy"])
        self.assertEqual(
            shipper.summary["operations"][0], {"operation": "dry-run", "pipeline": 10}
        )


class ShipMainPipelineTest(unittest.TestCase):
    def shipper(self, statuses: list[str]) -> tuple[ship.Shipper, list[int]]:
        shipper = ship.Shipper.__new__(ship.Shipper)
        shipper.say = lambda message: None
        shipper.remaining = lambda: 60.0
        shipper.forgejo = unittest.mock.Mock()
        shipper.forgejo.call.return_value = {"commit": {"id": SHA}}
        pipelines = [
            {"number": 10, "event": "push", "branch": "main", "status": statuses[0]}
        ]
        restarts: list[int] = []

        def restart(number: int) -> dict[str, int]:
            restarts.append(number)
            created = len(pipelines) + 10
            pipelines.append(
                {
                    "number": created,
                    "event": "push",
                    "branch": "main",
                    "status": statuses[len(pipelines)],
                }
            )
            return {"number": created}

        shipper.woodpecker = unittest.mock.Mock()
        shipper.woodpecker.pipelines_for = lambda sha: list(pipelines)
        shipper.woodpecker.restart = restart
        return shipper, restarts

    def test_killed_tip_pipeline_is_restarted_once(self) -> None:
        shipper, restarts = self.shipper(["killed", "success"])
        with patch.object(ship.time, "sleep"):
            self.assertEqual(shipper.main_pipeline(SHA), (SHA, 11))
        self.assertEqual(restarts, [10])

    def test_killed_again_after_restart_fails_instead_of_waiting(self) -> None:
        shipper, restarts = self.shipper(["killed", "killed"])
        with (
            patch.object(ship.time, "sleep"),
            self.assertRaisesRegex(ShipError, "killed again"),
        ):
            shipper.main_pipeline(SHA)
        self.assertEqual(restarts, [10])

    def test_failed_unrelated_plan_does_not_block_successful_source_validation(
        self,
    ) -> None:
        shipper, _ = self.shipper(["failure"])
        shipper.woodpecker.failure_report.return_value = "source validation failed"
        shipper.woodpecker.pipeline.return_value = {
            "workflows": [
                {"name": "validation-v2", "state": "success"},
                {"name": "cloudflare-plan", "state": "failure"},
            ]
        }
        self.assertEqual(shipper.main_pipeline(SHA), (SHA, 10))
        shipper.woodpecker.pipeline.return_value["workflows"][0]["state"] = "failure"
        with self.assertRaisesRegex(ShipError, "successful parent"):
            shipper.main_pipeline(SHA)


class ShipResumeTest(unittest.TestCase):
    def shipper(self) -> ship.Shipper:
        from argparse import Namespace

        shipper = ship.Shipper.__new__(ship.Shipper)
        shipper.args = Namespace(deploy="auto")
        shipper.summary = {"operations": []}
        shipper.say = unittest.mock.Mock()
        shipper.git = unittest.mock.Mock(
            side_effect=["", "", SHA, "b" * 40, "services/led-matrix/server.py"]
        )
        shipper.contains = unittest.mock.Mock(return_value=True)
        shipper.run_plan = unittest.mock.Mock()
        shipper.push = unittest.mock.Mock()
        shipper.pull_request = unittest.mock.Mock()
        return shipper

    def test_merged_deployment_resumes_without_push_or_new_pr(self) -> None:
        shipper = self.shipper()
        summary = shipper.resume(Path("/repo"), SHA)
        self.assertEqual(summary["state"], "deployed")
        self.assertEqual(shipper.run_plan.call_args.args[:2], (["matrix-publish"], SHA))
        shipper.push.assert_not_called()
        shipper.pull_request.assert_not_called()

    def test_unmerged_revision_is_rejected(self) -> None:
        shipper = self.shipper()
        shipper.contains.return_value = False
        with self.assertRaisesRegex(ShipError, "already merged"):
            shipper.resume(Path("/repo"), SHA)
        shipper.run_plan.assert_not_called()

    def test_dirty_checkout_is_rejected_before_delivery(self) -> None:
        shipper = self.shipper()
        shipper.git.side_effect = [" M scripts/ci/policy.py"]
        with self.assertRaisesRegex(ShipError, "tracked changes"):
            shipper.resume(Path("/repo"), SHA)
        shipper.run_plan.assert_not_called()


class ShipRolloutTest(unittest.TestCase):
    def test_kustomizations_follow_changed_paths(self) -> None:
        items = [
            {"metadata": {"name": "media"}, "spec": {"path": "./gitops/media"}},
            {
                "metadata": {"name": "mediaserver"},
                "spec": {"path": "./gitops/mediaserver"},
            },
            {"metadata": {"name": "voice"}, "spec": {"path": "./gitops/voice"}},
        ]
        self.assertEqual(
            kustomizations_for(["gitops/media/jellyfin.yaml"], items), ["media"]
        )
        self.assertEqual(kustomizations_for(["docs/a.md"], items), [])

    def test_kustomization_ready_requires_applied_revision(self) -> None:
        ready = {"type": "Ready", "status": "True", "message": "Applied"}
        stale = {
            "status": {
                "lastAppliedRevision": "main@sha1:" + "b" * 40,
                "conditions": [ready],
            }
        }
        fresh = {
            "status": {"lastAppliedRevision": "main@sha1:" + SHA, "conditions": [ready]}
        }
        includes = {SHA, "c" * 40}.__contains__
        self.assertFalse(kustomization_state(stale, includes)[0])
        self.assertTrue(kustomization_state(fresh, includes)[0])
        descendant = {
            "status": {
                "lastAppliedRevision": "main@sha1:" + "c" * 40,
                "conditions": [ready],
            }
        }
        self.assertTrue(kustomization_state(descendant, includes)[0])
        failing = {
            "status": {
                "lastAppliedRevision": "main@sha1:" + SHA,
                "conditions": [
                    {"type": "Ready", "status": "False", "message": "HealthCheckFailed"}
                ],
            }
        }
        self.assertEqual(
            kustomization_state(failing, includes), (False, "HealthCheckFailed")
        )

    def test_required_status_uses_latest_required_context(self) -> None:
        statuses = [
            {
                "context": REQUIRED_CONTEXT,
                "status": "pending",
                "updated_at": "2026-10-18T01:00:00Z",
                "id": 1,
            },
            {
                "context": REQUIRED_CONTEXT,
                "status": "success",
                "updated_at": "2026-10-18T01:05:00Z",
                "id": 2,
            },
            {
                "context": "ci/woodpecker/pr-validation-reused",
                "status": "failure",
                "updated_at": "2026-10-18T01:06:00Z",
                "id": 3,
            },
        ]
        self.assertEqual(required_status(statuses), "success")
        self.assertEqual(required_status([]), "missing")

    def test_promotion_patch_extraction(self) -> None:
        log = f"noise\n{PATCH_BEGIN} -----\n--- a/x\n+++ b/x\n{PATCH_END}\nmore"
        self.assertEqual(extract_patch(log), "--- a/x\n+++ b/x\n")
        self.assertIsNone(extract_patch("no patch here"))
        import base64

        patch = "--- a/x\n+++ b/x\n-  },\n+  {\n"
        encoded = base64.b64encode(patch.encode()).decode()
        chunks = "\n".join(encoded[i : i + 8] for i in range(0, len(encoded), 8))
        log = f"noise\n{PATCH_BEGIN} (base64) -----\n{chunks}\n{PATCH_END}\n"
        self.assertEqual(extract_patch(log), patch)


if __name__ == "__main__":
    unittest.main()


class ShipLiveVerificationTest(unittest.TestCase):
    def test_failed_acceptance_does_not_report_verified(self):
        runner = ship.Shipper.__new__(ship.Shipper)
        runner.args = unittest.mock.Mock(verify=["exit 7"])
        runner.say = lambda _: None
        runner.remaining = lambda: 10
        runner.summary = {"state": "deployed"}
        with self.assertRaisesRegex(
            ShipError, "verification 1 failed with exit code 7"
        ):
            runner.verify_live(ROOT)
        self.assertEqual(runner.summary["state"], "deployed")
        self.assertEqual(runner.summary["verification"], [{"check": 1, "exit_code": 7}])

    def test_successful_acceptance_is_recorded(self):
        runner = ship.Shipper.__new__(ship.Shipper)
        runner.args = unittest.mock.Mock(verify=["test -f README.md", "true"])
        runner.say = lambda _: None
        runner.remaining = lambda: 10
        runner.summary = {}
        runner.verify_live(ROOT)
        self.assertEqual(runner.summary["state"], "verified")
        self.assertEqual(len(runner.summary["verification"]), 2)


class ShipBranchCleanupTest(unittest.TestCase):
    def test_fetch_prunes_deleted_refs_even_with_main_only_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote, local = root / "remote.git", root / "local"
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            subprocess.run(
                ["git", "clone", "-q", str(remote), str(local)],
                check=True,
                capture_output=True,
            )

            def git(*args):
                return subprocess.check_output(
                    ["git", "-C", str(local), *args], text=True
                ).strip()

            git("config", "user.name", "Fixture")
            git("config", "user.email", "fixture@example.com")
            git("checkout", "-qb", "main")
            (local / "file").write_text("base")
            git("add", "file")
            git("commit", "-qm", "base")
            git("push", "-q", "origin", "main", "HEAD:refs/heads/temporary")
            git("fetch", "-q", "origin")
            git("push", "-q", "origin", ":refs/heads/temporary")
            git(
                "config",
                "remote.origin.fetch",
                "+refs/heads/main:refs/remotes/origin/main",
            )
            runner = ship.Shipper.__new__(ship.Shipper)
            runner.fetch(local)
            refs = git("for-each-ref", "--format=%(refname)", "refs/remotes/origin/")
            self.assertNotIn("temporary", refs)
            self.assertIn("refs/remotes/origin/main", refs)

    def test_promotion_deletes_only_successfully_merged_branch(self):
        for succeeds in (True, False):
            with (
                self.subTest(succeeds=succeeds),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)

                def git(*args, cwd=None):
                    return subprocess.check_output(
                        ["git", "-C", str(cwd or root), *args], text=True
                    ).strip()

                git("init", "-q")
                git("config", "user.name", "Fixture")
                git("config", "user.email", "fixture@example.com")
                (root / "file").write_text("before\n")
                git("add", "file")
                git("commit", "-qm", "base")
                git("update-ref", "refs/remotes/origin/main", "HEAD")
                (root / "file").write_text("after\n")
                patch_body = git("diff") + "\n"
                git("checkout", "--", "file")
                runner = ship.Shipper.__new__(ship.Shipper)
                runner.args = argparse.Namespace()
                runner.deadline = 9999999999
                runner.summary = {"promotions": []}
                runner.git = git
                runner.fetch = lambda *args: None
                runner.say = lambda _: None
                nested = unittest.mock.Mock()

                def promote(worktree):
                    self.assertEqual((worktree / "file").read_text(), "after\n")
                    if not succeeds:
                        raise ShipError("fixture failed")
                    return {"merged": "a" * 40, "state": "deployed"}

                nested.ship.side_effect = promote
                with patch.object(ship, "Shipper", return_value=nested):
                    if succeeds:
                        runner.promote(patch_body, "fixture")
                    else:
                        with self.assertRaisesRegex(ShipError, "fixture failed"):
                            runner.promote(patch_body, "fixture")
                branches = git("branch", "--list", "ship/promote-*").splitlines()
                self.assertEqual(len(branches), 0 if succeeds else 1)
                self.assertEqual(
                    len(git("worktree", "list", "--porcelain").split("worktree ")) - 1,
                    1,
                )
