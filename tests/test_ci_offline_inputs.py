from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class OfflineInputsTest(unittest.TestCase):
    def test_provider_archives_match_locked_versions_and_checksums(self) -> None:
        entries = json.loads((ROOT / "nix/ci-providers.json").read_text())
        self.assertEqual(len(entries), 8)
        for entry in entries:
            stack = entry["address"].split("/")[-1]
            lock = (ROOT / "tofu" / stack / ".terraform.lock.hcl").read_text()
            self.assertIn(f'provider "{entry["address"]}"', lock)
            self.assertIn(
                f'version     = "{entry["version"]}"',
                lock,
                "provider mirror lags the lock; run just tofu-providers-sync",
            )
            checksum = base64.b64decode(entry["hash"].removeprefix("sha256-")).hex()
            self.assertIn("zh:" + checksum, lock)

    def test_core_schema_retention_checksums(self) -> None:
        root = ROOT / "schemas/kubernetes-core"
        manifest = json.loads((root / "retention.json").read_text())
        for entry in manifest["checksums"]:
            data = (root / manifest["directory"] / entry["filename"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])
            self.assertIsInstance(json.loads(data), dict)

    @unittest.skipUnless(shutil.which("kubeconform"), "kubeconform is required")
    def test_core_validation_rejects_invalid_and_unretained_kinds(self) -> None:
        schema = str(ROOT / "schemas/kubernetes-core/v1.35.7-standalone-strict")
        command = [
            "kubeconform",
            "-strict",
            "-kubernetes-version",
            "1.35.7",
            "-schema-location",
            schema + "/{{.ResourceKind}}{{.KindSuffix}}.json",
        ]
        for manifest, expected in (
            ("apiVersion: v1\nkind: Namespace\nmetadata:\n  name: proof\n", 0),
            ("apiVersion: v1\nkind: Namespace\nmetadata: invalid\n", 1),
            ("apiVersion: v1\nkind: UnretainedKind\nmetadata:\n  name: proof\n", 1),
        ):
            with self.subTest(manifest=manifest):
                result = subprocess.run(
                    command, input=manifest, text=True, capture_output=True
                )
                self.assertEqual(
                    result.returncode, expected, result.stdout + result.stderr
                )


class ProviderSyncTest(unittest.TestCase):
    def test_sync_moves_only_changed_providers_to_registry_checksums(self) -> None:
        from scripts.ci.sync_providers import sync

        entries = [
            {
                "address": "registry.opentofu.org/a/x",
                "version": "1.0.0",
                "platform": "linux_amd64",
                "filename": "old",
                "url": "old",
                "hash": "old",
                "system": "x86_64-linux",
            },
            {
                "address": "registry.opentofu.org/b/y",
                "version": "2.0.0",
                "platform": "darwin_arm64",
                "filename": "keep",
                "url": "keep",
                "hash": "keep",
                "system": "aarch64-darwin",
            },
        ]
        requested = []

        def fetch(url: str) -> dict:
            requested.append(url)
            return {
                "filename": "x_1.1.0.zip",
                "download_url": "https://example/x.zip",
                "shasum": "00" * 32,
            }

        updated = sync(
            entries,
            {
                "registry.opentofu.org/a/x": "1.1.0",
                "registry.opentofu.org/b/y": "2.0.0",
            },
            fetch,
        )
        self.assertEqual(
            requested,
            [
                "https://registry.opentofu.org/v1/providers/a/x/1.1.0/download/linux/amd64"
            ],
        )
        self.assertEqual(updated[0]["version"], "1.1.0")
        self.assertEqual(
            updated[0]["hash"], "sha256-" + base64.b64encode(bytes(32)).decode()
        )
        self.assertEqual(updated[1], entries[1])
