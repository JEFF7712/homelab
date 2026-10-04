from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import chdir
from pathlib import Path
from unittest.mock import Mock, patch

import tomllib
import yaml

from scripts.ci import gitlab_operation, promote_commit
from scripts.ci.prepare_runner import configuration

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


def embedded(filename: str, key: str) -> dict:
    source = yaml.safe_load((ROOT / "gitops/automation" / filename).read_text())[
        "data"
    ][key]
    scope = {"__name__": "migration_test"}
    with patch("builtins.open", return_value=io.StringIO("test-topic")):
        exec(compile(source, filename, "exec"), scope)
    return scope


class AutomationTest(unittest.TestCase):
    def test_promotions_use_review_branches_and_correct_forge_credentials(self) -> None:
        for recovery in [False, True]:
            with tempfile.TemporaryDirectory() as directory, chdir(directory):
                path = Path("registry/images.lock.json")
                path.parent.mkdir()
                path.write_text("{}")
                response = {"web_url": "review", "html_url": "review"}
                environment = {
                    "CI_COMMIT_SHA": SHA,
                    "CI_OPERATION_AUTHORITY": "gitlab" if recovery else "woodpecker",
                    "GITLAB_PUBLISH_TOKEN": "gitlab-fixture",
                    "FORGEJO_PUBLISH_TOKEN": "forgejo-fixture",
                }
                with (
                    patch.dict(os.environ, environment, clear=True),
                    patch.object(promote_commit, "current_source"),
                    patch.object(
                        promote_commit.subprocess,
                        "check_output",
                        return_value=str(path) + "\n",
                    ),
                    patch.object(promote_commit.subprocess, "run") as git,
                    patch.object(
                        promote_commit.urllib.request,
                        "urlopen",
                        side_effect=[
                            io.BytesIO(b"[]"),
                            io.BytesIO(json.dumps(response).encode()),
                        ],
                    ) as api,
                ):
                    promote_commit.main()
                pushes = [call for call in git.call_args_list if "push" in call.args[0]]
                self.assertEqual(len(pushes), 1)
                command = pushes[0].args[0]
                self.assertTrue(
                    command[-1].startswith("HEAD:refs/heads/registry-promotion/")
                )
                self.assertFalse(any("--force" in word for word in command))
                self.assertEqual(
                    pushes[0].kwargs["env"]["CI_PUBLISH_USERNAME"],
                    "oauth2" if recovery else "homelab-publisher",
                )
                request = api.call_args_list[-1].args[0]
                body = json.loads(request.data)
                self.assertEqual(body["target_branch" if recovery else "base"], "main")
                self.assertIn(
                    "gitlab.com" if recovery else "git.internal", request.full_url
                )

    def test_gitlab_maps_role_secrets_and_file_variables(self) -> None:
        catalog = json.loads((ROOT / "config/ci/operations.json").read_text())
        operation = catalog["cloudflare-plan"]
        with tempfile.TemporaryDirectory() as directory:
            ca = Path(directory) / "ca"
            ca.write_text("ca-fixture")
            environment = {
                value["from_secret"].upper(): "secret-fixture"
                for value in operation["environment"].values()
                if isinstance(value, dict)
            }
            environment.update(
                CI_OPERATION="cloudflare-plan",
                CI_PIPELINE_ID="19",
                STATE_CA_FILE=str(ca),
                DR_STATE_PLAN_PASSWORD="dr-plan-fixture",
                CLOUDFLARE_PLAN_API_TOKEN="plan-provider-fixture",
            )
            captured = {}
            with (
                patch.dict(os.environ, environment, clear=True),
                patch.object(
                    gitlab_operation,
                    "run",
                    side_effect=lambda *args: captured.update(os.environ),
                ),
            ):
                gitlab_operation.main()
            self.assertEqual(captured["CLOUDFLARE_API_TOKEN"], "plan-provider-fixture")
            self.assertEqual(captured["STATE_CA_FILE"], "ca-fixture")
            self.assertEqual(captured["TF_HTTP_USERNAME"], "gitlab-plan")
            self.assertEqual(captured["TF_HTTP_PASSWORD"], "dr-plan-fixture")
            self.assertEqual(captured["CI_OPERATION_PARENT"], "19")

    def test_dashboard_preserves_ui_and_binds_approval_to_validated_head(self) -> None:
        scope = embedded("forgejo-dashboard-script.yaml", "dashboard.py")
        self.assertIn("status_payload", scope)
        self.assertIn("reviewer_run_status", scope)
        self.assertIn("github_status", scope)
        pr = {
            "state": "open",
            "head": {"sha": SHA, "ref": "renovate/nix"},
            "mergeable": True,
        }
        scope.update(
            FORGEJO_APPROVAL_TOKEN="operator-fixture",
            forgejo_allowlist=lambda: {("jeff7712", "homelab")},
            forgejo=Mock(return_value=pr),
            forgejo_checks=Mock(return_value={"state": "success"}),
            request=Mock(),
        )
        scope["approve_forgejo_mr"]("JEFF7712", "homelab", 4, SHA)
        self.assertEqual(
            scope["request"].call_args.args[3]["body"], "/renovate-agent approve " + SHA
        )
        scope["request"].reset_mock()
        with self.assertRaises(ValueError):
            scope["approve_forgejo_mr"]("JEFF7712", "homelab", 4, "b" * 40)
        scope["forgejo_checks"].return_value = {"state": "failure"}
        with self.assertRaises(ValueError):
            scope["approve_forgejo_mr"]("JEFF7712", "homelab", 4, SHA)
        scope["request"].assert_not_called()

    def test_reviewer_uses_native_status_and_rejects_stale_or_bot_approval(
        self,
    ) -> None:
        for author, body, expected in [
            ("JEFF7712", "/renovate-agent approve " + SHA, True),
            ("JEFF7712", "/renovate-agent approve " + "b" * 40, False),
            ("renovate", "/renovate-agent approve " + SHA, False),
            ("JEFF7712", "/renovate-agent approve", False),
        ]:
            scope = embedded("renovate-reviewer-script.yaml", "renovate-agent.py")
            api = Mock(
                side_effect=[
                    {
                        "statuses": [
                            {
                                "context": "ci/woodpecker/validation-v2",
                                "status": "success",
                            }
                        ]
                    },
                    [{"user": {"login": author}, "body": body}],
                    {},
                ]
            )
            scope.update(forgejo=api, notify=Mock(), patch_state=Mock())
            scope["process_forgejo_pr"](
                "JEFF7712",
                "homelab",
                {"number": 4, "head": {"sha": SHA}, "title": "renovate"},
                {},
            )
            merges = [call for call in api.call_args_list if call.args[0] == "POST"]
            self.assertEqual(bool(merges), expected)
            if merges:
                self.assertEqual(merges[0].args[2]["head_commit_id"], SHA)
            receipt = json.loads(scope["patch_state"].call_args.args[1])
            self.assertEqual(receipt["approval"]["approved"], expected)

    def test_dr_runners_have_separate_tokens_and_no_host_mounts(self) -> None:
        config = tomllib.loads(
            configuration("validation-fixture", "operations-fixture")
        )
        self.assertEqual(config["concurrent"], 2)
        for runner in config["runners"]:
            self.assertEqual(runner["executor"], "docker")
            self.assertFalse(runner["docker"]["privileged"])
            self.assertEqual(runner["docker"]["volumes"], [])
        with self.assertRaises(ValueError):
            configuration("same", "same")
        with self.assertRaises(ValueError):
            configuration("", "operations")
