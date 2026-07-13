from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.validate_argocd_apps import validate_repository


LOCAL_REPO = "https://gitlab.com/JEFF7712/homelab.git"


class DeploymentInventoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name)
        for directory in ("apps", "bootstrap", "infrastructure", "secrets"):
            (self.repo / directory).mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_documents(self, relative_path: str, *documents: dict[str, object]) -> None:
        path = self.repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump_all(documents, sort_keys=False), encoding="utf-8")

    def application(self, name: str, source_path: str) -> dict[str, object]:
        return {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "Application",
            "metadata": {"name": name, "namespace": "argocd"},
            "spec": {
                "source": {"repoURL": LOCAL_REPO, "path": source_path},
                "destination": {"server": "https://kubernetes.default.svc", "namespace": name},
            },
        }

    def write_valid_graph(self) -> None:
        self.write_documents("bootstrap/root-app.yaml", self.application("root-app", "apps"))
        self.write_documents("apps/example.yaml", self.application("example", "infrastructure/example"))
        self.write_documents(
            "infrastructure/example/deployment.yaml",
            {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "example"}},
        )

    def test_accepts_a_reachable_deployment_graph(self) -> None:
        self.write_valid_graph()

        result = validate_repository(self.repo)

        self.assertEqual([], result.errors)
        self.assertEqual(2, result.applications)

    def test_rejects_duplicate_application_names(self) -> None:
        self.write_valid_graph()
        self.write_documents("apps/duplicate.yaml", self.application("example", "infrastructure/example"))

        result = validate_repository(self.repo)

        self.assertTrue(any("duplicate Application name 'example'" in error for error in result.errors))

    def test_rejects_an_unreferenced_infrastructure_directory(self) -> None:
        self.write_valid_graph()
        self.write_documents(
            "infrastructure/orphan/deployment.yaml",
            {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "orphan"}},
        )

        result = validate_repository(self.repo)

        self.assertIn("infrastructure/orphan: no reachable local Application references this directory", result.errors)

    def test_allows_an_explicitly_excluded_draft_directory(self) -> None:
        self.write_valid_graph()
        self.write_documents(
            "infrastructure/autocompress/deployment.yaml",
            {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "draft"}},
        )

        result = validate_repository(self.repo)

        self.assertEqual([], result.errors)

    def test_rejects_unexpected_resource_kinds_in_apps(self) -> None:
        self.write_valid_graph()
        self.write_documents(
            "apps/workload.yaml",
            {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "misplaced"}},
        )

        result = validate_repository(self.repo)

        self.assertTrue(any("apps/workload.yaml" in error and "unexpected kind Deployment" in error for error in result.errors))

    def test_rejects_unsafe_local_source_paths(self) -> None:
        self.write_documents("bootstrap/root-app.yaml", self.application("root-app", "apps"))
        self.write_documents("apps/unsafe.yaml", self.application("unsafe", "../outside"))

        result = validate_repository(self.repo)

        self.assertTrue(any("unsafe local source path '../outside'" in error for error in result.errors))

    def test_scans_applications_in_a_referenced_secrets_directory(self) -> None:
        self.write_valid_graph()
        self.write_documents("apps/secrets.yaml", self.application("secrets", "secrets"))
        nested = self.application("nested", "infrastructure/example")
        nested["spec"]["source"] = {"repoURL": "https://example.com/charts"}
        self.write_documents("secrets/nested.yaml", nested)

        result = validate_repository(self.repo)

        self.assertEqual([], result.errors)
        self.assertEqual(4, result.applications)

    def test_follows_nested_applications_when_directory_recursion_is_enabled(self) -> None:
        root = self.application("root-app", "apps")
        root["spec"]["source"]["directory"] = {"recurse": True}
        self.write_documents("bootstrap/root-app.yaml", root)
        nested = self.application("nested", "infrastructure/example")
        nested["spec"]["source"] = {"repoURL": "https://example.com/charts"}
        self.write_documents("apps/nested/application.yaml", nested)

        result = validate_repository(self.repo)

        self.assertEqual([], result.errors)
        self.assertEqual(2, result.applications)

    def test_rejects_a_source_symlink_that_escapes_the_repository(self) -> None:
        self.write_documents("bootstrap/root-app.yaml", self.application("root-app", "apps"))
        with tempfile.TemporaryDirectory() as outside:
            (self.repo / "apps" / "outside").symlink_to(outside)
            self.write_documents("apps/unsafe.yaml", self.application("unsafe", "apps/outside"))

            result = validate_repository(self.repo)

        self.assertTrue(any("resolves outside the repository" in error for error in result.errors))

    def test_rejects_missing_and_non_directory_sources(self) -> None:
        self.write_documents("bootstrap/root-app.yaml", self.application("root-app", "apps"))
        self.write_documents("apps/missing.yaml", self.application("missing", "infrastructure/missing"))
        (self.repo / "infrastructure" / "file").write_text("not a directory", encoding="utf-8")
        self.write_documents("apps/file.yaml", self.application("file", "infrastructure/file"))

        result = validate_repository(self.repo)

        self.assertTrue(any("missing local source directory 'infrastructure/missing'" in error for error in result.errors))
        self.assertTrue(any("local source path is not a directory 'infrastructure/file'" in error for error in result.errors))

    def test_rejects_an_application_outside_the_argocd_namespace(self) -> None:
        root = self.application("root-app", "apps")
        root["metadata"]["namespace"] = "default"
        self.write_documents("bootstrap/root-app.yaml", root)

        result = validate_repository(self.repo)

        self.assertTrue(any("metadata namespace must be 'argocd'" in error for error in result.errors))

    def test_skips_raw_templates_inside_a_referenced_helm_chart(self) -> None:
        self.write_documents("bootstrap/root-app.yaml", self.application("root-app", "apps"))
        self.write_documents("apps/chart.yaml", self.application("chart", "infrastructure/chart"))
        self.write_documents(
            "infrastructure/chart/Chart.yaml",
            {"apiVersion": "v2", "name": "chart", "version": "0.1.0"},
        )
        template = self.repo / "infrastructure/chart/templates/workload.yaml"
        template.parent.mkdir()
        template.write_text("{{- range .Values.workloads }}\nkind: Deployment\n{{- end }}\n", encoding="utf-8")

        result = validate_repository(self.repo)

        self.assertEqual([], result.errors)
        self.assertEqual(2, result.applications)

    def test_traverses_every_local_path_in_a_multi_source_application(self) -> None:
        self.write_documents("bootstrap/root-app.yaml", self.application("root-app", "apps"))
        application = self.application("media", "infrastructure/raw")
        application["spec"].pop("source")
        application["spec"]["sources"] = [
            {"repoURL": LOCAL_REPO, "path": "infrastructure/raw"},
            {"repoURL": LOCAL_REPO, "path": "infrastructure/chart"},
        ]
        self.write_documents("apps/media.yaml", application)
        self.write_documents(
            "infrastructure/raw/deployment.yaml",
            {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "raw"}},
        )
        self.write_documents(
            "infrastructure/chart/Chart.yaml",
            {"apiVersion": "v2", "name": "chart", "version": "0.1.0"},
        )

        result = validate_repository(self.repo)

        self.assertEqual([], result.errors)


if __name__ == "__main__":
    unittest.main()
