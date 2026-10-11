from __future__ import annotations

import base64
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.ci.applications import Application, configuration, load_catalog
from scripts.ci.node_service import validate as validate_node
from scripts.ci.policy import resolve_configuration
from scripts.ci.static_site import validate

ROOT = Path(__file__).resolve().parents[1]


class CatalogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "applications.json"
        self.data = json.loads((ROOT / "config/ci/applications.json").read_text())

    def load(self) -> tuple[Application, ...]:
        self.path.write_text(json.dumps(self.data))
        return load_catalog(self.path)

    def test_actual_catalog_has_explicit_enrollment_and_locked_image(self) -> None:
        app = self.load()[0]
        self.assertEqual(app.woodpecker_repository_id, 2)
        lock = json.loads((ROOT / "registry/images.lock.json").read_text())
        self.assertTrue(
            any(
                app.validation_image
                == f"{lock['destination_registry']}/{record['destination_repository']}@{record['digest']}"
                for record in lock["images"]
            )
        )

    def test_invalid_catalog_contracts_fail_closed(self) -> None:
        original = copy.deepcopy(self.data)
        for key, value in [
            ("woodpecker_repository_id", True),
            ("woodpecker_repository_id", 1),
            ("woodpecker_repository_id", "2"),
            ("validator", "arbitrary-command"),
            ("validation_image", "python:latest"),
            ("artifact_repository", "apps/another-site"),
            ("deployment_path", "gitops/../secrets"),
            ("repository", "JEFF7712/homelab"),
            ("default_branch", "main; command"),
            ("state", "database"),
            ("commands", ["arbitrary command"]),
        ]:
            with self.subTest(key=key, value=value):
                self.data = copy.deepcopy(original)
                self.data["applications"][0][key] = value
                with self.assertRaises(ValueError):
                    self.load()

    def test_duplicate_identity_and_enrollment_fail_closed(self) -> None:
        self.data["applications"].append(copy.deepcopy(self.data["applications"][0]))
        with self.assertRaises(ValueError):
            self.load()
        self.data["applications"][0]["woodpecker_repository_id"] = 2
        self.data["applications"][1].update(
            id="other",
            repository="JEFF7712/other",
            woodpecker_repository_id=2,
            artifact_repository="apps/other",
            deployment_path="gitops/websites/other",
        )
        with self.assertRaises(ValueError):
            self.load()


class ApplicationPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = replace(
            load_catalog(ROOT / "config/ci/applications.json")[0],
            woodpecker_repository_id=2,
        )
        self.request = {
            "repo": {"id": 2, "owner": "JEFF7712", "name": "darkbit-site"},
            "pipeline": {
                "event": "push",
                "ref": "refs/heads/main",
                "commit": "a" * 40,
                "number": 1,
            },
        }

    def test_validation_routes_without_homelab_api_or_secrets(self) -> None:
        for event in ("push", "pull_request", "manual"):
            with self.subTest(event=event), patch("scripts.ci.policy.api") as api:
                self.request["pipeline"].update(event=event, from_fork=True)
                self.request["configs"] = [
                    {"commands": ["steal-credentials"], "privileged": True}
                ]
                result = resolve_configuration(
                    self.request, {"deploy-fleet": {}}, (self.app,)
                )
                api.assert_not_called()
                workflow = yaml.safe_load(result["configs"][0]["data"])
                self.assertEqual(
                    workflow["labels"], {"tier": "sandbox", "type": "docker"}
                )
                self.assertEqual(
                    result["configs"][0]["name"], "application-validation.yaml"
                )
                self.assertEqual(set(workflow), {"when", "labels", "steps"})
                step = workflow["steps"][0]
                self.assertEqual(set(step), {"name", "image", "commands"})
                self.assertNotIn("steal-credentials", json.dumps(workflow))
                self.assertIn("python -I", step["commands"][0])

    def test_exact_enrollment_is_required(self) -> None:
        for field, value in (
            ("id", 3),
            ("id", True),
            ("owner", "foreign"),
            ("name", "homelab"),
        ):
            request = copy.deepcopy(self.request)
            request["repo"][field] = value
            with self.subTest(field=field), patch("scripts.ci.policy.api") as api:
                with self.assertRaisesRegex(ValueError, "not enrolled"):
                    resolve_configuration(request, {}, (self.app,))
                api.assert_not_called()
        with self.assertRaisesRegex(ValueError, "not enrolled"):
            configuration(
                self.request, (replace(self.app, woodpecker_repository_id=None),)
            )

    def test_only_main_push_gets_separated_release_permissions(self) -> None:
        workflows = configuration(self.request, (self.app,))["configs"]
        self.assertEqual(len(workflows), 2)
        release = yaml.safe_load(workflows[1]["data"])
        self.assertEqual(release["depends_on"], ["application-validation"])
        prepare, assemble, publish = release["steps"]
        self.assertEqual(
            prepare["environment"]["REGISTRY_PASSWORD"]["from_secret"],
            "registry_read_password",
        )
        self.assertNotIn("environment", assemble)
        self.assertEqual(
            publish["environment"]["REGISTRY_PASSWORD"]["from_secret"],
            "darkbit_registry_password",
        )
        for update in [
            {"event": "pull_request"},
            {"event": "manual"},
            {"ref": "refs/heads/feature"},
            {"from_fork": True},
        ]:
            r = copy.deepcopy(self.request)
            r["pipeline"].update(update)
            self.assertEqual(len(configuration(r, (self.app,))["configs"]), 1)

    def test_release_source_and_number_fail_closed(self) -> None:
        for update in [
            {"commit": "a; command"},
            {"number": True},
            {"number": 0},
            {"number": 1000000},
        ]:
            r = copy.deepcopy(self.request)
            r["pipeline"].update(update)
            with self.assertRaises(ValueError):
                configuration(r, (self.app,))

    def test_application_cannot_enter_production_event_or_override(self) -> None:
        for change in (
            {"event": "deployment"},
            {"event": "cron"},
            {"event": "tag"},
            {"event": "release"},
            {"deploy_to": "deploy-fleet"},
            {"deploy": True},
            {"variables": {"CI_OPERATION": "deploy-fleet"}},
        ):
            request = copy.deepcopy(self.request)
            request["pipeline"].update(change)
            with self.subTest(change=change), patch("scripts.ci.policy.api") as api:
                with self.assertRaises(ValueError):
                    resolve_configuration(request, {"deploy-fleet": {}}, (self.app,))
                api.assert_not_called()

    def test_lifecycle_events_do_not_overwrite_required_status(self) -> None:
        self.request["pipeline"]["event"] = "pull_request_closed"
        result = configuration(self.request, (self.app,))
        self.assertEqual(result["configs"][0]["name"], "application-lifecycle.yaml")
        self.assertEqual(
            yaml.safe_load(result["configs"][0]["data"])["steps"][0]["commands"],
            ["true"],
        )

    def test_embedded_validator_runs_without_importing_checkout_modules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text('<link rel="stylesheet" href="style.css">')
            (root / "style.css").write_text("body { color: black; }")
            (root / "base64.py").write_text(
                "raise RuntimeError('repository module executed')"
            )
            result = configuration(self.request, (self.app,))
            command = yaml.safe_load(result["configs"][0]["data"])["steps"][0][
                "commands"
            ][0]
            encoded = command.split("b64decode('", 1)[1].split("')", 1)[0]
            validator = base64.b64decode(encoded).decode()
            process = subprocess.run(
                [sys.executable, "-I", "-c", validator],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            (root / "style.css").unlink()
            process = subprocess.run(
                [sys.executable, "-I", "-c", validator],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(process.returncode, 0)

    def test_homelab_routing_retains_its_required_validation(self) -> None:
        self.request["repo"].update(id=1, name="homelab")
        self.request["pipeline"]["ref"] = "refs/heads/feature"
        result = resolve_configuration(self.request, {}, (self.app,))
        self.assertEqual(result["configs"][0]["name"], "validation-v2.yaml")


class StaticSiteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "style.css").write_text("body { color: black; }")
        (self.root / "index.html").write_text(
            '<link rel="stylesheet" href="style.css?version=1"><a href="https://shop.example">Shop</a>'
        )

    def test_local_assets_and_external_navigation_pass(self) -> None:
        validate(self.root)

    def test_external_missing_and_escaping_assets_fail(self) -> None:
        for reference in (
            "https://fonts.example/font.css",
            "//cdn.example/image.png",
            "../outside.png",
            "%2e%2e/outside.png",
            "missing.png",
        ):
            with self.subTest(reference=reference):
                (self.root / "index.html").write_text(f'<img src="{reference}">')
                with self.assertRaises(ValueError):
                    validate(self.root)

    def test_symlink_assets_and_html_base_are_rejected(self) -> None:
        (self.root / "image.png").write_bytes(b"fixture")
        (self.root / "linked.png").symlink_to("image.png")
        for html in (
            '<img src="linked.png">',
            '<base href="https://foreign.example">',
            '<img srcset="image.png 1x">',
        ):
            with self.subTest(html=html):
                (self.root / "index.html").write_text(html)
                with self.assertRaises(ValueError):
                    validate(self.root)

    def test_css_and_inline_assets_are_checked(self) -> None:
        for css in (
            '@import "https://fonts.example/font.css";',
            "body { background: url(//cdn.example/image.png); }",
        ):
            with self.subTest(css=css):
                (self.root / "style.css").write_text(css)
                with self.assertRaises(ValueError):
                    validate(self.root)
        (self.root / "style.css").write_text("body { color: black; }")
        (self.root / "index.html").write_text(
            "<style>body { background: url(missing.png); }</style>"
        )
        with self.assertRaises(ValueError):
            validate(self.root)


class NodeServiceTest(unittest.TestCase):
    BASE = (
        "registry.rupan.dev/upstream/docker.io/library/node@sha256:"
        + "ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1"
    )

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "Dockerfile").write_text(f"FROM {self.BASE} AS runner\n")
        (self.root / "package.json").write_text('{"name": "fixture"}\n')
        (self.root / "package-lock.json").write_text('{"lockfileVersion": 3}\n')

    def test_compliant_source_passes(self) -> None:
        validate_node(self.root)

    def test_mirrored_prefix_bypass_is_rejected(self) -> None:
        for base in (
            "docker.io/registry.rupan.dev/upstream/library/node@sha256:" + "e" * 64,
            "registry.rupan.dev.evil.example/upstream/library/node@sha256:" + "e" * 64,
            "registry.rupan.dev/upstream/library/node:24-alpine",
            "registry.rupan.dev/upstream/library/node@sha256:xyz",
            "library/node@sha256:" + "e" * 64,
        ):
            with self.subTest(base=base):
                (self.root / "Dockerfile").write_text(f"FROM {base}\n")
                with self.assertRaises(ValueError):
                    validate_node(self.root)

    def test_hidden_supply_and_remote_installers_are_rejected(self) -> None:
        cases = [
            f"FROM {self.BASE}\nADD https://example.invalid/tool.tgz /tmp/tool.tgz\n",
            f"FROM {self.BASE} AS base\nCOPY --from=docker.io/library/node@sha256:{'e' * 64} /x /y\n",
            f"FROM {self.BASE}\nRUN --mount=type=gha,target=/root/.npm npm ci\n",
            f"FROM {self.BASE}\nENV npm_config_registry=https://registry.npmjs.org\n",
        ]
        for body in cases:
            with self.subTest(body=body.splitlines()[-1]):
                (self.root / "Dockerfile").write_text(body)
                with self.assertRaises(ValueError):
                    validate_node(self.root)

    def test_missing_source_contract_fails(self) -> None:
        (self.root / "Dockerfile").unlink()
        with self.assertRaises(ValueError):
            validate_node(self.root)
        (self.root / "Dockerfile").write_text(f"FROM {self.BASE}\n")
        (self.root / "package-lock.json").unlink()
        with self.assertRaises(ValueError):
            validate_node(self.root)

    def test_competing_delivery_authority_is_rejected(self) -> None:
        workflows = self.root / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text("name: CI\non: [push]\n")
        validate_node(self.root)
        for name, body in (
            ("publish.yml", "run: crane push registry.rupan.dev/apps/fixture\n"),
            ("cache.yml", "cache-to: type=gha,mode=max\n"),
        ):
            with self.subTest(name=name):
                (workflows / name).write_text(body)
                with self.assertRaises(ValueError):
                    validate_node(self.root)
                (workflows / name).unlink()
        compose = self.root / "docker-compose.yml"
        compose.write_text("services:\n  app:\n    image: ghcr.io/jeff7712/fixture\n")
        with self.assertRaises(ValueError):
            validate_node(self.root)

    def test_embedded_node_validator_runs_without_checkout_imports(self) -> None:
        app = replace(
            load_catalog(ROOT / "config/ci/applications.json")[0],
            id="node-fixture",
            repository="JEFF7712/node-fixture",
            woodpecker_repository_id=999,
            validator="node-service-v1",
            artifact_repository="apps/node-fixture",
            deployment_path="gitops/websites/node-fixture",
        )
        request = {
            "repo": {"id": 999, "owner": "JEFF7712", "name": "node-fixture"},
            "pipeline": {
                "event": "push",
                "ref": "refs/heads/main",
                "commit": "b" * 40,
                "number": 1,
            },
        }
        result = configuration(request, (app,))
        command = yaml.safe_load(result["configs"][0]["data"])["steps"][0]["commands"][
            0
        ]
        encoded = command.split("b64decode('", 1)[1].split("')", 1)[0]
        validator = base64.b64decode(encoded).decode()
        self.assertNotIn("scripts.ci.dockerfile_supply", validator)
        (self.root / "scripts").mkdir()
        (self.root / "scripts" / "ci").mkdir()
        (self.root / "scripts" / "ci" / "dockerfile_supply.py").write_text(
            "raise RuntimeError('repository module executed')"
        )
        process = subprocess.run(
            [sys.executable, "-I", "-c", validator],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        (self.root / "package-lock.json").unlink()
        process = subprocess.run(
            [sys.executable, "-I", "-c", validator],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(process.returncode, 0)


class NodeReleaseTest(unittest.TestCase):
    NODE_BASE = (
        "registry.rupan.dev/upstream/docker.io/library/node@sha256:"
        + "ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1"
    )
    NGINX_BASE = (
        "registry.rupan.dev/upstream/docker.io/library/nginx@sha256:"
        + "5616878291a2eed594aee8db4dade5878cf7edcb475e59193904b198d9b830de"
    )

    def supply(self) -> list[dict[str, object]]:
        return [
            {
                "package": "fixture-supply",
                "version": "1",
                "filename": "node.tar.gz",
                "sha256": "sha256:" + "a" * 64,
                "kind": "file",
                "dest": "/inputs/node.tar.gz",
            },
            {
                "package": "fixture-supply",
                "version": "1",
                "filename": "node-musl.tar.gz",
                "sha256": "sha256:" + "c" * 64,
                "kind": "file",
                "dest": "/inputs/node-musl.tar.gz",
            },
            {
                "package": "fixture-supply",
                "version": "1",
                "filename": "node-libs.tar.gz",
                "sha256": "sha256:" + "d" * 64,
                "kind": "file",
                "dest": "/inputs/node-libs.tar.gz",
            },
            {
                "package": "fixture-supply",
                "version": "1",
                "filename": "npm-cache.tar.gz",
                "sha256": "sha256:" + "b" * 64,
                "kind": "file",
                "dest": "/inputs/npm-cache.tar.gz",
            },
        ]

    def node_app(self) -> Application:
        base = load_catalog(ROOT / "config/ci/applications.json")
        template = next(a for a in base if a.id == "pulse-site")
        return replace(
            template,
            release_kind="node",
            release_base_image=self.NODE_BASE,
            release_files=[],
            release_supply=self.supply(),
            release_uid=1001,
            release_gid=1001,
            release_user="1001:1001",
            release_entrypoint=["node", "server.js"],
            release_env={"NODE_ENV": "production"},
            release_workdir="/app",
            node_manager="npm",
            node_dir="",
            node_build=["npm", "run", "build"],
            node_outputs=[
                [".next/standalone", "/app"],
                [".next/static", "/app/.next/static"],
                ["public", "/app/public"],
            ],
            node_runtime="node",
        )

    def load_with(self, app: Application) -> tuple[Application, ...]:
        data = json.loads((ROOT / "config/ci/applications.json").read_text())
        data["applications"] = [
            {**a, **{}} for a in data["applications"] if a["id"] != app.id
        ]
        import dataclasses

        data["applications"].append(dataclasses.asdict(app))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "applications.json"
            path.write_text(json.dumps(data))
            return load_catalog(path)

    def test_node_release_catalog_validates(self) -> None:
        apps = self.load_with(self.node_app())
        self.assertEqual(apps[-1].release_kind, "node")

    def test_node_release_contracts_fail_closed(self) -> None:
        good = self.node_app()
        cases = [
            ("validator", "python-service-v1"),
            ("node_manager", "bun"),
            ("node_manager", ""),
            ("node_dir", "/absolute"),
            ("node_dir", "../escape"),
            ("node_build", ["npm"]),
            ("node_build", ["yarn", "run", "build"]),
            ("node_build", ["npm", "run", "--registry=https://x.invalid"]),
            ("node_outputs", []),
            ("node_outputs", [["dist", "relative-dest"]]),
            ("node_outputs", [["dist", "/etc/passwd"]]),
            ("node_runtime", "deno"),
            ("node_runtime", ""),
            ("release_python", "3.12"),
            ("release_scripts", [{"name": "x"}]),
            ("release_entrypoint", []),
            ("release_workdir", "relative"),
            ("release_user", "1001:1002"),
            ("release_uid", 0),
            ("release_supply", []),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                bad = replace(good, **{field: value})
                with self.assertRaises(ValueError):
                    self.load_with(bad)

    def test_static_node_release_keeps_base_runners(self) -> None:
        app = replace(
            self.node_app(),
            release_base_image=self.NGINX_BASE,
            release_entrypoint=[],
            release_workdir="",
            release_uid=101,
            release_gid=101,
            release_user="101:101",
            node_build=["npm", "run", "docs:build"],
            node_outputs=[["dist", "/usr/share/nginx/html"]],
            node_runtime="static",
        )
        self.load_with(app)
        with self.assertRaises(ValueError):
            self.load_with(replace(app, release_entrypoint=["nginx"]))
        with self.assertRaises(ValueError):
            self.load_with(replace(app, release_workdir="/"))
        with self.assertRaises(ValueError):
            self.load_with(replace(app, release_base_image=self.NODE_BASE))

    def test_node_fields_rejected_on_other_kinds(self) -> None:
        base = load_catalog(ROOT / "config/ci/applications.json")
        darkbit = next(a for a in base if a.id == "darkbit")
        with self.assertRaises(ValueError):
            self.load_with(replace(darkbit, node_manager="npm"))

    def test_node_seeds_must_reference_retained_supply(self) -> None:
        good = self.node_app()
        plugins = dict(self.supply()[0])
        plugins.update(filename="quartz-plugins.tar.gz", sha256="sha256:" + "c" * 64)
        app = replace(
            good,
            release_supply=[*good.release_supply, plugins],
            node_seeds=[["quartz-plugins.tar.gz", "quartz/.quartz"]],
        )
        self.load_with(app)
        with self.assertRaises(ValueError):
            self.load_with(replace(good, node_seeds=[["missing.tar.gz", "x"]]))
        with self.assertRaises(ValueError):
            self.load_with(replace(good, node_seeds=[["../escape.tar.gz", "x"]]))
        with self.assertRaises(ValueError):
            self.load_with(replace(app, node_seeds=[["quartz-plugins.tar.gz", "../x"]]))

    def test_toolchain_selection_prefers_first_working_binary(self) -> None:
        import io
        import tarfile

        from scripts.ci.node_release import select_toolchain_bin

        def bundle(executable: Path) -> bytes:
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode="w:gz") as archive:
                info = tarfile.TarInfo("toolchain-x/bin/node")
                info.size = executable.stat().st_size
                info.mode = 0o755
                with executable.open("rb") as handle:
                    archive.addfile(info, handle)
            return output.getvalue()

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            broken = workspace / "broken.sh"
            broken.write_text("#!/bin/sh\nexit 127\n")
            broken.chmod(0o755)
            supply = {
                "/inputs/node.tar.gz": bundle(broken),
                "/inputs/node-musl.tar.gz": bundle(Path(sys.executable)),
            }
            selected, label = select_toolchain_bin(workspace, supply)
            self.assertTrue(
                str(selected).endswith("toolchain-musl/toolchain-x/bin/node")
            )
            self.assertEqual(label, "musl")
            fresh = Path(tempfile.mkdtemp())
            self.addCleanup(shutil.rmtree, fresh, True)
            with self.assertRaises(ValueError):
                select_toolchain_bin(fresh, {"/inputs/node.tar.gz": bundle(broken)})

    def test_main_push_gets_node_release_workflow(self) -> None:
        app = self.node_app()
        request = {
            "repo": {"id": 8, "owner": "JEFF7712", "name": "pulse"},
            "pipeline": {
                "event": "push",
                "ref": "refs/heads/main",
                "commit": "c" * 40,
                "number": 7,
            },
        }
        configs = configuration(request, (app,))["configs"]
        self.assertEqual(len(configs), 2)
        release = yaml.safe_load(configs[1]["data"])
        self.assertEqual(release["depends_on"], ["application-validation"])
        prepare, assemble, publish = release["steps"]
        self.assertEqual(
            prepare["environment"]["FORGEJO_TOKEN"]["from_secret"],
            "supply_read_password",
        )
        self.assertEqual(assemble["name"], "assemble-node-image")
        self.assertNotIn("environment", assemble)
        self.assertEqual(
            publish["environment"]["REGISTRY_PASSWORD"]["from_secret"],
            "pulse-site_registry_password",
        )
        command = assemble["commands"][0]
        self.assertIn("--manager npm", command)
        self.assertIn("--runtime node", command)
        pull_request = copy.deepcopy(request)
        pull_request["pipeline"]["event"] = "pull_request"
        self.assertEqual(len(configuration(pull_request, (app,))["configs"]), 1)
