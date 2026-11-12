from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.ci import migrate_secrets


class SecretInputTest(unittest.TestCase):
    def test_encrypted_values_accept_both_secret_encodings(self) -> None:
        document = {
            "kind": "Secret",
            "data": {"TOKEN": base64.b64encode(b"encoded").decode()},
            "stringData": {"TOKEN": "override", "FILE": "line1\nline2\n"},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "values.sops.yaml"
            path.touch()
            with patch.object(
                migrate_secrets.subprocess,
                "check_output",
                return_value=json.dumps(document),
            ):
                self.assertEqual(
                    migrate_secrets.encrypted_values(path),
                    {"TOKEN": "override", "FILE": "line1\nline2\n"},
                )

    def test_invalid_encrypted_document_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "values.sops.yaml"
            path.touch()
            for document in [
                {"kind": "ConfigMap"},
                {"kind": "Secret", "stringData": {"TOKEN": 123}},
                {"kind": "Secret", "data": {"TOKEN": "invalid!"}},
            ]:
                with (
                    self.subTest(document=document),
                    patch.object(
                        migrate_secrets.subprocess,
                        "check_output",
                        return_value=json.dumps(document),
                    ),
                    self.assertRaises(ValueError),
                ):
                    migrate_secrets.encrypted_values(path)

    def test_rotation_preserves_unrotated_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "gitops/secrets/homelab-values.sops.yaml"
            target.parent.mkdir(parents=True)
            target.write_text("encrypted")
            overrides = root / "overrides.json"
            overrides.write_text(json.dumps({"ROTATE": "new"}))
            with (
                patch.object(migrate_secrets, "ROOT", root),
                patch.object(
                    migrate_secrets, "references", return_value={"KEEP", "ROTATE"}
                ),
                patch.object(
                    migrate_secrets,
                    "encrypted_values",
                    return_value={"KEEP": "original", "ROTATE": "old"},
                ),
                patch.object(
                    migrate_secrets.subprocess,
                    "check_output",
                    return_value=b"ciphertext",
                ) as encrypt,
                patch("sys.argv", ["migrate_secrets", "--overrides", str(overrides)]),
            ):
                migrate_secrets.main()
            payload = yaml.safe_load(encrypt.call_args.kwargs["input"])
            self.assertEqual(
                payload["stringData"], {"KEEP": "original", "ROTATE": "new"}
            )
            self.assertEqual(target.read_bytes(), b"ciphertext")


if __name__ == "__main__":
    unittest.main()
