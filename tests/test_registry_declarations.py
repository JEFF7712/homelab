import hashlib
import json
import pathlib
import tempfile
import unittest

from scripts.registry.declarations import (
    is_declaration,
    lock_declarations,
    pending_declarations,
    without_conflicting_tags,
)

INDEX = "application/vnd.oci.image.index.v1+json"


def manifest() -> bytes:
    return json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": INDEX,
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": "sha256:" + "a" * 64,
                    "size": 1,
                    "platform": {"architecture": "amd64", "os": "linux"},
                }
            ],
        }
    ).encode()


class FakeClient:
    """Serves one manifest for any reference, like an upstream registry."""

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.requests: list[str] = []

    def raw_manifest(
        self, reference: str, *, destination: bool = False, **_: object
    ) -> bytes:
        self.requests.append(reference)
        return self.body


def upstream(reference: str, consumers: list[str], record_id: str) -> dict:
    name, _, digest = reference.partition("@")
    image, _, tag = name.rpartition(":")
    registry, _, repository = image.partition("/")
    return {
        "consumers": consumers,
        "destination_repository": f"upstream/{image}",
        "id": record_id,
        "kind": "upstream",
        "producer": None,
        "retention_class": "deployed",
        "source": {
            "digest": digest,
            "reference": reference,
            "registry": registry,
            "repository": repository,
            "tag": tag,
        },
    }


class DeclarationTests(unittest.TestCase):
    def test_declaration_files(self) -> None:
        self.assertTrue(is_declaration("gitops/media/registry-sources.yaml:12"))
        self.assertTrue(
            is_declaration("gitops/producer-base-images/base-images.yaml:3")
        )
        self.assertFalse(is_declaration("gitops/media/arr.yaml:40"))
        self.assertFalse(is_declaration("observed:media/pod/container"))

    def test_only_unlocked_declaration_only_records_are_pending(self) -> None:
        digest = "sha256:" + "b" * 64
        new = upstream(
            f"docker.io/library/couchdb:3.5.3@{digest}",
            ["gitops/obsidian/registry-sources.yaml:19"],
            "upstream-couchdb-new",
        )
        locked = upstream(
            f"docker.io/library/couchdb:3.5.2@{digest}",
            ["gitops/obsidian/registry-sources.yaml:19"],
            "upstream-couchdb-old",
        )
        workload = upstream(
            f"docker.io/library/alpine:3.24@{digest}",
            ["gitops/media/slskd.yaml:30"],
            "upstream-alpine",
        )
        discovered = {"images": [new, locked, workload]}
        lock = {"images": [{**locked, "digest": digest, "destination_tags": []}]}
        pending = pending_declarations(discovered, {"images": []}, lock)
        self.assertEqual([item["id"] for item in pending], ["upstream-couchdb-new"])

    def test_a_tag_the_mirror_holds_on_another_digest_is_dropped(self) -> None:
        old, new = "sha256:" + "1" * 64, "sha256:" + "2" * 64
        lock = {
            "images": [
                {
                    "destination_repository": "upstream/docker.io/library/python",
                    "destination_tags": ["3.14-alpine", "retention-deployed-1111"],
                    "digest": old,
                }
            ]
        }
        records = [
            {
                "destination_repository": "upstream/docker.io/library/python",
                "destination_tags": ["3.14-alpine", "retention-deployed-2222"],
                "digest": new,
            },
            {
                "destination_repository": "upstream/docker.io/library/couchdb",
                "destination_tags": ["3.5.3", "retention-deployed-3333"],
                "digest": new,
            },
        ]
        kept = without_conflicting_tags(records, lock)
        self.assertEqual(kept[0]["destination_tags"], ["retention-deployed-2222"])
        self.assertEqual(
            kept[1]["destination_tags"], ["3.5.3", "retention-deployed-3333"]
        )


class LockDeclarationsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.directory.name)
        self.body = manifest()
        self.digest = "sha256:" + hashlib.sha256(self.body).hexdigest()
        (self.root / "registry").mkdir()
        (self.root / "gitops/obsidian").mkdir(parents=True)
        self.inventory = {
            "generated_at": "2026-11-01T00:00:00+00:00",
            "images": [],
            "kind": "registry-inventory",
            "schema_version": 1,
            "source_revision": "unavailable",
            "unresolved_inputs": [],
        }
        self.lock = {
            "destination_registry": "registry.rupan.dev",
            "generated_at": "2026-11-01T00:00:00+00:00",
            "images": [],
            "kind": "registry-image-lock",
            "mirror_exceptions": [],
            "schema_version": 1,
            "source_revision": "unavailable",
            "unresolved_inputs": [],
        }
        self.write("registry/images.inventory.json", self.inventory)
        self.write("registry/images.lock.json", self.lock)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def write(self, relative: str, value: dict) -> None:
        (self.root / relative).write_text(json.dumps(value, indent=2) + "\n")

    def declare(self, tag: str) -> None:
        (self.root / "gitops/obsidian/registry-sources.yaml").write_text(
            "apiVersion: v1\nkind: List\nitems:\n"
            f"  - image: docker.io/library/couchdb:{tag}@{self.digest}\n"
        )

    def test_appends_the_inventory_and_lock_records_for_a_new_declaration(self) -> None:
        self.declare("3.5.3")
        client = FakeClient(self.body)

        added = lock_declarations(self.root, client)  # type: ignore[arg-type]

        reference = f"docker.io/library/couchdb:3.5.3@{self.digest}"
        self.assertEqual(added, [reference])
        lock = json.loads((self.root / "registry/images.lock.json").read_text())
        inventory = json.loads(
            (self.root / "registry/images.inventory.json").read_text()
        )
        (record,) = lock["images"]
        self.assertEqual(record["source"]["reference"], reference)
        self.assertEqual(record["digest"], self.digest)
        self.assertEqual(record["media_type"], INDEX)
        self.assertIn("3.5.3", record["destination_tags"])
        self.assertEqual(inventory["images"][0]["id"], record["id"])
        # Only the image arrays grew; the rest of each document is untouched.
        self.assertEqual({**lock, "images": []}, self.lock)

        # A second run finds nothing new and leaves the files alone.
        before = (self.root / "registry/images.lock.json").read_text()
        self.assertEqual(lock_declarations(self.root, client), [])  # type: ignore[arg-type]
        self.assertEqual((self.root / "registry/images.lock.json").read_text(), before)

    def test_nothing_declared_means_no_registry_access(self) -> None:
        client = FakeClient(self.body)
        self.assertEqual(lock_declarations(self.root, client), [])  # type: ignore[arg-type]
        self.assertEqual(client.requests, [])


if __name__ == "__main__":
    unittest.main()
