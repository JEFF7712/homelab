from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts.verifier.service import (
    CONTEXT,
    Forge,
    Mirror,
    decide,
    operator_approved,
    run_once,
)

ROOT = Path(__file__).resolve().parent.parent


class FakeForge(Forge):
    """Records writes and answers reads for one repository."""

    def __init__(self, pulls: list[dict[str, Any]]) -> None:
        self.pulls = {pull["number"]: pull for pull in pulls}
        self.reviews: dict[int, list[dict[str, Any]]] = {}
        self.comments: dict[int, list[dict[str, Any]]] = {}
        self.statuses: list[tuple[str, dict[str, Any]]] = []
        self.moved: dict[int, str] = {}

    def call(self, method: str, path: str, body: Any = None) -> Any:
        if method == "GET" and path.startswith("/pulls?"):
            return list(self.pulls.values())
        if method == "GET" and path.endswith("/reviews"):
            return self.reviews.get(int(path.split("/")[2]), [])
        if method == "GET" and path.startswith("/pulls/"):
            number = int(path.split("/")[2])
            pull = dict(self.pulls[number])
            if number in self.moved:
                pull["head"] = {"sha": self.moved[number]}
            return pull
        if method == "POST" and path.startswith("/statuses/"):
            self.statuses.append((path.split("/")[2], body))
            return {}
        if path.startswith("/issues/") and path.endswith("/comments"):
            number = int(path.split("/")[2])
            if method == "GET":
                return self.comments.get(number, [])
            self.comments.setdefault(number, []).append(body)
            return {}
        raise AssertionError(f"unexpected {method} {path}")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


