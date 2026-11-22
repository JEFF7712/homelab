from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml

from scripts.ci import policy
from scripts.ci.policy import (
    VALIDATION,
    VALIDATION_COMMAND,
    SourceMirror,
    configuration,
    find_branch_validation,
    resolve_configuration,
)
from scripts.ci.scope import determine

ROOT = Path(__file__).resolve().parents[1]
HEAD = "b" * 40
TREE = "c" * 40
PARENT = "d" * 40
MERGE = "e" * 40
CATALOG = json.loads((ROOT / "config/ci/operations.json").read_text())


def pipeline(**overrides: Any) -> dict[str, Any]:
    item = {
        "number": 12,
        "commit": HEAD,
        "event": "push",
        "branch": "feat/x",
        "status": "success",
        "finished": 1000,
    }
    item.update(overrides)
    return item


def green(number: int) -> dict[str, Any]:
    return {"workflows": [{"name": VALIDATION, "state": "success"}]}


def request(ref: str, commit: str) -> dict[str, Any]:
    return {
        "repo": {"id": 1, "owner": "JEFF7712", "name": "homelab"},
        "pipeline": {
            "id": 7,
            "number": 7,
            "event": "push",
            "ref": ref,
            "commit": commit,
            "author": "homelab-agent",
        },
        "netrc": {"login": "reader", "password": "token"},
    }


def workflows(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        config["name"].removesuffix(".yaml"): yaml.safe_load(config["data"])
        for config in result["configs"]
    }


class FindBranchValidationTest(unittest.TestCase):
    records = {HEAD: (TREE, PARENT)}

    def find(
        self,
        pipelines: list[dict[str, Any]],
        tree: str = TREE,
        parent: str = PARENT,
        details: Any = green,
        now: int = 2000,
    ) -> dict[str, Any] | None:
        return find_branch_validation(
            tree, parent, self.records, pipelines, details, now
        )

    def test_same_tree_over_the_same_parent_reuses_the_branch_push(self) -> None:
        self.assertEqual(self.find([pipeline()])["number"], 12)

    def test_any_difference_in_content_or_base_validates_again(self) -> None:
        # A different tree is different content; a different first parent
        # means main moved after the branch was validated, so the branch's
        # scope no longer equals the merge's.
        self.assertIsNone(self.find([pipeline()], tree="f" * 40))
        self.assertIsNone(self.find([pipeline()], parent="f" * 40))
        self.assertIsNone(self.find([pipeline(commit="f" * 40)]))

    def test_only_a_green_recent_same_repository_branch_push_counts(self) -> None:
        for item in (
            pipeline(status="failure"),
            pipeline(status="running"),
            pipeline(event="pull_request"),
            pipeline(event="manual"),
            pipeline(branch="main"),
            pipeline(from_fork=True),
            pipeline(finished=2000 - 86401),
        ):
            with self.subTest(item=item):
                self.assertIsNone(self.find([item]))

    def test_the_validation_workflow_itself_must_have_passed(self) -> None:
        for details in (
            lambda number: {"workflows": []},
            lambda number: {"workflows": [{"name": VALIDATION, "state": "failure"}]},
            lambda number: {"workflows": [{"name": "other", "state": "success"}]},
        ):
            with self.subTest(details=details):
                self.assertIsNone(self.find([pipeline()], details=details))


class ConfigurationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = json.loads((ROOT / "config/ci/operations.json").read_text())

    def test_main_push_reusing_a_branch_runs_no_validation_but_keeps_its_gate(
        self,
    ) -> None:
        result = workflows(
            configuration(
                request("refs/heads/main", MERGE),
                self.catalog,
                current=MERGE,
                changed_files=("docs/a.md",),
                branch_validation=pipeline(),
            )
        )
        step = result[VALIDATION]["steps"][0]
        self.assertNotIn(VALIDATION_COMMAND, step["commands"])
        self.assertIn("#12", step["commands"][0])
        self.assertIn(HEAD, step["commands"][0])
        # Deployments and the plans still find a passed validation-v2.
        for plan in ("opnsense-plan", "cloudflare-plan", "cloudflare-access-plan"):
            self.assertEqual(result[plan]["depends_on"], [VALIDATION])

    def test_main_push_without_a_match_validates(self) -> None:
        step = workflows(
            configuration(
                request("refs/heads/main", MERGE), self.catalog, current=MERGE
            )
        )[VALIDATION]["steps"][0]
        self.assertIn(VALIDATION_COMMAND, step["commands"])

    def test_only_a_branch_push_that_contains_main_validates_as_main(self) -> None:
        def environment(ref: str, backstop: bool) -> dict[str, str]:
            result = configuration(
                request(ref, HEAD), self.catalog, current=HEAD, main_backstop=backstop
            )
            return workflows(result)[VALIDATION]["steps"][0]["environment"]

        self.assertEqual(
            environment("refs/heads/feat/x", True)["CI_MAIN_BACKSTOP"], "1"
        )
        self.assertNotIn("CI_MAIN_BACKSTOP", environment("refs/heads/feat/x", False))
        self.assertNotIn("CI_MAIN_BACKSTOP", environment("refs/heads/main", True))


class MirrorReuseTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.origin = self.root / "origin"
        self.origin.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        self.commit("README.md", "baseline")
        self.git("checkout", "-q", "-b", "feature")
        self.commit("gitops/site.yaml", "feature")
        self.mirror = SourceMirror(self.root / "mirror.git", remote=str(self.origin))
        self.addCleanup(policy.BRANCH_VALIDATIONS.clear)
        policy.BRANCH_VALIDATIONS.clear()

    def git(self, *args: str) -> str:
        return subprocess.check_output(
            ["git", *args], cwd=self.origin, text=True
        ).strip()

    def commit(self, name: str, message: str) -> None:
        path = self.origin / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(message + "\n")
        self.git("add", name)
        self.git("commit", "-qm", message)

    def merge_into_main(self) -> str:
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "--no-edit", "feature")
        return self.git("rev-parse", "HEAD")

    def resolve(self, ref: str, commit: str, pipelines: list[dict[str, Any]]) -> Any:
        def fake_api(url: str, token: str) -> Any:
            del token
            if "branches/main" in url:
                return {"commit": {"id": self.git("rev-parse", "main")}}
            if url.endswith("/pipelines?perPage=50"):
                return pipelines
            return green(0)

        with (
            patch("scripts.ci.policy.api", side_effect=fake_api),
            patch.dict(os.environ, {"POLICY_API_TOKEN": "t"}),
        ):
            return workflows(
                resolve_configuration(
                    request(ref, commit), CATALOG, (), (), self.mirror
                )
            )

    def test_an_up_to_date_branch_is_reused_by_its_merge(self) -> None:
        head = self.git("rev-parse", "feature")
        branch = self.resolve("refs/heads/feature", head, [])
        self.assertEqual(
            branch[VALIDATION]["steps"][0]["environment"]["CI_MAIN_BACKSTOP"], "1"
        )
        merge = self.merge_into_main()
        green_branch = pipeline(commit=head, finished=int(time.time()))
        step = self.resolve("refs/heads/main", merge, [green_branch])[VALIDATION][
            "steps"
        ][0]
        self.assertNotIn(VALIDATION_COMMAND, step["commands"])

    def test_a_branch_behind_main_is_validated_again_after_merge(self) -> None:
        self.git("checkout", "-q", "main")
        self.commit("justfile", "main moves on")
        self.git("checkout", "-q", "feature")
        head = self.git("rev-parse", "feature")
        branch = self.resolve("refs/heads/feature", head, [])
        self.assertNotIn(
            "CI_MAIN_BACKSTOP", branch[VALIDATION]["steps"][0]["environment"]
        )
        merge = self.merge_into_main()
        green_branch = pipeline(commit=head, finished=int(time.time()))
        step = self.resolve("refs/heads/main", merge, [green_branch])[VALIDATION][
            "steps"
        ][0]
        self.assertIn(VALIDATION_COMMAND, step["commands"])


class BackstopScopeTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.run_git("init", "-q", "-b", "main")
        self.run_git("config", "user.email", "test@example.invalid")
        self.run_git("config", "user.name", "Test")
        (self.root / "README.md").write_text("docs\n")
        self.run_git("add", "README.md")
        self.run_git("commit", "-qm", "docs")
        head = self.run_git("rev-parse", "HEAD")
        self.env = {
            "CI_PIPELINE_SOURCE": "push",
            "CI_COMMIT_SHA": head,
            "CI_COMMIT_BRANCH": "feat/x",
            "CI_SCOPE_FILES": json.dumps(["README.md"]),
        }

    def run_git(self, *args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=self.root, text=True).strip()

    def test_a_backstopped_branch_runs_main_scope(self) -> None:
        main = determine(self.root, dict(self.env, CI_COMMIT_BRANCH="main"))
        backstop = determine(self.root, dict(self.env, CI_MAIN_BACKSTOP="1"))
        self.assertEqual(backstop.checks, main.checks)
        self.assertIn("python", backstop.checks)
        self.assertEqual(determine(self.root, self.env).mode, "documentation")


if __name__ == "__main__":
    unittest.main()
