from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from scripts.ci import nixos_deploy
from scripts.ci.nixos_deploy import (
    DEPLOY_BRANCH,
    REQUIRED_CONTEXT,
    UPDATE_BRANCH,
    advance_action,
    validation_state,
)
from scripts.ci.policy import SCHEDULES

ROOT = Path(__file__).resolve().parents[1]
MAIN = "a" * 40
DEPLOYED = "b" * 40
CANDIDATE = "c" * 40


def status(state: str, updated: str = "2026-10-21T00:00:00Z") -> dict[str, str]:
    return {"context": REQUIRED_CONTEXT, "status": state, "updated_at": updated}


class FakeForgejo:
    def __init__(
        self,
        branches: dict[str, str],
        statuses: dict[str, str],
        pulls: list[dict[str, Any]] | None = None,
    ) -> None:
        self.branches = branches
        self.statuses = statuses
        self.pulls = pulls or []
        self.calls: list[tuple[str, str, Any]] = []

    def branch(self, name: str) -> str | None:
        return self.branches.get(name)

    def validation(self, sha: str) -> str:
        return self.statuses.get(sha, "pending")

    def update_pull(self) -> dict[str, Any] | None:
        return next(iter(self.pulls), None)

    def call(self, method: str, path: str, body: Any = None) -> Any:
        self.calls.append((method, path, body))
        return {"html_url": "https://git.rupan.dev/pr/1", "number": 1}


class ValidationStateTests(unittest.TestCase):
    def test_latest_required_status_wins(self) -> None:
        statuses = [
            status("failure", "2026-10-20T00:00:00Z"),
            status("success", "2026-10-21T00:00:00Z"),
            {"context": "other", "status": "failure", "updated_at": "2026-10-22"},
        ]
        self.assertEqual(validation_state(statuses), "success")

    def test_missing_or_running_status_is_pending(self) -> None:
        self.assertEqual(validation_state(None), "pending")
        self.assertEqual(validation_state([status("pending")]), "pending")

    def test_error_counts_as_failure(self) -> None:
        self.assertEqual(validation_state([status("error")]), "failure")


class AdvanceActionTests(unittest.TestCase):
    def test_current_deployment_needs_nothing(self) -> None:
        self.assertEqual(advance_action(MAIN, MAIN, "pending"), "current")

    def test_only_validated_main_is_published(self) -> None:
        self.assertEqual(advance_action(MAIN, DEPLOYED, "success"), "publish")
        self.assertEqual(advance_action(MAIN, None, "success"), "publish")
        self.assertEqual(advance_action(MAIN, DEPLOYED, "pending"), "deferred")
        self.assertEqual(advance_action(MAIN, DEPLOYED, "failure"), "blocked")


class AdvanceTests(unittest.TestCase):
    def run_advance(
        self, forgejo: FakeForgejo, ancestor: bool = True
    ) -> tuple[MagicMock, MagicMock]:
        checkout = MagicMock()
        checkout.is_ancestor.return_value = ancestor
        with (
            patch.object(nixos_deploy, "Checkout", return_value=checkout),
            patch.object(nixos_deploy, "publish") as publish,
            patch.object(nixos_deploy, "POLL_SECONDS", 0),
            patch.object(nixos_deploy, "VALIDATION_POLLS", 1),
        ):
            nixos_deploy.advance(forgejo, "token")  # type: ignore[arg-type]
        return checkout, publish

    def test_publishes_before_fast_forwarding_deploy_branch(self) -> None:
        forgejo = FakeForgejo(
            {"main": MAIN, DEPLOY_BRANCH: DEPLOYED}, {MAIN: "success"}
        )
        order: list[str] = []
        checkout = MagicMock()
        checkout.is_ancestor.return_value = True
        checkout.git.side_effect = lambda *args, **_: order.append(args[0])
        with (
            patch.object(nixos_deploy, "Checkout", return_value=checkout),
            patch.object(
                nixos_deploy, "publish", side_effect=lambda _: order.append("publish")
            ),
        ):
            nixos_deploy.advance(forgejo, "token")  # type: ignore[arg-type]
        self.assertEqual(order, ["checkout", "publish", "push"])
        checkout.git.assert_called_with(
            "push", "origin", f"{MAIN}:refs/heads/{DEPLOY_BRANCH}"
        )

    def test_push_is_never_forced(self) -> None:
        forgejo = FakeForgejo({"main": MAIN}, {MAIN: "success"})
        checkout, _ = self.run_advance(forgejo)
        for call in checkout.git.call_args_list:
            self.assertNotIn("--force", call.args)

    def test_unvalidated_main_is_not_published(self) -> None:
        forgejo = FakeForgejo({"main": MAIN, DEPLOY_BRANCH: DEPLOYED}, {})
        checkout, publish = self.run_advance(forgejo)
        publish.assert_not_called()
        checkout.git.assert_not_called()

    def test_failed_validation_blocks_deployment(self) -> None:
        forgejo = FakeForgejo(
            {"main": MAIN, DEPLOY_BRANCH: DEPLOYED}, {MAIN: "failure"}
        )
        with self.assertRaisesRegex(ValueError, "not deploying"):
            self.run_advance(forgejo)

    def test_diverged_deploy_branch_fails_before_publishing(self) -> None:
        forgejo = FakeForgejo(
            {"main": MAIN, DEPLOY_BRANCH: DEPLOYED}, {MAIN: "success"}
        )
        with (
            patch.object(nixos_deploy, "Checkout") as factory,
            patch.object(nixos_deploy, "publish") as publish,
        ):
            factory.return_value.is_ancestor.return_value = False
            with self.assertRaisesRegex(ValueError, "not an ancestor"):
                nixos_deploy.advance(forgejo, "token")  # type: ignore[arg-type]
        publish.assert_not_called()


