from __future__ import annotations

import json
import unittest

from scripts.verifier.freshness import (
    narrowed,
    overlap,
    record_scopes,
    scopes,
)


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


def record(repository: str, digest: str, *consumers: str) -> dict[str, object]:
    return {
        "id": f"{repository.replace('/', '-')}-{digest}",
        "destination_repository": repository,
        "digest": f"sha256:{digest}",
        "consumers": list(consumers),
    }


def lock(*records: dict[str, object], **fields: object) -> str:
    return json.dumps(
        {
            "kind": "registry-image-lock",
            "generated_at": "then",
            "images": list(records),
            **fields,
        }
    )


MATRIX = record("apps/matrix", "aa", "gitops/led-matrix/deployment.yaml:20")
BUS = record("apps/bus", "bb", "gitops/websites/bus/deployment.yaml:23")
BASE = lock(MATRIX, BUS)
LOCK = "registry/images.lock.json"


class RegistryRecordScopesTest(unittest.TestCase):
    def promote(self, old: dict[str, object], digest: str) -> dict[str, object]:
        consumers = old["consumers"]
        assert isinstance(consumers, list)
        return record(str(old["destination_repository"]), digest, *consumers)

    def scoped(self, after: str) -> dict[str, frozenset[str]]:
        found = record_scopes(BASE, after)
        assert found is not None
        return {LOCK: found}

    def test_a_promotion_is_scoped_to_its_image_and_consumers(self) -> None:
        after = lock(self.promote(MATRIX, "cc"), BUS, generated_at="now")
        self.assertEqual(
            record_scopes(BASE, after),
            {"image:apps/matrix", "gitops/led-matrix"},
        )

    def test_promotions_of_different_images_are_independent(self) -> None:
        branch = self.scoped(lock(self.promote(MATRIX, "cc"), BUS))
        main = self.scoped(lock(MATRIX, self.promote(BUS, "dd")))
        self.assertEqual(overlap([LOCK], [LOCK], branch, main), [])

    def test_promotions_of_the_same_image_overlap(self) -> None:
        branch = self.scoped(lock(self.promote(MATRIX, "cc"), BUS))
        main = self.scoped(lock(self.promote(MATRIX, "dd"), BUS))
        self.assertIn("both change apps/matrix", overlap([LOCK], [LOCK], branch, main))

    def test_a_promotion_overlaps_its_consumer_and_the_registry_tooling(self) -> None:
        main = self.scoped(lock(self.promote(MATRIX, "cc"), BUS))
        self.assertTrue(
            overlap(["gitops/led-matrix/deployment.yaml"], [LOCK], None, main)
        )
        self.assertTrue(overlap(["scripts/registry/core.py"], [LOCK], None, main))
        self.assertEqual(overlap(["gitops/media/app.yaml"], [LOCK], None, main), [])

    def test_observed_cluster_consumers_add_no_repository_scope(self) -> None:
        notes = record("apps/notes", "ff", "observed:obsidian/notes-0/notes")
        self.assertEqual(
            record_scopes(lock(MATRIX, BUS), lock(MATRIX, BUS, notes)),
            {"image:apps/notes"},
        )

    def test_other_record_changes_stay_global(self) -> None:
        for before, after in [
            (BASE, lock(MATRIX, BUS, destination_registry="elsewhere")),
            (BASE, lock(MATRIX, {"id": "no-repository"})),
            (BASE, lock(MATRIX, record("apps/ci", "ee", ".gitlab-ci.yml:*"))),
            (BASE, "{not json"),
            (None, BASE),
            (json.dumps({"images": {}}), json.dumps({"images": {"x": 1}})),
        ]:
            with self.subTest(after=after):
                self.assertIsNone(record_scopes(before, after))

    def test_only_record_files_are_narrowed(self) -> None:
        reads = {LOCK: (BASE, lock(self.promote(MATRIX, "cc"), BUS))}
        found = narrowed(
            [LOCK, "registry/observed-images.json", "gitops/led-matrix/a.yaml"],
            lambda path: reads.get(path, ("", ""))[0],
            lambda path: reads.get(path, ("", ""))[1],
        )
        self.assertEqual(set(found), {LOCK})
        self.assertTrue(
            overlap(["registry/observed-images.json"], [LOCK], None, found),
            "unnarrowed registry files still affect every check",
        )


if __name__ == "__main__":
    unittest.main()
