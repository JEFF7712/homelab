from __future__ import annotations

import argparse
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scripts.agent import ship
from scripts.agent.ship import (
    LEASE_REF,
    LeaseLost,
    MergeLease,
    ShipError,
    lease_holder,
    lease_message,
)
from scripts.verifier.freshness import OUTDATED


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


class Waiting(Exception):
    """Stops a lease wait loop in tests."""


class Agent:
    def __init__(self) -> None:
        self.said: list[str] = []

    def remaining(self) -> float:
        return 60.0

    def say(self, message: str) -> None:
        self.said.append(message)


class MergeLeaseTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.remote = root / "remote.git"
        subprocess.run(
            ["git", "init", "-q", "--bare", str(self.remote)],
            check=True,
            capture_output=True,
        )
        self.clones = []
        for name in ("a", "b"):
            clone = root / name
            subprocess.run(
                ["git", "init", "-q", str(clone)], check=True, capture_output=True
            )
            self.clones.append(clone)

    def lease(self, index: int, holder: str) -> tuple[MergeLease, Agent]:
        agent = Agent()
        return (
            MergeLease(agent, holder, str(self.remote), self.clones[index]),  # type: ignore[arg-type]
            agent,
        )

    def remote_lease(self) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.remote), "rev-parse", "--verify", "-q", LEASE_REF],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip()

    def test_one_ship_holds_the_lease_and_others_wait(self) -> None:
        first, _ = self.lease(0, "feat/a (PR #1)")
        first.acquire()
        self.assertEqual(self.remote_lease(), first.sha)
        message = git(self.remote, "log", "-1", "--format=%B", LEASE_REF)
        self.assertIn("[skip ci]", message, "the lease never starts pipelines")
        self.assertEqual(git(self.remote, "show", "-s", "--format=%P", LEASE_REF), "")

        second, agent = self.lease(1, "feat/b (PR #2)")
        with (
            patch.object(ship.time, "sleep", side_effect=Waiting),
            self.assertRaises(Waiting),
        ):
            second.acquire()
        self.assertIn("waiting for the merge lease held by feat/a (PR #1)", agent.said)
        self.assertEqual(self.remote_lease(), first.sha)

        first.release()
        self.assertEqual(self.remote_lease(), "")
        second.acquire()
        self.assertEqual(self.remote_lease(), second.sha)

    def test_an_expired_lease_is_taken_over_and_its_holder_notices(self) -> None:
        stale, _ = self.lease(0, "crashed")
        with patch.object(ship, "LEASE_TTL_SECONDS", -1):
            stale.acquire()
        fresh, agent = self.lease(1, "next")
        fresh.acquire()
        self.assertIn("taking over an expired merge lease", agent.said)
        self.assertEqual(self.remote_lease(), fresh.sha)

        with self.assertRaises(LeaseLost):
            stale.ensure()
        stale.renewed = 0.0
        with self.assertRaises(LeaseLost):
            stale.renew()
        stale.release()
        self.assertEqual(self.remote_lease(), fresh.sha, "release is compare-and-swap")

    def test_renewal_moves_the_lease_to_a_new_expiry(self) -> None:
        lease, _ = self.lease(0, "holder")
        lease.acquire()
        before = lease.sha
        lease.renew()
        self.assertEqual(lease.sha, before, "renewal waits for its interval")
        lease.renewed = 0.0
        with patch.object(ship.time, "time", return_value=ship.time.time() + 120):
            lease.renew()
        self.assertNotEqual(lease.sha, before)
        self.assertEqual(self.remote_lease(), lease.sha)

    def test_lease_messages_round_trip_and_expire(self) -> None:
        message = lease_message("feat/x (PR #3)", 1000)
        self.assertEqual(lease_holder(message, 999), "feat/x (PR #3)")
        self.assertIsNone(lease_holder(message, 1000))
        self.assertIsNone(lease_holder("garbage", 0))