class FinishTests(unittest.TestCase):
    def pull(self) -> dict[str, Any]:
        return {"number": 7, "head": {"sha": CANDIDATE, "ref": UPDATE_BRANCH}}

    def test_publishes_candidate_then_merges(self) -> None:
        forgejo = FakeForgejo({}, {CANDIDATE: "success"}, [self.pull()])
        with (
            patch.object(nixos_deploy, "Checkout") as factory,
            patch.object(nixos_deploy, "publish") as publish,
        ):
            nixos_deploy.finish(forgejo, "token")  # type: ignore[arg-type]
        factory.return_value.git.assert_any_call("checkout", "--detach", CANDIDATE)
        publish.assert_called_once()
        self.assertEqual(forgejo.calls, [("POST", "/pulls/7/merge", {"Do": "merge"})])

    def test_failed_candidate_is_closed_without_publishing(self) -> None:
        forgejo = FakeForgejo({}, {CANDIDATE: "failure"}, [self.pull()])
        with (
            patch.object(nixos_deploy, "publish") as publish,
            self.assertRaisesRegex(ValueError, "closed update PR #7"),
        ):
            nixos_deploy.finish(forgejo, "token")  # type: ignore[arg-type]
        publish.assert_not_called()
        self.assertEqual(forgejo.calls, [("PATCH", "/pulls/7", {"state": "closed"})])

    def test_pending_candidate_is_kept_for_retry(self) -> None:
        forgejo = FakeForgejo({}, {}, [self.pull()])
        with (
            patch.object(nixos_deploy, "POLL_SECONDS", 0),
            patch.object(nixos_deploy, "VALIDATION_POLLS", 1),
            patch.object(nixos_deploy, "publish") as publish,
            self.assertRaisesRegex(ValueError, "next run retries"),
        ):
            nixos_deploy.finish(forgejo, "token")  # type: ignore[arg-type]
        publish.assert_not_called()
        self.assertEqual(forgejo.calls, [])

    def test_open_pull_is_not_replaced_by_weekly_proposal(self) -> None:
        forgejo = FakeForgejo({}, {}, [self.pull()])
        with patch.object(nixos_deploy, "Checkout") as factory:
            nixos_deploy.propose(forgejo, "token")  # type: ignore[arg-type]
        factory.assert_not_called()
        self.assertEqual(forgejo.calls, [])


class CatalogTests(unittest.TestCase):
    def test_schedules_and_credentials(self) -> None:
        catalog = json.loads((ROOT / "config/ci/operations.json").read_text())
        self.assertEqual(SCHEDULES["weekly"], ("nixos-lock-update",))
        self.assertIn("nixos-deploy", SCHEDULES["nightly"])
        for name, schedule in [
            ("nixos-lock-update", "weekly"),
            ("nixos-deploy", "nightly"),
        ]:
            operation = catalog[name]
            self.assertTrue(operation["mutation"])
            self.assertEqual(operation["group"], "nix-cache")
            self.assertEqual(
                operation["commands"], [f"python -m scripts.ci.nixos_deploy {schedule}"]
            )
            self.assertEqual(
                operation["environment"]["FORGEJO_PUBLISH_TOKEN"],
                {"from_secret": "forgejo_publish_token"},
            )
            self.assertEqual(
                operation["environment"]["ATTIC_TOKEN"], {"from_secret": "attic_token"}
            )


if __name__ == "__main__":
    unittest.main()