class MergePolicyService(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.remote = self.tmp / "remote"
        self.remote.mkdir()
        git(self.remote, "init", "-q", "-b", "main")
        git(self.remote, "config", "user.email", "t@example.com")
        git(self.remote, "config", "user.name", "t")
        # The source may be a read-only store path under the Nix build sandbox.
        shutil.copytree(
            ROOT / "gitops/websites",
            self.remote / "gitops/websites",
            copy_function=shutil.copyfile,
        )
        for path in (self.remote / "gitops").rglob("*"):
            path.chmod(0o755 if path.is_dir() else 0o644)
        (self.remote / "gitops").chmod(0o755)
        git(self.remote, "add", "-A")
        git(self.remote, "commit", "-q", "-m", "base")
        self.base = git(self.remote, "rev-parse", "HEAD")
        self.mirror = Mirror(self.tmp / "mirror.git", str(self.remote), "unused")

    def branch(self, number: int, edit) -> str:
        git(self.remote, "checkout", "-q", "-b", f"pr{number}", self.base)
        edit(self.remote)
        git(self.remote, "add", "-A")
        git(self.remote, "commit", "-q", "-m", f"pr {number}")
        head = git(self.remote, "rev-parse", "HEAD")
        git(self.remote, "checkout", "-q", "main")
        return head

    def pull(self, number: int, head: str) -> dict[str, Any]:
        return {
            "number": number,
            "head": {"sha": head},
            "base": {"ref": "main"},
            "html_url": f"u/{number}",
        }

    def test_routine_change_passes_and_widened_ingress_fails_with_a_comment(
        self,
    ) -> None:
        def routine(repo: Path) -> None:
            spec = repo / "gitops/websites/darkbit/site.yaml"
            spec.write_text(spec.read_text() + "replicas: 2\n")
            deployment = repo / "gitops/websites/darkbit/deployment.yaml"
            deployment.write_text(
                deployment.read_text().replace("replicas: 1", "replicas: 2")
            )

        def widen(repo: Path) -> None:
            spec = repo / "gitops/websites/ism/site.yaml"
            spec.write_text(spec.read_text() + "\n")
            policy = repo / "gitops/websites/ism/network-policy.yaml"
            policy.write_text(
                policy.read_text()
                + "    - from:\n        - ipBlock:\n            cidr: 0.0.0.0/0\n"
            )

        good, bad = self.branch(1, routine), self.branch(2, widen)
        forge = FakeForge([self.pull(1, good), self.pull(2, bad)])
        judged: dict[int, tuple[str, str]] = {}
        run_once(forge, self.mirror, judged)
        states = {sha: body["state"] for sha, body in forge.statuses}
        self.assertEqual(states, {good: "success", bad: "failure"})
        self.assertTrue(all(body["context"] == CONTEXT for _, body in forge.statuses))
        self.assertIn("ingress may only come from", forge.comments[2][0]["body"])
        self.assertIn(f"head={bad}", forge.comments[2][0]["body"])

        run_once(forge, self.mirror, judged)
        self.assertEqual(
            len(forge.statuses), 2, "unchanged decisions are not re-posted"
        )
        self.assertEqual(len(forge.comments[2]), 1, "one comment per head")

    def test_operator_approval_of_the_exact_head_overrides(self) -> None:
        def eject(repo: Path) -> None:
            (repo / "gitops/websites/darkbit/site.yaml").unlink()
            root = repo / "gitops/websites/kustomization.yaml"
            root.write_text(root.read_text().replace("  - darkbit\n", ""))

        head = self.branch(3, eject)
        forge = FakeForge([self.pull(3, head)])
        judged: dict[int, tuple[str, str]] = {}
        run_once(forge, self.mirror, judged)
        self.assertEqual(forge.statuses[-1][1]["state"], "failure")
        self.assertIn("outcome=escalated", forge.comments[3][0]["body"])

        forge.reviews[3] = [
            {"state": "APPROVED", "user": {"login": "rupan"}, "commit_id": "0" * 40}
        ]
        run_once(forge, self.mirror, judged)
        self.assertEqual(
            forge.statuses[-1][1]["state"], "failure", "approval of another commit"
        )

        forge.reviews[3].append(
            {"state": "APPROVED", "user": {"login": "rupan"}, "commit_id": head}
        )
        run_once(forge, self.mirror, judged)
        self.assertEqual(forge.statuses[-1][1]["state"], "success")

    def test_no_status_is_posted_when_the_head_moves_during_judgement(self) -> None:
        head = self.branch(4, lambda repo: (repo / "README").write_text("x\n"))
        forge = FakeForge([self.pull(4, head)])
        forge.moved[4] = "f" * 40
        run_once(forge, self.mirror, {})
        self.assertEqual(forge.statuses, [])

    def test_repeated_evaluation_errors_fail_closed_with_a_reason(self) -> None:
        forge = FakeForge([self.pull(5, "e" * 40)])
        judged: dict[int, tuple[str, str]] = {}
        errors: dict[tuple[int, str], int] = {}
        for _ in range(2):
            run_once(forge, self.mirror, judged, errors=errors)
        self.assertEqual(forge.statuses, [], "transient errors post nothing")
        run_once(forge, self.mirror, judged, errors=errors)
        self.assertEqual(forge.statuses[-1][1]["state"], "failure")
        self.assertIn("evaluation", forge.comments[5][0]["body"])


class Decide(unittest.TestCase):
    def test_policy_paths_need_operator_authority(self) -> None:
        decision = decide(
            ["scripts/verifier/websites.py"], None, None, set(), set(), False
        )
        self.assertEqual((decision.state, decision.escalate), ("failure", True))
        self.assertEqual(
            decide(["CODEOWNERS"], None, None, set(), set(), True).state, "success"
        )

    def test_changes_outside_websites_and_policy_pass(self) -> None:
        decision = decide(
            ["scripts/websites/render.py", "docs/x.md"], None, None, set(), set(), False
        )
        self.assertEqual(decision.state, "success")

    def test_only_unstale_operator_approvals_count(self) -> None:
        head = "a" * 40
        review = {"state": "APPROVED", "user": {"login": "rupan"}, "commit_id": head}
        self.assertTrue(operator_approved([review], head))
        self.assertFalse(operator_approved([{**review, "stale": True}], head))
        self.assertFalse(operator_approved([{**review, "dismissed": True}], head))
        self.assertFalse(
            operator_approved([{**review, "user": {"login": "homelab-agent"}}], head)
        )
        self.assertFalse(operator_approved([{**review, "state": "COMMENT"}], head))


if __name__ == "__main__":
    unittest.main()
