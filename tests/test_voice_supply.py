import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.voice.build_image import source_revision
from scripts.voice.fetch_supply import fetch


class VoiceSupplyTests(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, bytes]:
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            member = tarfile.TarInfo("wheels/example.whl")
            member.size = 5
            archive.addfile(member, io.BytesIO(b"wheel"))
        data = stream.getvalue()
        part = {
            "filename": "wheels.tar.part000",
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        manifest = root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "package": "JEFF7712/voice-supply",
                    "version": "1",
                    "archives": [
                        {
                            "filename": "wheels.tar",
                            "size": len(data),
                            "sha256": part["sha256"],
                            "files": [part],
                        }
                    ],
                }
            )
        )
        return manifest, data

    def test_download_verified_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, data = self.fixture(root)
            with patch("urllib.request.urlopen", return_value=io.BytesIO(data)):
                fetch(manifest, root / "supply", "test-token")
            self.assertEqual(
                (root / "supply/wheels/example.whl").read_bytes(), b"wheel"
            )

    def test_modified_download_is_rejected_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, data = self.fixture(root)
            with patch(
                "urllib.request.urlopen", return_value=io.BytesIO(data + b"bad")
            ):
                with self.assertRaisesRegex(ValueError, "part mismatch"):
                    fetch(manifest, root / "supply", "test-token")
            self.assertFalse((root / "supply/wheels").exists())

    def test_archive_traversal_is_rejected_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, _ = self.fixture(root)
            record = json.loads(manifest.read_text())
            record["archives"][0]["filename"] = "../escape.tar"
            manifest.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, "filename"):
                fetch(manifest, root / "supply", "test-token")
            self.assertFalse((root / "supply").exists())

    def test_nix_export_is_verified_without_tar_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, data = self.fixture(root)
            record = json.loads(manifest.read_text())
            record["archives"][0]["filename"] = "sdk.nar.gz"
            manifest.write_text(json.dumps(record))
            with patch("urllib.request.urlopen", return_value=io.BytesIO(data)):
                fetch(manifest, root / "supply", "test-token")
            self.assertEqual((root / "supply/sdk.nar.gz").read_bytes(), data)
            self.assertFalse((root / "supply/wheels").exists())

    def test_image_revision_rejects_modified_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args: str) -> str:
                return subprocess.check_output(
                    ["git", "-C", str(root), *args], text=True
                )

            git("init", "-q")
            paths = (
                "scripts/voice/build_image.py",
                "services/voice-supply/Dockerfile.nemotron",
                "services/voice-supply/native-sdk-manifest.json",
                "services/voice-supply/native-source-manifest.json",
                "services/voice-supply/native-wheels.json",
                "gitops/voice/nemotron-bridge/bridge.py",
                "gitops/voice/nemotron-bridge/proxy.py",
                "gitops/voice/nemotron-bridge/verify_contract.py",
            )
            for name in paths:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("original\n")
            git("add", "--", *paths)
            git(
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@localhost",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "fixture",
            )
            self.assertEqual(source_revision(root), git("rev-parse", "HEAD").strip())
            (root / paths[1]).write_text("modified\n")
            with self.assertRaisesRegex(ValueError, "uncommitted"):
                source_revision(root)
