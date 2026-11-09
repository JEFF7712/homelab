import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from scripts.ci.orchestrator_release import (
    IMAGES,
    INVENTORY,
    LOCK,
    PINS,
    promotion_patch,
)
from scripts.registry.core import (
    RESERVED_FIRST_PARTY,
    render_access_control,
    validate_lock,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent


def receipt(role: str, repository: str, number: int) -> dict:
    digest = "sha256:" + f"{number:x}" * 64
    digest = digest[: 7 + 64]
    return {
        "role": role,
        "repository": repository,
        "tag": f"ci-{100 + number}-{'a' * 12}",
        "digest": digest,
        "media_type": "application/vnd.oci.image.manifest.v1+json",
        "retention_tag": "retention-candidate-" + digest[7:23],
    }


def receipts(number: int) -> dict:
    return {
        image.role: receipt(image.role, image.repository, number + index)
        for index, image in enumerate(IMAGES)
    }


class PromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = pathlib.Path(directory.name)
        for name in (LOCK, INVENTORY):
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / name, self.root / name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)

    def apply(self, patch: str) -> None:
        subprocess.run(
            ["git", "apply", "--unidiff-zero", "-"],
            cwd=self.root,
            input=patch.encode(),
            check=True,
        )

    def lock(self) -> dict:
        return json.loads((self.root / LOCK).read_text())

    def test_first_publication_pins_and_records_both_images(self) -> None:
        published = receipts(1)
        self.apply(promotion_patch(self.root, published))
        pins = json.loads((self.root / PINS).read_text())
        self.assertEqual(
            pins,
            {
                role: f"registry.rupan.dev/{r['repository']}@{r['digest']}"
                for role, r in published.items()
            },
        )
        lock = self.lock()
        self.assertEqual(validate_lock(lock), [])
        records = {
            r["destination_repository"]: r
            for r in lock["images"]
            if r["destination_repository"] in RESERVED_FIRST_PARTY
        }
        self.assertEqual(set(records), set(RESERVED_FIRST_PARTY))
        pins_lines = (self.root / PINS).read_text().splitlines()
        for record in records.values():
            path, line = record["consumers"][0].rsplit(":", 1)
            self.assertEqual(path, PINS)
            self.assertIn(record["digest"], pins_lines[int(line) - 1])
            self.assertEqual(record["kind"], "first-party")
            self.assertEqual(
                record["producer"]["location"], "flake/packages/orchestrator-images.nix"
            )
        inventory = json.loads((self.root / INVENTORY).read_text())
        self.assertEqual(
            {r["id"] for r in inventory["images"]}
            >= {r["id"] for r in records.values()},
            True,
        )

    def test_republishing_updates_records_in_place_and_leaves_others(self) -> None:
        before = json.loads((ROOT / LOCK).read_text())
        self.apply(promotion_patch(self.root, receipts(1)))
        self.apply(promotion_patch(self.root, receipts(5)))
        lock = self.lock()
        self.assertEqual(validate_lock(lock), [])
        self.assertEqual(len(lock["images"]), len(before["images"]) + 2)
        # Every pre-existing record is untouched and in its place.
        self.assertEqual(lock["images"][: len(before["images"])], before["images"])
        digests = {
            r["destination_repository"]: r["digest"]
            for r in lock["images"]
            if r["destination_repository"] in RESERVED_FIRST_PARTY
        }
        self.assertEqual(
            set(digests.values()), {r["digest"] for r in receipts(5).values()}
        )

    def test_incomplete_or_unverified_receipts_are_refused(self) -> None:
        published = receipts(1)
        for broken in (
            {"controller": published["controller"]},
            {**published, "worker": {**published["worker"], "retention_tag": "x"}},
            {**published, "worker": {**published["worker"], "tag": "latest"}},
        ):
            with self.assertRaises(ValueError):
                promotion_patch(self.root, broken)


class AccessTests(unittest.TestCase):
    def test_reserved_repositories_grant_only_their_own_publisher(self) -> None:
        lock = json.loads((ROOT / LOCK).read_text())
        policy = render_access_control(lock)
        for repository in RESERVED_FIRST_PARTY:
            project = repository.removeprefix("apps/")
            self.assertEqual(
                policy["repositories"][repository],
                {
                    "policies": [
                        {"users": ["node"], "actions": ["read"]},
                        {
                            "users": [f"publisher-{project}"],
                            "actions": ["read", "create", "update"],
                        },
                    ],
                    "defaultPolicy": [],
                },
            )
        # The release publishes exactly the reserved repositories, each with
        # the publisher the policy grants.
        self.assertEqual(
            {image.repository for image in IMAGES}, set(RESERVED_FIRST_PARTY)
        )
        for image in IMAGES:
            self.assertEqual(
                image.identity, "publisher-" + image.repository.removeprefix("apps/")
            )

    def test_the_operation_uses_only_the_scoped_publisher_secrets(self) -> None:
        operations = json.loads((ROOT / "config/ci/operations.json").read_text())
        spec = operations["orchestrator-publish"]
        registry = {
            name
            for name, value in spec["environment"].items()
            if name.startswith("REGISTRY") or "registry_" in json.dumps(value)
        }
        # The only registry credentials are the two scoped publishers.
        self.assertEqual(registry, {image.auth_variable for image in IMAGES})
        for image in IMAGES:
            name = "registry_" + image.repository.removeprefix("apps/").replace(
                "-", "_"
            )
            self.assertEqual(
                spec["environment"][image.auth_variable],
                {"from_secret": name + "_publisher_auth_file"},
            )
        routes = json.loads((ROOT / "config/ci/ship-routes.json").read_text())
        [route] = [r for r in routes["routes"] if r["name"] == "orchestrator-images"]
        self.assertEqual(route["operations"], ["orchestrator-publish"])


if __name__ == "__main__":
    unittest.main()
