from __future__ import annotations

import unittest

from scripts.verifier.freshness import overlap, scopes


class MergeFreshnessTest(unittest.TestCase):
    def test_different_applications_are_independent(self) -> None:
        self.assertEqual(
            overlap(["gitops/media/app.yaml"], ["gitops/observability/app.yaml"]), []
        )
        self.assertEqual(
            overlap(["home-assistant/core/x.yaml"], ["flake/modules/nas.nix"]), []
        )

    def test_the_same_application_overlaps(self) -> None:
        self.assertEqual(
            overlap(["gitops/media/a.yaml"], ["gitops/media/b.yaml"]),
            ["both change gitops/media"],
        )

    def test_generators_overlap_the_trees_they_render(self) -> None:
        self.assertTrue(
            overlap(["gitops/websites/ism/site.yaml"], ["scripts/websites/render.py"])
        )
        self.assertTrue(
            overlap(
                ["gitops/home-assistant/deployment.yaml"],
                ["home-assistant/core/configuration.yaml"],
            )
        )

    def test_python_packages_share_one_scope(self) -> None:
        self.assertTrue(overlap(["scripts/agent/ship.py"], ["scripts/verifier/x.py"]))

    def test_validation_machinery_and_unknown_paths_overlap_everything(self) -> None:
        for path in (
            "flake/flake.lock",
            "scripts/checks/gitops.sh",
            "scripts/ci/validate.py",
            ".woodpecker/offline-checks.yaml",
            "registry/images.lock.json",
            "tests/helpers.py",
            "justfile",
            "brand-new/file",
        ):
            with self.subTest(path=path):
                self.assertIsNone(scopes(path))
                self.assertTrue(overlap(["gitops/media/a.yaml"], [path]))
                self.assertTrue(overlap([path], ["docs/runbooks/x.md"]))

    def test_documentation_and_tests_only_overlap_the_same_file(self) -> None:
        self.assertEqual(overlap(["docs/a.md"], ["docs/b.md"]), [])
        self.assertEqual(overlap(["tests/test_a.py"], ["tests/test_b.py"]), [])
        self.assertEqual(overlap(["AGENTS.md"], ["AGENT_MAP.md"]), [])
        self.assertEqual(
            overlap(["docs/a.md"], ["docs/a.md"]), ["both change docs/a.md"]
        )

    def test_a_current_head_never_overlaps(self) -> None:
        self.assertEqual(overlap(["flake/flake.lock"], []), [])


if __name__ == "__main__":
    unittest.main()
