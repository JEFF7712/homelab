from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.ci.applications import Application
from scripts.ci.development_projects import (
    NODE_VALIDATION_IMAGE,
    UV_VALIDATION_IMAGE,
    DevelopmentProject,
    load_development_projects,
)
from scripts.ci.development_projects import (
    configuration as development_configuration,
)
from scripts.ci.policy import resolve_configuration

ROOT = Path(__file__).resolve().parents[1]


class DevelopmentCatalogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "projects.json"
        self.data = json.loads((ROOT / "config/ci/projects.json").read_text())

    def load(self) -> tuple[DevelopmentProject, ...]:
        self.path.write_text(json.dumps(self.data))
        return load_development_projects(self.path)

    def test_actual_manifest_loads_and_contains_autocompress(self) -> None:
        projects = self.load()
        autocompress = next((p for p in projects if p.name == "autocompress"), None)
        self.assertIsNotNone(autocompress)
        self.assertEqual(autocompress.repository, "JEFF7712/autocompress")
        self.assertEqual(autocompress.kind, "python-uv-v1")
        self.assertEqual(autocompress.tier, "sandbox")
        self.assertEqual(autocompress.default_branch, "main")

    def test_validation_image_is_pinned_in_registry_lock(self) -> None:
        lock = json.loads((ROOT / "registry/images.lock.json").read_text())
        self.assertTrue(
            any(
                UV_VALIDATION_IMAGE
                == f"{lock['destination_registry']}/{record['destination_repository']}@{record['digest']}"
                for record in lock["images"]
            ),
            f"{UV_VALIDATION_IMAGE} not found in registry/images.lock.json",
        )

    def test_node_validation_image_is_pinned_in_registry_lock(self) -> None:
        lock = json.loads((ROOT / "registry/images.lock.json").read_text())
        self.assertTrue(
            any(
                NODE_VALIDATION_IMAGE
                == f"{lock['destination_registry']}/{record['destination_repository']}@{record['digest']}"
                for record in lock["images"]
            ),
            f"{NODE_VALIDATION_IMAGE} not found in registry/images.lock.json",
        )

    def test_project_type_development_remains_enrolled_when_state_changes(self) -> None:
        test_data = copy.deepcopy(self.data)
        for p in test_data["projects"]:
            if p["name"] == "autocompress":
                p["project_type"] = "development"
                p["state"] = "already-primary"
                p["migration_status"] = "complete"
                break
        self.path.write_text(json.dumps(test_data))
        loaded = load_development_projects(self.path)
        autocompress = next((p for p in loaded if p.name == "autocompress"), None)
        self.assertIsNotNone(autocompress)
        self.assertEqual(autocompress.state, "already-primary")

    def test_invalid_manifest_fails_closed(self) -> None:
        # 1. Invalid schema version
        bad_version = copy.deepcopy(self.data)
        bad_version["schema_version"] = 2
        self.path.write_text(json.dumps(bad_version))
        with self.assertRaisesRegex(
            ValueError, "Unsupported development projects schema"
        ):
            load_development_projects(self.path)

        # 2. Missing projects array
        bad_projects = copy.deepcopy(self.data)
        bad_projects["projects"] = "not-a-list"
        self.path.write_text(json.dumps(bad_projects))
        with self.assertRaisesRegex(
            ValueError, "Unsupported development projects schema"
        ):
            load_development_projects(self.path)

        # 3. Development project claiming privileged tier
        bad_tier = copy.deepcopy(self.data)
        for p in bad_tier["projects"]:
            if p["name"] == "autocompress":
                p["ci"]["tier"] = "deploy"
                break
        self.path.write_text(json.dumps(bad_tier))
        with self.assertRaisesRegex(ValueError, "cannot claim privileged tier"):
            load_development_projects(self.path)

        # 4. Duplicate repository identity
        dup_repo = copy.deepcopy(self.data)
        dup_repo["projects"].append(
            {
                "name": "autocompress-dup",
                "github_repo": "JEFF7712/autocompress",
                "forgejo_repo": "JEFF7712/autocompress",
                "visibility": "public",
                "state": "migrate",
                "default_branch": "main",
                "ci": {"kind": "python-uv-v1", "tier": "sandbox"},
                "woodpecker_repository_id": None,
            }
        )
        self.path.write_text(json.dumps(dup_repo))
        with self.assertRaisesRegex(ValueError, "Duplicate repository"):
            load_development_projects(self.path)

        # 5. Invalid repository format or homelab identity
        bad_repo = copy.deepcopy(self.data)
        for p in bad_repo["projects"]:
            if p["name"] == "autocompress":
                p["forgejo_repo"] = "JEFF7712/homelab"
                break
        self.path.write_text(json.dumps(bad_repo))
        with self.assertRaisesRegex(ValueError, "Invalid repository identity"):
            load_development_projects(self.path)

        # 6. Repository ID <= 1 (reserved for homelab)
        bad_id = copy.deepcopy(self.data)
        for p in bad_id["projects"]:
            if p["name"] == "autocompress":
                p["woodpecker_repository_id"] = 1
                break
        self.path.write_text(json.dumps(bad_id))
        with self.assertRaisesRegex(ValueError, "integer greater than 1"):
            load_development_projects(self.path)


class DevelopmentPipelineGenerationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.project = DevelopmentProject(
            name="autocompress",
            repository="JEFF7712/autocompress",
            woodpecker_repository_id=100,
            default_branch="main",
            kind="python-uv-v1",
            tier="sandbox",
            visibility="public",
            state="migrate",
        )
        self.request = {
            "repo": {
                "id": 100,
                "owner": "JEFF7712",
                "name": "autocompress",
                "default_branch": "main",
            },
            "pipeline": {
                "event": "push",
                "ref": "refs/heads/main",
                "commit": "a" * 40,
                "number": 1,
                "from_fork": False,
            },
        }

    def test_push_event_generates_sandbox_uv_validation(self) -> None:
        result = development_configuration(self.request, (self.project,))
        configs = result.get("configs", [])
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]["name"], "development-validation.yaml")

        workflow = yaml.safe_load(configs[0]["data"])
        self.assertEqual(workflow["when"], [{"event": "push"}])
        self.assertEqual(
            workflow["labels"],
            {"tier": "sandbox", "type": "docker", "platform": "linux/amd64"},
        )

        steps = workflow.get("steps", [])
        self.assertEqual(len(steps), 1)
        step = steps[0]
        self.assertEqual(step["name"], "python-uv-test")
        self.assertEqual(step["image"], UV_VALIDATION_IMAGE)
        self.assertEqual(
            step["commands"], ["uv run --frozen --all-groups pytest tests/"]
        )

        # Isolation checks: no credentials, host sockets, or privileged attributes
        self.assertNotIn("environment", step)
        self.assertNotIn("secrets", step)
        self.assertNotIn("volumes", step)
        self.assertNotIn("privileged", step)

    def test_push_event_generates_generic_python_validation(self) -> None:
        cctop = DevelopmentProject(
            name="cctop",
            repository="JEFF7712/cctop",
            woodpecker_repository_id=25,
            default_branch="main",
            kind="python-test-v1",
            tier="sandbox",
            visibility="private",
            state="migrate",
        )
        req = {
            "repo": {"id": 25, "owner": "JEFF7712", "name": "cctop"},
            "pipeline": {
                "event": "push",
                "ref": "refs/heads/main",
                "commit": "b" * 40,
                "number": 1,
                "from_fork": False,
            },
        }
        result = development_configuration(req, (cctop,))
        configs = result.get("configs", [])
        self.assertEqual(len(configs), 1)
        workflow = yaml.safe_load(configs[0]["data"])
        self.assertEqual(workflow["when"], [{"event": "push"}])
        self.assertEqual(workflow["labels"]["tier"], "sandbox")
        steps = workflow.get("steps", [])
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["name"], "python-test")
        self.assertEqual(
            steps[0]["commands"], ["uv run --with pytest --with pyyaml pytest tests/"]
        )

    def test_push_event_generates_python_syntax_validation(self) -> None:
        bayesian = DevelopmentProject(
            name="bayesian-reaction-optimization-engine",
            repository="JEFF7712/bayesian-reaction-optimization-engine",
            woodpecker_repository_id=27,
            default_branch="main",
            kind="python-syntax-v1",
            tier="sandbox",
            visibility="private",
            state="migrate",
        )
        req = {
            "repo": {
                "id": 27,
                "owner": "JEFF7712",
                "name": "bayesian-reaction-optimization-engine",
            },
            "pipeline": {
                "event": "push",
                "ref": "refs/heads/main",
                "commit": "c" * 40,
                "number": 1,
                "from_fork": False,
            },
        }
        result = development_configuration(req, (bayesian,))
        configs = result.get("configs", [])
        self.assertEqual(len(configs), 1)
        workflow = yaml.safe_load(configs[0]["data"])
        self.assertEqual(workflow["when"], [{"event": "push"}])
        self.assertEqual(workflow["labels"]["tier"], "sandbox")
        steps = workflow.get("steps", [])
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["name"], "python-syntax-check")
        self.assertEqual(steps[0]["commands"], ["python3 -m compileall -q ."])

    def test_custom_test_path_in_pipeline(self) -> None:
        predmarkbot = DevelopmentProject(
            name="predmarkbot",
            repository="JEFF7712/predmarkbot",
            woodpecker_repository_id=26,
            default_branch="main",
            kind="python-uv-v1",
            tier="sandbox",
            visibility="private",
            state="migrate",
            test_path="tests/unit/",
        )
        req = {
            "repo": {"id": 26, "owner": "JEFF7712", "name": "predmarkbot"},
            "pipeline": {
                "event": "push",
                "ref": "refs/heads/main",
                "commit": "d" * 40,
                "number": 1,
                "from_fork": False,
            },
        }
        result = development_configuration(req, (predmarkbot,))
        workflow = yaml.safe_load(result["configs"][0]["data"])
        self.assertEqual(
            workflow["steps"][0]["commands"],
            ["uv run --frozen --all-groups pytest tests/unit/"],
        )

    def test_pull_request_event_generates_validation(self) -> None:
        self.request["pipeline"]["event"] = "pull_request"
        self.request["pipeline"]["ref"] = "refs/pull/1/head"
        result = development_configuration(self.request, (self.project,))
        workflow = yaml.safe_load(result["configs"][0]["data"])
        self.assertEqual(workflow["when"], [{"event": "pull_request"}])
        self.assertEqual(workflow["labels"]["tier"], "sandbox")

    def test_pr_lifecycle_events_generate_safe_noop(self) -> None:
        for event in ["pull_request_closed", "pull_request_metadata"]:
            with self.subTest(event=event):
                self.request["pipeline"]["event"] = event
                result = development_configuration(self.request, (self.project,))
                self.assertEqual(result["configs"][0]["name"], "pr-lifecycle.yaml")
                workflow = yaml.safe_load(result["configs"][0]["data"])
                self.assertEqual(workflow["labels"]["tier"], "sandbox")

    def test_node_lint_build_pipeline_generation(self) -> None:
        proj = DevelopmentProject(
            name="apptlyAI-web",
            repository="JEFF7712/apptlyAI-web",
            woodpecker_repository_id=30,
            default_branch="main",
            kind="node-lint-build-v1",
            tier="sandbox",
            visibility="private",
            state="migrate",
        )
        req = {
            "repo": {"id": 30, "owner": "JEFF7712", "name": "apptlyAI-web"},
            "pipeline": {
                "event": "push",
                "ref": "refs/heads/main",
                "commit": "e" * 40,
                "number": 1,
                "from_fork": False,
            },
        }
        result = development_configuration(req, (proj,))
        workflow = yaml.safe_load(result["configs"][0]["data"])
        self.assertEqual(workflow["labels"]["tier"], "sandbox")
        step = workflow["steps"][0]
        self.assertEqual(step["name"], "node-lint-build")
        self.assertEqual(step["image"], NODE_VALIDATION_IMAGE)
        self.assertEqual(
            step["commands"],
            ["npm ci", "npm run lint", "npx tsc --noEmit", "npm run build"],
        )
        self.assertNotIn("environment", step)
        self.assertNotIn("secrets", step)
        self.assertNotIn("volumes", step)
        self.assertNotIn("privileged", step)

    def test_node_vitest_pipeline_generation(self) -> None:
        proj = DevelopmentProject(
            name="spotify-clone-temp-name",
            repository="JEFF7712/spotify-clone-temp-name",
            woodpecker_repository_id=31,
            default_branch="main",
            kind="node-vitest-v1",
            tier="sandbox",
            visibility="private",
            state="migrate",
        )
        req = {
            "repo": {"id": 31, "owner": "JEFF7712", "name": "spotify-clone-temp-name"},
            "pipeline": {
                "event": "push",
                "ref": "refs/heads/main",
                "commit": "f" * 40,
                "number": 1,
                "from_fork": False,
            },
        }
        result = development_configuration(req, (proj,))
        workflow = yaml.safe_load(result["configs"][0]["data"])
        self.assertEqual(workflow["labels"]["tier"], "sandbox")
        step = workflow["steps"][0]
        self.assertEqual(step["name"], "node-vitest-build")
        self.assertEqual(step["image"], NODE_VALIDATION_IMAGE)
        self.assertEqual(
            step["commands"],
            ["npm ci", "npm run lint", "npm test", "npx tsc --noEmit", "npm run build"],
        )
        self.assertNotIn("environment", step)
        self.assertNotIn("secrets", step)
        self.assertNotIn("volumes", step)
        self.assertNotIn("privileged", step)

    def test_node_build_pipeline_generation(self) -> None:
        proj = DevelopmentProject(
            name="apptlyAI",
            repository="JEFF7712/apptlyAI",
            woodpecker_repository_id=32,
            default_branch="main",
            kind="node-build-v1",
            tier="sandbox",
            visibility="private",
            state="migrate",
        )
        req = {
            "repo": {"id": 32, "owner": "JEFF7712", "name": "apptlyAI"},
            "pipeline": {
                "event": "push",
                "ref": "refs/heads/main",
                "commit": "0" * 40,
                "number": 1,
                "from_fork": False,
            },
        }
        result = development_configuration(req, (proj,))
        workflow = yaml.safe_load(result["configs"][0]["data"])
        self.assertEqual(workflow["labels"]["tier"], "sandbox")
        step = workflow["steps"][0]
        self.assertEqual(step["name"], "node-build")
        self.assertEqual(step["image"], NODE_VALIDATION_IMAGE)
        self.assertEqual(
            step["commands"],
            ["npm ci", "npx tsc --noEmit", "npm run build"],
        )
        self.assertNotIn("environment", step)
        self.assertNotIn("secrets", step)
        self.assertNotIn("volumes", step)
        self.assertNotIn("privileged", step)


class NegativeSecurityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.project = DevelopmentProject(
            name="autocompress",
            repository="JEFF7712/autocompress",
            woodpecker_repository_id=100,
            default_branch="main",
            kind="python-uv-v1",
            tier="sandbox",
            visibility="public",
            state="migrate",
        )
        self.request = {
            "repo": {
                "id": 100,
                "owner": "JEFF7712",
                "name": "autocompress",
                "default_branch": "main",
            },
            "pipeline": {
                "event": "push",
                "ref": "refs/heads/main",
                "commit": "a" * 40,
                "number": 1,
                "from_fork": False,
            },
        }

    def test_unknown_repo_id_fails_closed(self) -> None:
        req = copy.deepcopy(self.request)
        req["repo"]["id"] = 999
        with self.assertRaisesRegex(ValueError, "Development project is not enrolled"):
            development_configuration(req, (self.project,))

    def test_mismatched_repository_name_fails_closed(self) -> None:
        req = copy.deepcopy(self.request)
        req["repo"]["name"] = "other-repo"
        with self.assertRaisesRegex(ValueError, "Development project is not enrolled"):
            development_configuration(req, (self.project,))

    def test_unenrolled_project_with_none_id_fails_closed(self) -> None:
        unenrolled = DevelopmentProject(
            name="autocompress",
            repository="JEFF7712/autocompress",
            woodpecker_repository_id=None,
            default_branch="main",
            kind="python-uv-v1",
            tier="sandbox",
            visibility="public",
            state="migrate",
        )
        with self.assertRaisesRegex(ValueError, "Development project is not enrolled"):
            development_configuration(self.request, (unenrolled,))

    def test_malicious_variable_overrides_are_rejected(self) -> None:
        req = copy.deepcopy(self.request)
        req["pipeline"]["variables"] = {"DOCKER_HOST": "unix:///var/run/docker.sock"}
        with self.assertRaisesRegex(ValueError, "variable overrides"):
            development_configuration(req, (self.project,))

    def test_deployment_requests_are_rejected(self) -> None:
        for key in ["deploy", "deploy_to"]:
            with self.subTest(key=key):
                req = copy.deepcopy(self.request)
                req["pipeline"][key] = "production"
                with self.assertRaisesRegex(
                    ValueError, "operations or variable overrides"
                ):
                    development_configuration(req, (self.project,))

    def test_unauthorized_events_are_rejected(self) -> None:
        for event in ["tag", "release", "deployment", "cron"]:
            with self.subTest(event=event):
                req = copy.deepcopy(self.request)
                req["pipeline"]["event"] = event
                with self.assertRaisesRegex(
                    ValueError, "Unsupported development event"
                ):
                    development_configuration(req, (self.project,))

    def test_user_supplied_configs_cannot_escalate_privilege(self) -> None:
        req = copy.deepcopy(self.request)
        # Attempt to inject host socket mount and privileged flag in request
        req["configs"] = [
            {
                "privileged": True,
                "volumes": ["/var/run/docker.sock:/var/run/docker.sock"],
                "labels": {"tier": "deploy"},
            }
        ]
        result = development_configuration(req, (self.project,))
        workflow = yaml.safe_load(result["configs"][0]["data"])
        # Server ignores input configs completely and pins sandbox
        self.assertEqual(workflow["labels"]["tier"], "sandbox")
        self.assertNotIn("volumes", workflow)
        self.assertNotIn("privileged", workflow)


class PolicyIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = Application(
            id="darkbit",
            repository="JEFF7712/darkbit-site",
            woodpecker_repository_id=2,
            default_branch="main",
            validator="static-site-v1",
            validation_image="registry.rupan.dev/upstream/docker.io/library/python@sha256:c6ead215bfd31f1e433d968853b7a769989117115b728874824e6c0a27cb96fc",
            release_base_image="registry.rupan.dev/upstream/docker.io/nginxinc/nginx-unprivileged@sha256:0c79d56aee561a1d81c63f00eee5fb5fe29279560cdc55e91425133104c7fbe6",
            artifact_repository="apps/darkbit",
            deployment_path="gitops/websites/darkbit",
            state="stateless",
            release_kind="static",
            release_files=[],
            release_supply=[],
            release_scripts=[],
            release_python="",
            release_uid=0,
            release_gid=0,
            release_user="",
            release_entrypoint=[],
            release_env={},
            release_workdir="",
            node_manager="",
            node_dir="",
            node_build=[],
            node_outputs=[],
            node_seeds=[],
            node_runtime="",
        )
        self.dev_project = DevelopmentProject(
            name="autocompress",
            repository="JEFF7712/autocompress",
            woodpecker_repository_id=100,
            default_branch="main",
            kind="python-uv-v1",
            tier="sandbox",
            visibility="public",
            state="migrate",
        )

    def test_homelab_routes_to_homelab_pipeline(self) -> None:
        req = {
            "repo": {"id": 1, "owner": "JEFF7712", "name": "homelab"},
            "pipeline": {
                "event": "push",
                "ref": "refs/heads/feature",
                "commit": "1" * 40,
                "number": 1,
            },
        }
        res = resolve_configuration(req, {}, (self.app,), (self.dev_project,))
        self.assertEqual(res["configs"][0]["name"], "validation-v2.yaml")

    def test_production_app_routes_to_production_pipeline(self) -> None:
        req = {
            "repo": {"id": 2, "owner": "JEFF7712", "name": "darkbit-site"},
            "pipeline": {
                "event": "push",
                "ref": "refs/heads/feature",
                "commit": "2" * 40,
                "number": 1,
            },
        }
        res = resolve_configuration(req, {}, (self.app,), (self.dev_project,))
        self.assertEqual(res["configs"][0]["name"], "application-validation.yaml")

    def test_development_project_routes_to_development_pipeline(self) -> None:
        req = {
            "repo": {"id": 100, "owner": "JEFF7712", "name": "autocompress"},
            "pipeline": {
                "event": "push",
                "ref": "refs/heads/main",
                "commit": "3" * 40,
                "number": 1,
            },
        }
        res = resolve_configuration(req, {}, (self.app,), (self.dev_project,))
        self.assertEqual(res["configs"][0]["name"], "development-validation.yaml")

    def test_unknown_repo_in_resolve_configuration_fails_closed(self) -> None:
        req = {
            "repo": {"id": 999, "owner": "JEFF7712", "name": "unknown-repo"},
            "pipeline": {
                "event": "push",
                "ref": "refs/heads/main",
                "commit": "4" * 40,
                "number": 1,
            },
        }
        with self.assertRaisesRegex(ValueError, "Development project is not enrolled"):
            resolve_configuration(req, {}, (self.app,), (self.dev_project,))


if __name__ == "__main__":
    unittest.main()