class MergeUnderLeaseTest(unittest.TestCase):
    """merge_locked revalidates only when main's changes overlap the head."""

    def shipper(
        self,
        outdated: list[list[str]],
        policy: list[bool] | None = None,
        protection: bool = False,
    ) -> tuple[ship.Shipper, list[str]]:
        shipper = ship.Shipper.__new__(ship.Shipper)
        shipper.remaining = lambda: 60.0
        shipper.args = argparse.Namespace(merge_style="merge")
        shipper.remaining = lambda: 60.0
        shipper.wait_status = lambda sha, label: None
        verdicts = list(policy or [])
        shipper.wait_policy = lambda pull: verdicts.pop(0) if verdicts else True
        shipper.outdated = lambda head: outdated.pop(0) if outdated else []
        calls: list[str] = []
        heads = iter("abcdef")
        state = {"head": next(heads) * 40}

        def call(method: str, path: str, body: Any = None) -> Any:
            calls.append(f"{method} {path}")
            if path.endswith("/update?style=merge"):
                state["head"] = next(heads) * 40
                return None
            if path.endswith("/reviews"):
                return []
            return {
                "number": 4,
                "head": {"sha": state["head"]},
                "merged": False,
                "merge_commit_sha": "m" * 40,
            }

        def try_call(method: str, path: str, body: Any = None) -> tuple[int, Any]:
            calls.append(f"{method} {path}")
            return 200, None

        shipper.forgejo = unittest.mock.Mock()
        shipper.forgejo.call = call
        shipper.forgejo.try_call = try_call
        self.patches = [
            patch.object(ship, "policy_required", return_value=protection),
            patch.object(ship.time, "sleep"),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        return shipper, calls

    def test_independent_main_changes_merge_without_revalidation(self) -> None:
        shipper, calls = self.shipper(outdated=[[]])
        shipper.merge_locked(4)
        self.assertNotIn("POST /pulls/4/update?style=merge", calls)
        self.assertIn("POST /pulls/4/merge", calls)

    def test_overlapping_main_changes_update_then_merge(self) -> None:
        shipper, calls = self.shipper(outdated=[["both change gitops/media"], []])
        shipper.merge_locked(4)
        self.assertEqual(calls.count("POST /pulls/4/update?style=merge"), 1)
        self.assertLess(
            calls.index("POST /pulls/4/update?style=merge"),
            calls.index("POST /pulls/4/merge"),
        )

    def test_an_outdated_policy_verdict_updates_the_branch(self) -> None:
        shipper, calls = self.shipper(
            outdated=[], policy=[False, True], protection=True
        )
        shipper.merge_locked(4)
        self.assertEqual(calls.count("POST /pulls/4/update?style=merge"), 1)
        self.assertLess(
            calls.index("POST /pulls/4/update?style=merge"),
            calls.index("POST /pulls/4/merge"),
        )

    def test_outdated_policy_status_is_reported_not_raised(self) -> None:
        class Forge:
            def call(self, method: str, path: str, body: Any = None) -> Any:
                return {
                    "statuses": [
                        {
                            "context": ship.POLICY_CONTEXT,
                            "status": "failure",
                            "description": f"{OUTDATED}: both change gitops/media",
                        }
                    ]
                }

        shipper = ship.Shipper.__new__(ship.Shipper)
        shipper.forgejo = Forge()  # type: ignore[assignment]
        shipper.say = lambda message: None
        shipper.remaining = lambda: 60.0
        self.assertFalse(shipper.wait_policy({"number": 4, "head": {"sha": "a" * 40}}))

    def test_lost_lease_queues_again_and_always_releases(self) -> None:
        shipper = ship.Shipper.__new__(ship.Shipper)
        shipper.say = lambda message: None
        shipper.forgejo = unittest.mock.Mock()
        shipper.forgejo.call.return_value = {
            "number": 4,
            "merged": False,
            "head": {"sha": "a" * 40, "ref": "feat/x"},
        }
        shipper.wait_status = lambda sha, label: None
        attempts = [LeaseLost("lost"), "m" * 40]

        def merge_locked(number: int) -> str:
            self.assertIsNotNone(shipper.lease)
            outcome = attempts.pop(0)
            if isinstance(outcome, ShipError):
                raise outcome
            return outcome

        shipper.merge_locked = merge_locked
        with (
            patch.object(MergeLease, "acquire") as acquire,
            patch.object(MergeLease, "release") as release,
        ):
            self.assertEqual(shipper.merge({"number": 4}), "m" * 40)
        self.assertEqual((acquire.call_count, release.call_count), (2, 2))
        self.assertIsNone(shipper.lease)


class OutdatedTest(unittest.TestCase):
    def test_compares_branch_and_main_changes_since_the_merge_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            git(repo, "init", "-q", "-b", "main")
            git(repo, "config", "user.email", "t@example.com")
            git(repo, "config", "user.name", "t")

            def commit(path: str) -> str:
                target = repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(path + "\n")
                git(repo, "add", "-A")
                git(repo, "commit", "-q", "-m", path)
                return git(repo, "rev-parse", "HEAD")

            commit("README.md")
            git(repo, "checkout", "-q", "-b", "feat")
            head = commit("gitops/media/app.yaml")
            git(repo, "checkout", "-q", "main")
            commit("gitops/observability/app.yaml")
            git(repo, "update-ref", "refs/remotes/origin/main", "main")

            shipper = ship.Shipper.__new__(ship.Shipper)
            shipper.fetch = lambda cwd=None: None
            with patch.object(ship, "ROOT", repo):
                self.assertEqual(shipper.outdated(head), [])
                commit("gitops/media/other.yaml")
                git(repo, "update-ref", "refs/remotes/origin/main", "main")
                self.assertEqual(shipper.outdated(head), ["both change gitops/media"])


if __name__ == "__main__":
    unittest.main()
