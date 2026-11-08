from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.registry.ordering import gitops_files, imported, premature, references

ROOT = Path(__file__).resolve().parents[1]
OLD = "sha256:" + "a" * 64
NEW = "sha256:" + "b" * 64


def lock(*entries: tuple[str, str]) -> dict:
    return {
        "images": [
            {"destination_repository": repository, "digest": digest}
            for repository, digest in entries
        ]
    }


def manifest(reference: str) -> tuple[str, str]:
    return (
        "gitops/home-assistant/deployment.yaml",
        f"spec:\n  containers:\n    - image: {reference}  # yamllint disable-line\n",
    )


HA = "registry.rupan.dev/upstream/ghcr.io/home-assistant/home-assistant"


class ImportOrderingTest(unittest.TestCase):
    """A manifest may only reference upstream digests main already imports."""

    def test_reference_to_an_imported_digest_passes(self) -> None:
        base = lock(("upstream/ghcr.io/home-assistant/home-assistant", OLD))
        self.assertEqual(premature(base, [manifest(f"{HA}@{OLD}")]), {})

    def test_digest_added_in_the_same_change_is_refused(self) -> None:
        # Regression (2026-10-27): the lock and the manifest moved together,
        # the import was cancelled and Home Assistant could not pull.
        base = lock(("upstream/ghcr.io/home-assistant/home-assistant", OLD))
        found = premature(base, [manifest(f"{HA}:2026.10.4@{NEW}")])
        self.assertEqual(
            found,
            {
                ("upstream/ghcr.io/home-assistant/home-assistant", NEW): [
                    "gitops/home-assistant/deployment.yaml:3"
                ]
            },
        )

    def test_same_digest_in_another_repository_does_not_count(self) -> None:
        base = lock(("upstream/docker.io/library/postgres", NEW))
        self.assertTrue(premature(base, [manifest(f"{HA}@{NEW}")]))

    def test_first_party_images_are_not_this_check(self) -> None:
        reference = f"registry.rupan.dev/apps/bus-display:0.0.5@{NEW}"
        self.assertEqual(references([manifest(reference)]), {})
        self.assertEqual(imported(lock(("apps/bus-display", NEW))), set())

    def test_current_tree_references_only_imported_digests(self) -> None:
        current = json.loads((ROOT / "registry/images.lock.json").read_text())
        files = list(gitops_files(ROOT))
        self.assertTrue(references(files), "no upstream references were parsed")
        self.assertEqual(premature(current, files), {})


if __name__ == "__main__":
    unittest.main()
