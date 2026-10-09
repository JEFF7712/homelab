from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.ci.static_release import (
    CONFIG,
    LAYER,
    MANIFEST,
    Registry,
    assemble,
    digest,
    encode,
    load_base,
    publish,
    store,
)


class RecordingRegistry(Registry):
    def __init__(self) -> None:
        self.blobs: list[bytes] = []
        self.manifests: dict[str, bytes] = {}

    def request(
        self, method: str, path: str, body: bytes | None = None, media: str = MANIFEST
    ) -> tuple[int, dict[str, str], bytes]:
        if method == "GET":
            return (
                (200, {}, self.manifests[path])
                if path in self.manifests
                else (404, {}, b"")
            )
        if method == "PUT" and body is not None:
            self.manifests[path] = body
            self.manifests[digest(body)] = body
            return 201, {}, b""
        raise AssertionError(method)

    def push_blob(self, repository: str, data: bytes) -> None:
        self.blobs.append(data)

    def get(self, repository: str, kind: str, reference: str) -> bytes:
        return self.manifests[reference]


class StaticReleaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "index.html").write_text('<link href="style.css">')
        (self.source / "style.css").write_text("body {color: black}")
        (self.source / "assets").mkdir()
        (self.source / "assets/product.png").write_bytes(b"product image fixture")
        self.base_root = self.root / "base"
        config = {
            "os": "linux",
            "architecture": "amd64",
            "config": {"User": "101", "Cmd": ["nginx", "-g", "daemon off;"]},
            "rootfs": {"type": "layers", "diff_ids": [digest(b"base tar")]},
        }
        self.base_manifest = {
            "schemaVersion": 2,
            "mediaType": MANIFEST,
            "config": store(self.base_root, encode(config), CONFIG),
            "layers": [store(self.base_root, b"compressed base fixture", LAYER)],
        }
        original = store(self.base_root, encode(self.base_manifest), MANIFEST)
        self.base = (
            "registry.rupan.dev/upstream/docker.io/nginxinc/nginx-unprivileged@"
            + original["digest"]
        )
        (self.base_root / "input.json").write_bytes(
            encode({"base": self.base, "manifest": original, "original": original})
        )
        self.commit = "a" * 40
        self.output = self.root / "output"
        self.registry = RecordingRegistry()

    def build(self) -> str:
        return assemble(
            self.source, self.base_root, self.output, self.base, self.commit
        )

    def push(self) -> str:
        return publish(
            self.output,
            self.base_root,
            self.base,
            self.commit,
            "apps/darkbit",
            21,
            self.registry,
            self.source,
        )

    def test_build_is_deterministic_without_registry_or_commands(self) -> None:
        (self.source / "Dockerfile").write_text("RUN steal secrets")
        (self.source / "base64.py").write_text("raise RuntimeError()")
        first = self.build()
        (self.source / "index.html").touch()
        second = assemble(
            self.source, self.base_root, self.root / "second", self.base, self.commit
        )
        self.assertEqual(first, second)
        self.assertEqual(self.push(), first)
        self.assertIn("/v2/apps/darkbit/manifests/0.0.1000021", self.registry.manifests)

    def test_missing_and_corrupt_inputs_fail_closed(self) -> None:
        layer = self.base_manifest["layers"][0]
        path = self.base_root / "blobs/sha256" / layer["digest"][7:]
        original = path.read_bytes()
        path.write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.build()
        path.write_bytes(original)
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.build()

    def test_base_record_cannot_substitute_another_manifest(self) -> None:
        record = json.loads((self.base_root / "input.json").read_text())
        changed = copy.deepcopy(self.base_manifest)
        changed["annotations"] = {"tampered": "true"}
        record["manifest"] = store(self.base_root, encode(changed), MANIFEST)
        (self.base_root / "input.json").write_bytes(encode(record))
        with self.assertRaisesRegex(ValueError, "pinned"):
            load_base(self.base_root, self.base)

    def test_symlinks_and_reserved_output_fail_closed(self) -> None:
        (self.source / "assets/escape").symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.build()
        (self.source / "assets/escape").unlink()
        with self.assertRaises(FileExistsError):
            self.build()

    def test_publisher_rejects_source_changed_after_build_before_writes(self) -> None:
        self.build()
        (self.source / "index.html").write_text("modified")
        with self.assertRaisesRegex(ValueError, "checked source"):
            self.push()
        self.assertEqual(self.registry.blobs, [])
        self.assertEqual(self.registry.manifests, {})

    def test_publisher_rejects_runtime_change_with_valid_rehashed_blobs(self) -> None:
        self.build()
        release = json.loads((self.output / "release.json").read_text())
        manifest_path = self.output / "blobs/sha256" / release["manifest"]["digest"][7:]
        manifest = json.loads(manifest_path.read_text())
        config_path = self.output / "blobs/sha256" / manifest["config"]["digest"][7:]
        config = json.loads(config_path.read_text())
        config["config"]["Cmd"] = ["malicious"]
        manifest["config"] = store(self.output, encode(config), CONFIG)
        release["manifest"] = store(self.output, encode(manifest), MANIFEST)
        (self.output / "release.json").write_bytes(encode(release))
        with self.assertRaisesRegex(ValueError, "runtime configuration"):
            self.push()
        self.assertEqual(self.registry.blobs, [])

    def test_publisher_rejects_conflicting_tag_and_wrong_source(self) -> None:
        self.build()
        self.registry.manifests["/v2/apps/darkbit/manifests/0.0.1000021"] = (
            b"another manifest"
        )
        with self.assertRaisesRegex(ValueError, "another image"):
            self.push()
        release = json.loads((self.output / "release.json").read_text())
        release["source"] = "b" * 40
        (self.output / "release.json").write_bytes(encode(release))
        with self.assertRaisesRegex(ValueError, "identity"):
            self.push()
        self.assertEqual(self.registry.blobs, [])


if __name__ == "__main__":
    unittest.main()
