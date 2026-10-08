from __future__ import annotations

import base64
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.ci.applications import Application, configuration, load_catalog
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
            "pipeline": {"event": "push", "ref": "refs/heads/main", "commit": "a" * 40},
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
