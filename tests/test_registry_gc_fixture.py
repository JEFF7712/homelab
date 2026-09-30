from __future__ import annotations

import hashlib
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from scripts.registry import gc_fixture
from scripts.registry.gc_fixture import (
    EXPECTED_ZOT_VERSION,
    build_config,
    build_image,
    retention_tag,
    zot_version,
)


class BuildImageTests(unittest.TestCase):
    def test_layout_is_well_formed_and_self_consistent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            digest = build_image(root, b"payload")

            self.assertTrue(digest.startswith("sha256:"))
            self.assertEqual(
                json.loads((root / "oci-layout").read_text()),
                {"imageLayoutVersion": "1.0.0"},
            )
            index = json.loads((root / "index.json").read_text())
            self.assertEqual(index["manifests"][0]["digest"], digest)

            manifest = (root / "blobs" / "sha256" / digest[7:]).read_bytes()
            self.assertEqual("sha256:" + hashlib.sha256(manifest).hexdigest(), digest)
            descriptor = json.loads(manifest)
            for reference in [descriptor["config"], *descriptor["layers"]]:
                body = (
                    root / "blobs" / "sha256" / reference["digest"][7:]
                ).read_bytes()
                self.assertEqual(len(body), reference["size"])
                self.assertEqual(
                    "sha256:" + hashlib.sha256(body).hexdigest(), reference["digest"]
                )

    def test_same_payload_yields_the_same_digest(self) -> None:
        with (
            tempfile.TemporaryDirectory() as first,
            tempfile.TemporaryDirectory() as second,
        ):
            self.assertEqual(
                build_image(pathlib.Path(first), b"stable"),
                build_image(pathlib.Path(second), b"stable"),
            )

    def test_different_payload_yields_a_different_digest(self) -> None:
        with (
            tempfile.TemporaryDirectory() as first,
            tempfile.TemporaryDirectory() as second,
        ):
            self.assertNotEqual(
                build_image(pathlib.Path(first), b"one"),
                build_image(pathlib.Path(second), b"two"),
            )


class ConfigTests(unittest.TestCase):
    def test_matches_production_on_what_decides_collection(self) -> None:
        config = build_config(
            pathlib.Path("/tmp/storage"),
            pathlib.Path("/tmp/htpasswd"),
            5000,
            "fixture",
        )
        self.assertEqual(config["distSpecVersion"], "1.1.1")
        self.assertTrue(config["storage"]["gc"])
        self.assertTrue(config["storage"]["dedupe"])
        self.assertTrue(config["storage"]["commit"])
        # The whole point: production sets no retention policy, which is what
        # makes an untagged manifest collectable. If this ever gains a key, the
        # fixture would be proving something production does not do.
        self.assertNotIn("retention", config["storage"])

    def test_collection_interval_is_shortened_for_the_test(self) -> None:
        config = build_config(
            pathlib.Path("/tmp/storage"),
            pathlib.Path("/tmp/htpasswd"),
            5000,
            "fixture",
        )
        # Production runs 24h; the fixture cannot wait that long.
        self.assertNotEqual(config["storage"]["gcInterval"], "24h")

    def test_the_fixture_user_can_delete_tags(self) -> None:
        # Without the delete action the tag removal 403s and nothing is ever
        # collectable, which silently turns every check into a pass.
        config = build_config(
            pathlib.Path("/tmp/storage"),
            pathlib.Path("/tmp/htpasswd"),
            5000,
            "fixture",
        )
        policy = config["http"]["accessControl"]["repositories"]["**"]["policies"][0]
        self.assertIn("delete", policy["actions"])
        self.assertEqual(policy["users"], ["fixture"])


class VersionTests(unittest.TestCase):
    def _version(self, commit: str) -> str:
        completed = mock.Mock(
            returncode=0, stdout=json.dumps({"commit": commit}), stderr=""
        )
        with mock.patch.object(gc_fixture.subprocess, "run", return_value=completed):
            return zot_version("zot")

    def test_git_describe_suffix_is_dropped(self) -> None:
        self.assertEqual(self._version("v2.1.20-0-g3b5796d"), "2.1.20")

    def test_plain_version_is_accepted(self) -> None:
        self.assertEqual(self._version("v2.1.20"), "2.1.20")

    def test_unparseable_version_is_reported(self) -> None:
        with self.assertRaises(gc_fixture.FixtureError):
            self._version("vnightly")

    def test_expected_version_is_the_release_the_registry_serves(self) -> None:
        self.assertEqual(EXPECTED_ZOT_VERSION, "2.1.20")


class RetentionTagTests(unittest.TestCase):
    def test_tag_is_derived_from_the_digest(self) -> None:
        digest = "sha256:" + "a" * 64
        self.assertEqual(retention_tag(digest), "retention-deployed-" + "a" * 16)

    def test_distinct_digests_give_distinct_tags(self) -> None:
        self.assertNotEqual(
            retention_tag("sha256:" + "a" * 64), retention_tag("sha256:" + "b" * 64)
        )
