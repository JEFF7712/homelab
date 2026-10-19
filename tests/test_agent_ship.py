from __future__ import annotations

import json
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

    def test_infrastructure_routes_are_held_unless_named(self) -> None:
        changed = ["flake/hosts/homelab-04/default.nix", "tofu/opnsense/rules.tf"]
        plan = plan_operations(changed, self.routes, "auto", self.catalog)
        self.assertEqual(plan.run, [])
        self.assertEqual(set(plan.held), {"fleet", "opnsense"})
        explicit = plan_operations(changed, self.routes, "opnsense-apply", self.catalog)
        self.assertEqual(explicit.run, ["opnsense-plan", "opnsense-apply"])
        self.assertEqual(
            plan_operations(changed, self.routes, "none", self.catalog).run, []
        )

    def test_unknown_operation_is_rejected(self) -> None:
        with self.assertRaises(ShipError):
            plan_operations([], self.routes, "rm-rf", self.catalog)

    def test_docs_only_change_deploys_nothing(self) -> None:
        plan = plan_operations(["docs/a.md"], self.routes, "auto", self.catalog)
        self.assertEqual((plan.run, plan.held), ([], {}))


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
        log = f"noise\n{PATCH_BEGIN}\n--- a/x\n+++ b/x\n{PATCH_END}\nmore"
        self.assertEqual(extract_patch(log), "--- a/x\n+++ b/x\n")
        self.assertIsNone(extract_patch("no patch here"))


if __name__ == "__main__":
    unittest.main()
