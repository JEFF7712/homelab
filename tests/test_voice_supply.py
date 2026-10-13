import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
