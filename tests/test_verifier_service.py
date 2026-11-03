from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts.verifier.service import (
    CONTEXT,
    OUTDATED,
    Approvals,
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
        self.comments: dict[int, list[dict[str, Any]]] = {}
        self.statuses: list[tuple[str, dict[str, Any]]] = []
        self.moved: dict[int, str] = {}

    def call(self, method: str, path: str, body: Any = None) -> Any:
        if method == "GET" and path.startswith("/pulls?"):
            return list(self.pulls.values())
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


class FakeApprovals(Approvals):
    """The approvals dashboard: approved heads per PR, and filed requests."""

    def __init__(self) -> None:
        self.heads: dict[int, list[str]] = {}
        self.filed: list[tuple[int, str, dict[str, str], list[str]]] = []
        self.down = False

    def approved(self, number: int) -> list[str]:
        if self.down:
            raise ConnectionRefusedError("approvals unreachable")
        return self.heads.get(number, [])

    def file(
        self, number: int, head: str, files: dict[str, str], findings: list[str]
    ) -> str:
        self.filed.append((number, head, files, findings))
        return f"https://approvals.test/r/{number}-{head[:6]}"


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
        self.approvals = FakeApprovals()

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

    def test_routine_change_passes_and_an_unhardened_change_fails_with_a_comment(
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
            deployment = repo / "gitops/websites/ism/deployment.yaml"
            deployment.write_text(
                deployment.read_text().replace(
                    "readOnlyRootFilesystem: true", "readOnlyRootFilesystem: false"
                )
            )

        good, bad = self.branch(1, routine), self.branch(2, widen)
        forge = FakeForge([self.pull(1, good), self.pull(2, bad)])
        judged: dict[int, tuple[str, str, str, str]] = {}
        run_once(forge, self.mirror, self.approvals, judged)
        states = {sha: body["state"] for sha, body in forge.statuses}
        self.assertEqual(states, {good: "success", bad: "failure"})
        self.assertTrue(all(body["context"] == CONTEXT for _, body in forge.statuses))
        self.assertIn("readOnlyRootFilesystem", forge.comments[2][0]["body"])
        self.assertIn("outcome=rejected", forge.comments[2][0]["body"])
        self.assertIn(f"head={bad}", forge.comments[2][0]["body"])
        self.assertEqual(self.approvals.filed, [], "repairs need no operator")

        run_once(forge, self.mirror, self.approvals, judged)
        self.assertEqual(
            len(forge.statuses), 2, "unchanged decisions are not re-posted"
        )
        self.assertEqual(len(forge.comments[2]), 1, "one comment per head")

    def advance_main(self, path: str) -> None:
        target = self.remote / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("main\n")
        git(self.remote, "add", "-A")
        git(self.remote, "commit", "-q", "-m", f"main changes {path}")

    def test_a_head_behind_overlapping_main_changes_fails_without_a_comment(
        self,
    ) -> None:
        def media(repo: Path) -> None:
            (repo / "gitops/media").mkdir(parents=True)
            (repo / "gitops/media/app.yaml").write_text("branch\n")

        head = self.branch(8, media)
        forge = FakeForge([self.pull(8, head)])
        judged: dict[int, tuple[str, str, str, str]] = {}
        run_once(forge, self.mirror, self.approvals, judged)
        self.assertEqual(forge.statuses[-1][1]["state"], "success")

        self.advance_main("gitops/observability/app.yaml")
        run_once(forge, self.mirror, self.approvals, judged)
        self.assertEqual(len(forge.statuses), 1, "independent main changes keep it")

        self.advance_main("gitops/media/other.yaml")
        run_once(forge, self.mirror, self.approvals, judged)
        state = forge.statuses[-1][1]
        self.assertEqual(state["state"], "failure")
        self.assertTrue(state["description"].startswith(OUTDATED))
        self.assertIn("gitops/media", state["description"])
        self.assertNotIn(8, forge.comments, "a branch update fixes it, not a repair")

    def test_operator_approval_of_the_exact_head_overrides(self) -> None:
        def eject(repo: Path) -> None:
            # Removing a site that holds stored data needs an operator.
            root = repo / "gitops/websites/kustomization.yaml"
            root.write_text(root.read_text().replace("  - uwagent\n", ""))

        head = self.branch(3, eject)
        forge = FakeForge([self.pull(3, head)])
        judged: dict[int, tuple[str, str, str, str]] = {}
        run_once(forge, self.mirror, self.approvals, judged)
        self.assertEqual(forge.statuses[-1][1]["state"], "failure")
        self.assertIn("outcome=escalated", forge.comments[3][0]["body"])

        self.approvals.heads[3] = ["0" * 40]
        run_once(forge, self.mirror, self.approvals, judged)
        self.assertEqual(
            forge.statuses[-1][1]["state"], "failure", "approval of another commit"
        )

        self.approvals.heads[3].append(head)
        run_once(forge, self.mirror, self.approvals, judged)
        self.assertEqual(forge.statuses[-1][1]["state"], "success")

    def test_an_escalation_files_one_dashboard_request_with_its_files(self) -> None:
        def touch_policy(repo: Path) -> None:
            (repo / "scripts/ci").mkdir(parents=True)
            (repo / "scripts/ci/policy.py").write_text("policy\n")
            (repo / "README").write_text("unprotected\n")

        head = self.branch(10, touch_policy)
        forge = FakeForge([self.pull(10, head)])
        judged: dict[int, tuple[str, str, str, str]] = {}
        run_once(forge, self.mirror, self.approvals, judged)
        run_once(forge, self.mirror, self.approvals, judged)
        [(number, filed_head, files, findings)] = self.approvals.filed
        self.assertEqual((number, filed_head), (10, head))
        blob = git(self.remote, "rev-parse", f"{head}:scripts/ci/policy.py")
        self.assertEqual(files, {"scripts/ci/policy.py": f"100644 {blob}"})
        self.assertTrue(any("scripts/ci/policy.py" in f for f in findings))
        link = f"https://approvals.test/r/10-{head[:6]}"
        self.assertEqual(forge.statuses[-1][1]["target_url"], link)
        self.assertIn(link, forge.comments[10][0]["body"])
        self.assertNotIn("@rupan", forge.comments[10][0]["body"])

    def test_an_approvals_outage_never_flips_a_status(self) -> None:
        def touch_policy(repo: Path) -> None:
            (repo / ".sops.yaml").write_text("x\n")

        head = self.branch(11, touch_policy)
        self.approvals.heads[11] = [head]
        forge = FakeForge([self.pull(11, head)])
        judged: dict[int, tuple[str, str, str, str]] = {}
        errors: dict[tuple[int, str], int] = {}
        run_once(forge, self.mirror, self.approvals, judged, errors=errors)
        self.assertEqual(forge.statuses[-1][1]["state"], "success")

        self.advance_main("README")
        self.approvals.down = True
        run_once(forge, self.mirror, self.approvals, judged, errors=errors)
        self.assertEqual(len(forge.statuses), 1, "a transient outage posts nothing")

    def test_approved_head_behind_overlapping_main_reports_the_new_reason(
        self,
    ) -> None:
        def touch_policy(repo: Path) -> None:
            (repo / ".sops.yaml").write_text("x\n")

        head = self.branch(9, touch_policy)
        forge = FakeForge([self.pull(9, head)])
        judged: dict[int, tuple[str, str, str, str]] = {}
        run_once(forge, self.mirror, self.approvals, judged)
        self.assertEqual(
            forge.statuses[-1][1]["description"],
            "needs operator approval of this commit",
        )

        self.advance_main(".sops.yaml")
        self.approvals.heads[9] = [head]
        run_once(forge, self.mirror, self.approvals, judged)
        state = forge.statuses[-1][1]
        self.assertEqual(state["state"], "failure")
        self.assertTrue(state["description"].startswith(OUTDATED), state)

        run_once(forge, self.mirror, self.approvals, judged)
        self.assertEqual(len(forge.statuses), 2, "an unchanged reason is not re-posted")

    def test_approval_carries_over_a_main_update_but_not_a_new_change(self) -> None:
        def touch_policy(repo: Path) -> None:
            (repo / ".sops.yaml").write_text("x\n")

        approved = self.branch(7, touch_policy)
        # main advances, then the PR branch is updated from it.
        (self.remote / "README").write_text("main moved\n")
        git(self.remote, "add", "-A")
        git(self.remote, "commit", "-q", "-m", "main moves")
        git(self.remote, "checkout", "-q", "pr7")
        git(self.remote, "merge", "-q", "--no-edit", "main")
        updated = git(self.remote, "rev-parse", "HEAD")
        git(self.remote, "checkout", "-q", "main")
        self.approvals.heads[7] = [approved]

        forge = FakeForge([self.pull(7, updated)])
        run_once(forge, self.mirror, self.approvals, {})
        self.assertEqual(forge.statuses[-1][1]["state"], "success")

        git(self.remote, "checkout", "-q", "pr7")
        (self.remote / ".sops.yaml").write_text("y\n")
        git(self.remote, "commit", "-qam", "new change")
        changed = git(self.remote, "rev-parse", "HEAD")
        git(self.remote, "checkout", "-q", "main")
        forge = FakeForge([self.pull(7, changed)])
        run_once(forge, self.mirror, self.approvals, {})
        self.assertEqual(forge.statuses[-1][1]["state"], "failure")

    def test_no_status_is_posted_when_the_head_moves_during_judgement(self) -> None:
        head = self.branch(4, lambda repo: (repo / "README").write_text("x\n"))
        forge = FakeForge([self.pull(4, head)])
        forge.moved[4] = "f" * 40
        run_once(forge, self.mirror, self.approvals, {})
        self.assertEqual(forge.statuses, [])

    def test_repeated_evaluation_errors_fail_closed_with_a_reason(self) -> None:
        forge = FakeForge([self.pull(5, "e" * 40)])
        judged: dict[int, tuple[str, str, str, str]] = {}
        errors: dict[tuple[int, str], int] = {}
        for _ in range(2):
            run_once(forge, self.mirror, self.approvals, judged, errors=errors)
        self.assertEqual(forge.statuses, [], "transient errors post nothing")
        run_once(forge, self.mirror, self.approvals, judged, errors=errors)
        self.assertEqual(forge.statuses[-1][1]["state"], "failure")
        self.assertIn("evaluation", forge.comments[5][0]["body"])

    def test_forge_timeouts_are_retried_not_fatal(self) -> None:
        class SlowForge(FakeForge):
            def call(self, method: str, path: str, body: Any = None) -> Any:
                if path.startswith("/pulls?"):
                    raise TimeoutError("timed out")
                return super().call(method, path, body)

        head = self.branch(6, lambda repo: (repo / "README").write_text("x\n"))
        run_once(SlowForge([self.pull(6, head)]), self.mirror, self.approvals, {})

        class SlowPull(FakeForge):
            def call(self, method: str, path: str, body: Any = None) -> Any:
                if path == "/pulls/6":
                    raise TimeoutError("timed out")
                return super().call(method, path, body)

        forge = SlowPull([self.pull(6, head)])
        run_once(forge, self.mirror, self.approvals, {})
        self.assertEqual(forge.statuses, [])


class Decide(unittest.TestCase):
    def test_policy_paths_need_operator_authority(self) -> None:
        decision = decide(
            ["scripts/verifier/websites.py"], None, None, set(), set(), False
        )
        self.assertEqual((decision.state, decision.escalate), ("failure", True))
        self.assertEqual(
            decide([".sops.yaml"], None, None, set(), set(), True).state, "success"
        )

    def test_changes_outside_websites_and_policy_pass(self) -> None:
        decision = decide(
            ["scripts/websites/render.py", "docs/x.md"], None, None, set(), set(), False
        )
        self.assertEqual(decision.state, "success")

    def test_approvals_cover_the_head_or_an_identical_change(self) -> None:
        head, other = "a" * 40, "c" * 40
        self.assertTrue(operator_approved([head], head))
        self.assertFalse(operator_approved([], head))
        self.assertFalse(operator_approved([other], head))
        self.assertFalse(operator_approved([other], head, lambda a, b: False))
        self.assertTrue(operator_approved([other], head, lambda a, b: True))


class AuthorityBoundaryTest(unittest.TestCase):
    """The operator approves spending and enforcement code; agents decide the rest."""

    def test_enforcement_code_needs_an_operator(self) -> None:
        from scripts.verifier.enforcement import enforcement

        for path in (
            "scripts/verifier/service.py",
            "scripts/verifier/enforcement.py",
            "scripts/ci/policy.py",
            "scripts/ci/deployment_risk.py",
            "scripts/ci/operation.py",
            "scripts/ci/state.py",
            "scripts/approvals/server.py",
            "flake/modules/woodpecker.nix",
            "flake/modules/merge-policy.nix",
            "flake/modules/tofu-state.nix",
            "config/ci/forgejo-protection.json",
            ".sops.yaml",
            "config/spend.json",
        ):
            with self.subTest(path=path):
                self.assertTrue(enforcement(path))
                decision = decide([path], None, None, set(), set(), False)
                self.assertEqual((decision.state, decision.escalate), ("failure", True))

    def test_agent_controls_outside_enforcement_are_the_agents(self) -> None:
        for path in (
            "scripts/agent/guard.py",
            "hooks/pre-tool-use",
            "CODEOWNERS",
            "AGENTS.md",
            "scripts/projectctl/authorization.py",
            "opnsense_reconciler/agent_acl.py",
        ):
            with self.subTest(path=path):
                decision = decide([path], None, None, set(), set(), False)
                self.assertEqual(decision.state, "success")

    def test_operator_approval_still_overrides(self) -> None:
        decision = decide(["scripts/ci/policy.py"], None, None, set(), set(), True)
        self.assertEqual(decision.state, "success")

    def test_ordinary_infrastructure_changes_need_no_one(self) -> None:
        decision = decide(
            [
                "flake/modules/kiosk.nix",
                "gitops/media/arr.yaml",
                "scripts/deploy_fleet.py",
            ],
            None,
            None,
            set(),
            set(),
            False,
        )
        self.assertEqual(decision.state, "success")

    def catalog(self, base: dict, head: dict):
        files = {
            "base": {"config/ci/operations.json": json.dumps(base)},
            "head": {"config/ci/operations.json": json.dumps(head)},
        }
        return decide(
            ["config/ci/operations.json"],
            files["base"].get,
            files["head"].get,
            set(),
            set(),
            False,
        )

    def test_granting_an_existing_operation_to_agents_is_recorded(self) -> None:
        decision = self.catalog(
            {"provision": {"mutation": True}},
            {"provision": {"mutation": True, "agent": True}},
        )
        self.assertEqual((decision.state, decision.escalate), ("success", False))
        self.assertIn("newly granted to agents", " ".join(decision.findings))

    def test_unmarking_a_mutation_needs_an_operator(self) -> None:
        decision = self.catalog(
            {"apply": {"mutation": True, "agent": True}},
            {"apply": {"mutation": False, "agent": True}},
        )
        self.assertTrue(decision.escalate)

    def test_new_agent_operations_and_other_edits_pass(self) -> None:
        decision = self.catalog(
            {"apply": {"mutation": True, "agent": True, "commands": ["a"]}},
            {
                "apply": {"mutation": True, "agent": True, "commands": ["b"]},
                "new-op": {"mutation": True, "agent": True},
            },
        )
        self.assertEqual(decision.state, "success")


if __name__ == "__main__":
    unittest.main()
