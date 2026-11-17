from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import chdir
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.ci import promote_commit
from tests.gitops_render import config_map

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


def embedded(config_map_name: str, key: str) -> dict:
    source = config_map("automation", config_map_name)["data"][key]
    scope = {"__name__": "migration_test"}
    with patch("builtins.open", return_value=io.StringIO("test-topic")):
        exec(compile(source, key, "exec"), scope)  # noqa: S102 - test the mounted script
    return scope


class AutomationTest(unittest.TestCase):
    def test_api_accepts_an_empty_success_response(self) -> None:
        response = Mock()
        response.status = 200
        response.read.return_value = b""
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(
            promote_commit.urllib.request, "urlopen", return_value=response
        ):
            result = promote_commit._api(
                "fixture-token", "/pulls/42/update?style=rebase", data={}
            )
        self.assertEqual(result, {})

    def test_auto_merge_waits_for_the_required_homelab_status(self) -> None:
        pull = {
            "number": 42,
            "head": {"sha": SHA},
            "base": {"sha": "base-sha"},
        }
        with patch.object(
            promote_commit,
            "_api",
            side_effect=[
                {},
                pull,
                pull,
                {
                    "statuses": [
                        {
                            "context": "ci/woodpecker/validation-v2",
                            "status": "success",
                            "updated_at": "2026-10-12T00:00:00Z",
                        },
                        {
                            "context": "policy/merge",
                            "status": "success",
                            "updated_at": "2026-10-12T00:00:00Z",
                        },
                    ]
                },
                {},
            ],
        ) as api:
            promote_commit._auto_merge_pull("fixture-token", pull)
        self.assertEqual(api.call_count, 5)
        self.assertEqual(
            api.call_args_list[0].args,
            ("fixture-token", "/pulls/42/update?style=rebase"),
        )
        self.assertEqual(
            api.call_args_list[-1].args,
            ("fixture-token", "/pulls/42/merge"),
        )
        self.assertEqual(api.call_args_list[-1].kwargs["data"], {"Do": "merge"})

    def test_auto_merge_waits_through_a_null_status_list(self) -> None:
        pull = {"number": 42, "head": {"sha": SHA}, "base": {"sha": "base-sha"}}
        success = {
            "statuses": [
                {
                    "context": "ci/woodpecker/validation-v2",
                    "status": "success",
                    "updated_at": "2026-10-12T00:00:00Z",
                },
                {
                    "context": "policy/merge",
                    "status": "success",
                    "updated_at": "2026-10-12T00:00:00Z",
                },
            ]
        }
        with (
            patch.object(
                promote_commit,
                "_api",
                side_effect=[{}, pull, pull, {"statuses": None}, pull, success, {}],
            ) as api,
            patch.object(promote_commit.time, "sleep"),
        ):
            promote_commit._auto_merge_pull("fixture-token", pull)
        self.assertEqual(api.call_args_list[-1].args[1], "/pulls/42/merge")

    def test_auto_merge_refuses_when_the_merge_policy_fails(self) -> None:
        pull = {"number": 42, "head": {"sha": SHA}, "base": {"sha": "base-sha"}}
        statuses = {
            "statuses": [
                {
                    "context": "ci/woodpecker/validation-v2",
                    "status": "success",
                    "updated_at": "1",
                },
                {"context": "policy/merge", "status": "failure", "updated_at": "1"},
            ]
        }
        with (
            patch.object(
                promote_commit, "_api", side_effect=[{}, pull, pull, statuses]
            ),
            self.assertRaisesRegex(ValueError, "Required homelab validation failed"),
        ):
            promote_commit._auto_merge_pull("fixture-token", pull)

    def test_auto_merge_rejects_a_failed_required_status(self) -> None:
        pull = {
            "number": 42,
            "head": {"sha": SHA},
            "base": {"sha": "base-sha"},
        }
        with (
            patch.object(
                promote_commit,
                "_api",
                side_effect=[
                    {},
                    pull,
                    pull,
                    {
                        "statuses": [
                            {
                                "context": "ci/woodpecker/validation-v2",
                                "status": "failure",
                                "updated_at": "2026-10-12T00:00:00Z",
                            }
                        ]
                    },
                ],
            ) as api,
            self.assertRaisesRegex(ValueError, "validation failed"),
        ):
            promote_commit._auto_merge_pull("fixture-token", pull)
        self.assertEqual(api.call_count, 4)

    def test_auto_merge_rebases_when_main_advances_and_waits_for_new_status(
        self,
    ) -> None:
        pull = {
            "number": 42,
            "head": {"sha": SHA},
            "base": {"sha": "old-base"},
        }
        updated = {
            "number": 42,
            "head": {"sha": "rebased-sha"},
            "base": {"sha": "new-base"},
        }
        advanced = {
            "number": 42,
            "head": {"sha": SHA},
            "base": {"sha": "new-base"},
        }
        with (
            patch.object(
                promote_commit,
                "_api",
                side_effect=[
                    {},
                    pull,
                    advanced,
                    {},
                    updated,
                    updated,
                    {
                        "statuses": [
                            {
                                "context": promote_commit.REQUIRED_CONTEXT,
                                "status": "success",
                            },
                            {"context": "policy/merge", "status": "success"},
                        ]
                    },
                    {},
                ],
            ) as api,
            patch.object(promote_commit.time, "sleep"),
        ):
            promote_commit._auto_merge_pull("fixture-token", pull)
        self.assertEqual(
            api.call_args_list[3].args,
            ("fixture-token", "/pulls/42/update?style=rebase"),
        )
        self.assertEqual(
            api.call_args_list[-1].args,
            ("fixture-token", "/pulls/42/merge"),
        )

    def test_auto_promotion_lock_scope_rejects_unrelated_records(self) -> None:
        previous = {
            "generated_at": "old",
            "images": [
                {"destination_repository": "apps/other", "digest": "same"},
                {"destination_repository": "apps/nix-agent-site", "digest": "old"},
            ],
        }
        current = {
            "generated_at": "new",
            "images": [
                {"destination_repository": "apps/nix-agent-site", "digest": "new"},
                {"destination_repository": "apps/other", "digest": "same"},
            ],
        }
        old_image = "image: registry.rupan.dev/apps/nix-agent-site@sha256:" + "a" * 64
        new_image = old_image.replace("a" * 64, "b" * 64)
        consumers = [
            "gitops/websites/nixagent/site.yaml",
            "gitops/websites/nixagent/deployment.yaml",
        ]
        changed = ["registry/images.lock.json", *consumers]

        def head(command: list[str], text: bool) -> str:
            path = command[-1].removeprefix("HEAD:")
            if path == "registry/images.lock.json":
                return json.dumps(previous)
            return f"profile: static\n{old_image}\n"

        with tempfile.TemporaryDirectory() as directory, chdir(directory):
            path = Path("registry/images.lock.json")
            path.parent.mkdir()
            path.write_text(json.dumps(current))
            for consumer in consumers:
                Path(consumer).parent.mkdir(parents=True, exist_ok=True)
                Path(consumer).write_text(f"profile: static\n{new_image}\n")
            with patch.object(
                promote_commit.subprocess, "check_output", side_effect=head
            ):
                promote_commit._assert_nix_agent_only(changed)
                with self.assertRaisesRegex(ValueError, "only the nix-agent digest"):
                    promote_commit._assert_nix_agent_only(changed[:2])
                Path(consumers[0]).write_text(f"profile: server\n{new_image}\n")
                with self.assertRaisesRegex(ValueError, "more than digests"):
                    promote_commit._assert_nix_agent_only(changed)
                Path(consumers[0]).write_text(f"profile: static\n{new_image}\n")
                current["images"][1]["digest"] = "changed"
                path.write_text(json.dumps(current))
                with self.assertRaisesRegex(ValueError, "unrelated lock policy"):
                    promote_commit._assert_nix_agent_only(changed)

    def test_promotions_use_review_branches_and_forgejo_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory, chdir(directory):
            path = Path("registry/images.lock.json")
            path.parent.mkdir()
            path.write_text("{}")
            response = {"html_url": "review"}
            environment = {
                "CI_COMMIT_SHA": SHA,
                "CI_OPERATION_AUTHORITY": "woodpecker",
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
                pushes[0].kwargs["env"]["CI_PUBLISH_USERNAME"], "homelab-publisher"
            )
            request = api.call_args_list[-1].args[0]
            self.assertEqual(json.loads(request.data)["base"], "main")
            self.assertIn("git.rupan.dev", request.full_url)

    def test_dashboard_preserves_ui_and_binds_approval_to_validated_head(self) -> None:
        scope = embedded("forgejo-dashboard-script", "dashboard.py")
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
            scope = embedded("renovate-reviewer-script", "renovate-agent.py")
            api = Mock(
                side_effect=[
                    {
                        "statuses": [
                            {
                                "context": "ci/woodpecker/validation-v2",
                                "status": "success",
                            },
                            {"context": "policy/merge", "status": "success"},
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

    def test_reviewer_waits_for_every_required_check(self) -> None:
        for context in ("ci/woodpecker/validation-v2", "policy/merge"):
            for state in (None, "pending", "failure", "error"):
                with self.subTest(context=context, state=state):
                    scope = embedded("renovate-reviewer-script", "renovate-agent.py")
                    statuses = [
                        {
                            "context": name,
                            "status": state if name == context else "success",
                        }
                        for name in ("ci/woodpecker/validation-v2", "policy/merge")
                        if name != context or state is not None
                    ]
                    api = Mock(
                        side_effect=[
                            {"statuses": statuses},
                            [
                                {
                                    "user": {"login": "JEFF7712"},
                                    "body": "/renovate-agent approve " + SHA,
                                }
                            ],
                        ]
                    )
                    scope.update(forgejo=api, notify=Mock(), patch_state=Mock())
                    scope["process_forgejo_pr"](
                        "JEFF7712",
                        "homelab",
                        {
                            "number": 4,
                            "head": {"sha": SHA},
                        },
                        {},
                    )
                    self.assertFalse(
                        any(call.args[0] == "POST" for call in api.call_args_list)
                    )
                    self.assertIn(context, scope["notify"].call_args.args[2])

    def test_native_automerge_owns_eligible_prs(self) -> None:
        scope = embedded("renovate-reviewer-script", "renovate-agent.py")
        api = Mock(
            side_effect=[
                {
                    "statuses": [
                        {"context": "ci/woodpecker/validation-v2", "status": "success"},
                        {"context": "policy/merge", "status": "success"},
                    ]
                },
                [],
            ]
        )
        scope.update(forgejo=api, notify=Mock(), patch_state=Mock())
        scope["process_forgejo_pr"](
            "JEFF7712",
            "homelab",
            {
                "number": 4,
                "head": {"sha": SHA},
                "user": {"login": "homelab-renovate"},
                "body": "**Automerge**: Enabled.",
            },
            {},
        )
        self.assertFalse(any(call.args[0] == "POST" for call in api.call_args_list))
        message = scope["notify"].call_args.args[2]
        self.assertIn("Native automerge enabled", message)
        self.assertNotIn("Approve this exact commit", message)
        receipt = json.loads(scope["patch_state"].call_args.args[1])
        self.assertTrue(receipt["native_automerge"])
        self.assertFalse(receipt["approval"]["approved"])

    def test_forgejo_https_configuration_contract(self) -> None:
        forgejo_nix = (ROOT / "flake/modules/forgejo.nix").read_text()
        self.assertIn('default = "git.rupan.dev";', forgejo_nix)
        self.assertIn('ROOT_URL = "https://${cfg.hostName}/";', forgejo_nix)
        self.assertIn("DOMAIN = cfg.hostName;", forgejo_nix)
        self.assertIn("virtualHosts.${cfg.hostName}", forgejo_nix)
        self.assertIn("onlySSL = true;", forgejo_nix)
        self.assertIn("useACMEHost = cfg.hostName;", forgejo_nix)
        self.assertIn('dnsProvider = "cloudflare";', forgejo_nix)

        nas_nix = (ROOT / "flake/hosts/nas-01/default.nix").read_text()
        self.assertIn("homelab.forgejo", nas_nix)
        self.assertIn('acmeEmail = "rupanpandyan@gmail.com";', nas_nix)

        woodpecker_nix = (ROOT / "flake/modules/woodpecker.nix").read_text()
        self.assertIn('default = "https://git.rupan.dev";', woodpecker_nix)
        self.assertIn('"git.rupan.dev"', woodpecker_nix)

        rewrites = json.loads((ROOT / "inventory/adguard-rewrites.json").read_text())
        fleet = json.loads((ROOT / "inventory/fleet.json").read_text())
        self.assertIn(
            {
                "domain": "git.rupan.dev",
                "answer": fleet["hosts"]["nas-01"]["ip"],
                "enabled": True,
            },
            rewrites,
        )

        coredns = (ROOT / "gitops/platform/coredns-custom.yaml").read_text()
        self.assertIn("10.0.30.20 git.rupan.dev", coredns)


class FirstPartyPromotionGuardTest(unittest.TestCase):
    def repository(self, directory: str) -> Path:
        root = Path(directory)
        (root / "gitops/app").mkdir(parents=True)
        (root / "registry").mkdir()
        (root / "gitops/app/deployment.yaml").write_text(
            "image: registry.rupan.dev/apps/app@sha256:" + "a" * 64 + "\nreplicas: 1\n"
        )
        lock = {
            "generated_at": "then",
            "images": [
                {
                    "id": "first-party-app",
                    "destination_repository": "apps/app",
                    "digest": "sha256:" + "a" * 64,
                },
                {
                    "id": "upstream",
                    "destination_repository": "upstream/x",
                    "digest": "sha256:" + "c" * 64,
                },
            ],
        }
        (root / "registry/images.lock.json").write_text(json.dumps(lock))
        for command in (
            ["git", "init", "-q"],
            ["git", "add", "."],
            [
                "git",
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "-qm",
                "init",
            ],
        ):
            subprocess.run(command, cwd=root, check=True)
        return root

    def run_guard(self, root: Path, changed: list[str]) -> None:
        with chdir(root):
            promote_commit._assert_first_party_digests_only(changed)

    def test_digest_moves_pass_and_anything_else_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.repository(directory)
            deployment = root / "gitops/app/deployment.yaml"
            lock_path = root / "registry/images.lock.json"
            deployment.write_text(deployment.read_text().replace("a" * 64, "b" * 64))
            lock = json.loads(lock_path.read_text())
            lock["generated_at"] = "now"
            lock["images"][0]["digest"] = "sha256:" + "b" * 64
            lock_path.write_text(json.dumps(lock))
            changed = ["gitops/app/deployment.yaml", "registry/images.lock.json"]
            self.run_guard(root, changed)

            deployment.write_text(
                deployment.read_text().replace("replicas: 1", "replicas: 3")
            )
            with self.assertRaisesRegex(ValueError, "more than digests"):
                self.run_guard(root, changed)
            subprocess.run(["git", "checkout", "-q", "gitops"], cwd=root, check=True)

            lock["images"][1]["digest"] = "sha256:" + "d" * 64
            lock_path.write_text(json.dumps(lock))
            with self.assertRaisesRegex(ValueError, "non-first-party"):
                self.run_guard(root, ["registry/images.lock.json"])
            with self.assertRaisesRegex(ValueError, "may not change"):
                self.run_guard(root, ["flake/flake.nix"])


class PromotionSupersedeTest(unittest.TestCase):
    def test_only_same_scope_or_legacy_promotions_are_superseded(self) -> None:
        def pull(ref: str) -> dict:
            return {"number": len(ref), "head": {"ref": ref}}

        pulls = [
            pull("registry-promotion/first-party-aaaaaaaaaaaa"),
            pull("registry-promotion/first-party-bbbbbbbbbbbb"),
            pull("registry-promotion/nix-agent-site-cccccccccccc"),
            pull("registry-promotion/f21a4a2a8e35-ecd6991af91e"),
            pull("feat/unrelated"),
        ]
        closed = promote_commit.superseded_pulls(
            pulls, "first-party", "registry-promotion/first-party-bbbbbbbbbbbb"
        )
        self.assertEqual(
            [item["head"]["ref"] for item in closed],
            [
                "registry-promotion/first-party-aaaaaaaaaaaa",
                "registry-promotion/f21a4a2a8e35-ecd6991af91e",
            ],
        )
