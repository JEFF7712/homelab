from __future__ import annotations

import base64
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from scripts.ci.matrix_release import (
    IDENTITY,
    REGISTRY,
    REPOSITORY,
    preflight,
    promotion_patch,
    publisher_auth,
    require_new_tag,
    unpack_source,
    verify_publication,
    worker,
)
from scripts.registry.core import validate_lock

ROOT = Path(__file__).resolve().parents[1]


def auth(user: str = IDENTITY) -> dict:
    return {
        "auths": {
            REGISTRY: {
                "auth": base64.b64encode(
                    f"{user}:synthetic-test-password".encode()
                ).decode()
            }
        }
    }


class MatrixReleaseTests(unittest.TestCase):
    def test_existing_tags_and_auth_failures_are_not_overwritten(self) -> None:
        with patch("scripts.ci.matrix_release.urllib.request.urlopen"):
            with self.assertRaisesRegex(ValueError, "already exists"):
                require_new_tag("synthetic", "ci-1-" + "a" * 12)
        for status in (401, 403, 500):
            with patch(
                "scripts.ci.matrix_release.urllib.request.urlopen",
                side_effect=urllib.error.HTTPError(
                    "https://registry.rupan.dev", status, "fixture", {}, None
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, str(status)):
                    require_new_tag("synthetic", "ci-1-" + "a" * 12)
        with patch(
            "scripts.ci.matrix_release.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "https://registry.rupan.dev", 404, "fixture", {}, None
            ),
        ):
            require_new_tag("synthetic", "ci-1-" + "a" * 12)

    def test_wrong_project_and_missing_credentials_fail_before_network_or_build(
        self,
    ) -> None:
        for value in (
            {},
            auth("publisher-bus-display"),
            {"auths": {REGISTRY: {"auth": "invalid"}}},
        ):
            with (
                self.subTest(value=value),
                patch("scripts.ci.matrix_release.registry_get") as get,
            ):
                with self.assertRaisesRegex(ValueError, "publisher-spotify-matrix"):
                    preflight(value)
                get.assert_not_called()
        with patch("scripts.ci.matrix_release.subprocess.run") as run:
            with self.assertRaises(ValueError):
                worker(
                    {
                        "sha": "a" * 40,
                        "number": 1,
                        "parent": 1,
                        "publisher_auth": auth("publisher-bus-display"),
                    }
                )
            run.assert_not_called()

    def test_preflight_only_reads_registry(self) -> None:
        with patch("scripts.ci.matrix_release.registry_get", return_value=b"{}") as get:
            self.assertEqual(preflight(auth()), publisher_auth(auth()))
        get.assert_called_once_with(publisher_auth(auth()), "")

    def test_source_archive_rejects_links_escape_and_other_services(self) -> None:
        for name, kind in (
            ("../escape", tarfile.REGTYPE),
            ("services/bus-display/code.py", tarfile.REGTYPE),
            ("services/led-matrix/link", tarfile.SYMTYPE),
        ):
            data = io.BytesIO()
            with tarfile.open(fileobj=data, mode="w") as archive:
                item = tarfile.TarInfo(name)
                item.type = kind
                item.linkname = "/etc/passwd"
                archive.addfile(item, io.BytesIO())
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):
                    unpack_source(
                        base64.b64encode(data.getvalue()).decode(), Path(directory)
                    )
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_verified_manifest_must_match_tested_image_and_source(self) -> None:
        import hashlib

        sha = "a" * 40
        config = {
            "architecture": "amd64",
            "os": "linux",
            "config": {
                "Labels": {"org.opencontainers.image.revision": sha},
                "User": "1000:1000",
                "Entrypoint": ["python", "/app/ha_matrix_streamer.py"],
            },
        }
        raw_config = json.dumps(config).encode()
        image_id = "sha256:" + hashlib.sha256(raw_config).hexdigest()
        manifest = json.dumps(
            {
                "config": {"digest": image_id},
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
            }
        ).encode()
        with patch(
            "scripts.ci.matrix_release.registry_get",
            side_effect=[manifest, raw_config, manifest],
        ):
            receipt = verify_publication("synthetic", "ci-1-" + sha[:12], image_id, sha)
        self.assertEqual(
            receipt["digest"], "sha256:" + hashlib.sha256(manifest).hexdigest()
        )
        with patch("scripts.ci.matrix_release.registry_get", return_value=manifest):
            with self.assertRaisesRegex(ValueError, "tested local image"):
                verify_publication(
                    "synthetic", "ci-1-" + sha[:12], "sha256:" + "b" * 64, sha
                )
        with patch(
            "scripts.ci.matrix_release.registry_get", side_effect=[manifest, raw_config]
        ):
            with self.assertRaisesRegex(ValueError, "runtime contract"):
                verify_publication("synthetic", "ci-1-" + sha[:12], image_id, "c" * 40)
        with patch(
            "scripts.ci.matrix_release.registry_get", side_effect=[manifest, b"corrupt"]
        ):
            with self.assertRaisesRegex(ValueError, "checksum"):
                verify_publication("synthetic", "ci-1-" + sha[:12], image_id, sha)

    def test_promotion_is_reviewable_applicable_and_does_not_write_source(self) -> None:
        receipt = {
            "digest": "sha256:" + "a" * 64,
            "repository": REPOSITORY,
            "tag": "ci-12-" + "b" * 12,
            "media_type": "application/vnd.oci.image.manifest.v1+json",
            "retention_tag": "retention-candidate-" + "a" * 16,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = (
                "gitops/led-matrix/deployment.yaml",
                "registry/images.lock.json",
                "registry/images.inventory.json",
            )
            originals = {}
            for name in names:
                originals[name] = (ROOT / name).read_bytes()
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(originals[name])
            proposal = promotion_patch(root, receipt)
            for name in names:
                self.assertEqual((root / name).read_bytes(), originals[name])
            subprocess.run(
                ["git", "apply", "--check", "-"],
                input=proposal,
                text=True,
                cwd=root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "apply", "-"],
                input=proposal,
                text=True,
                cwd=root,
                check=True,
                capture_output=True,
            )
            self.assertIn(receipt["digest"], (root / names[0]).read_text())
            for name in names[1:]:
                original = json.loads(originals[name])
                updated = json.loads((root / name).read_text())
                for before, after in zip(
                    original["images"], updated["images"], strict=True
                ):
                    if before["id"] == "first-party-apps-spotify-matrix":
                        self.assertEqual(after["digest"], receipt["digest"])
                        self.assertEqual(after["source"]["digest"], receipt["digest"])
                    else:
                        self.assertEqual(before, after)
            self.assertEqual(
                validate_lock(json.loads((root / names[1]).read_text())), []
            )

    def test_protected_operation_is_manual_and_uses_only_scoped_writer(self) -> None:
        operation = json.loads((ROOT / "config/ci/operations.json").read_text())[
            "matrix-publish"
        ]
        self.assertFalse(operation["automatic"])
        self.assertTrue(operation["mutation"])
        self.assertIn("REGISTRY_MATRIX_PUBLISHER_AUTH_FILE", operation["file_secrets"])
        self.assertNotIn("REGISTRY_MAINTENANCE_AUTH_FILE", operation["environment"])
        self.assertIn(
            "python -m scripts.ci.matrix_release coordinate", operation["commands"]
        )
