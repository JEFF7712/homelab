from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.recovery_pack import CONDITIONS, REQUIREMENTS, audit, verify_oci


class RecoveryPackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "proof.txt").write_bytes(b"verified receipt bytes")

    def entry(self, relative: str) -> dict:
        path = self.root / relative
        return {
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    def manifest(self) -> dict:
        return {
            "schema_version": 1,
            "assets": [{"id": "proof", "files": [self.entry("proof.txt")]}],
            "requirements": {name: {"assets": ["proof"]} for name in REQUIREMENTS},
            "conditions": {
                name: {"satisfied": True, "evidence_asset": "proof"}
                for name in CONDITIONS
            },
        }

    def test_integrity_is_separate_from_declared_coverage(self) -> None:
        manifest = self.manifest()
        manifest["requirements"].pop("platform-state")
        result = audit(self.root, manifest)
        self.assertTrue(result["integrity_valid"])
        self.assertFalse(result["declared_inputs_complete"])
        self.assertIn("platform-state", result["blocked_requirements"])

    def test_matching_size_does_not_hide_corruption(self) -> None:
        manifest = self.manifest()
        data = (self.root / "proof.txt").read_bytes()
        (self.root / "proof.txt").write_bytes(b"x" + data[1:])
        result = audit(self.root, manifest)
        self.assertFalse(result["integrity_valid"])
        self.assertNotIn("proof", result["verified_assets"])

    def test_escape_and_symlink_are_rejected(self) -> None:
        for relative in ("../proof.txt", "/etc/passwd", "link/proof.txt"):
            with self.subTest(relative=relative):
                manifest = self.manifest()
                manifest["assets"][0]["files"][0]["path"] = relative
                (self.root / "link").symlink_to(self.root, target_is_directory=True)
                try:
                    self.assertFalse(audit(self.root, manifest)["integrity_valid"])
                finally:
                    (self.root / "link").unlink()

    def test_unverified_custody_and_missing_evidence_fail_closed(self) -> None:
        manifest = self.manifest()
        manifest["conditions"]["independent-copy"] = {
            "satisfied": False,
            "reason": "Operator deferred second copy",
        }
        manifest["conditions"]["installer-boot"]["evidence_asset"] = "absent"
        result = audit(self.root, manifest)
        self.assertFalse(result["declared_inputs_complete"])
        self.assertIn("independent-copy", result["blocked_requirements"])
        self.assertIn("installer-boot", result["blocked_requirements"])

    def test_duplicate_asset_identity_is_rejected(self) -> None:
        manifest = self.manifest()
        manifest["assets"].append(manifest["assets"][0])
        with self.assertRaisesRegex(ValueError, "unique"):
            audit(self.root, manifest)

    def layout(self) -> tuple[str, dict]:
        blobs = self.root / "image/blobs/sha256"
        blobs.mkdir(parents=True)

        def put(data: bytes) -> dict:
            sha = hashlib.sha256(data).hexdigest()
            (blobs / sha).write_bytes(data)
            return {"digest": "sha256:" + sha, "size": len(data)}

        config = put(b'{"architecture":"amd64","os":"linux"}')
        layer = put(b"retained layer bytes")
        manifest = put(
            json.dumps(
                {"schemaVersion": 2, "config": config, "layers": [layer]}
            ).encode()
        )
        (self.root / "image/index.json").write_text(
            json.dumps({"schemaVersion": 2, "manifests": [manifest]})
        )
        return manifest["digest"], layer

    def test_original_manifest_and_every_layer_are_required(self) -> None:
        digest, layer = self.layout()
        self.assertEqual(verify_oci(self.root, "image", [digest]), 3)
        (self.root / "image/blobs/sha256" / layer["digest"][7:]).unlink()
        with self.assertRaises(FileNotFoundError):
            verify_oci(self.root, "image", [digest])

    def test_nested_index_cannot_hide_a_missing_child(self) -> None:
        digest, _ = self.layout()
        missing = {"digest": "sha256:" + "a" * 64, "size": 100}
        data = json.dumps({"schemaVersion": 2, "manifests": [missing]}).encode()
        root = hashlib.sha256(data).hexdigest()
        (self.root / "image/blobs/sha256" / root).write_bytes(data)
        (self.root / "image/index.json").write_text(
            json.dumps({"manifests": [{"digest": "sha256:" + root, "size": len(data)}]})
        )
        with self.assertRaises(FileNotFoundError):
            verify_oci(self.root, "image", [digest])

    def test_converted_layout_cannot_substitute_an_absent_original(self) -> None:
        self.layout()
        with self.assertRaises(FileNotFoundError):
            verify_oci(self.root, "image", ["sha256:" + "b" * 64])


if __name__ == "__main__":
    unittest.main()
