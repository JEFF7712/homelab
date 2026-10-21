from __future__ import annotations

import io
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.ci import dotnet_release as release
from scripts.ci import dotnet_service

SDK = "registry.rupan.dev/apps/dotnet-sdk-6@sha256:" + "a" * 64
BASE = "registry.rupan.dev/upstream/docker.io/library/python@sha256:" + "b" * 64


def supply() -> list[dict]:
    return [
        {
            "package": "bookshelf-build",
            "version": "1",
            "filename": path.rsplit("/", 1)[-1],
            "sha256": "sha256:" + "c" * 64,
            "kind": "file",
            "dest": path,
        }
        for path in (*release.INPUTS, "/usr/lib/x86_64-linux-gnu/libsqlite3.so.0")
    ]


class DotnetReleaseTests(unittest.TestCase):
    def test_build_copy_excludes_private_clone_metadata_and_prior_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "README.md").write_text("application source")
            pack = source / ".git/objects/pack"
            pack.mkdir(parents=True)
            private = pack / "pack.promisor"
            private.write_text("clone metadata")
            private.chmod(0)
            previous = source / ".sovereign/build"
            previous.mkdir(parents=True)
            (previous / "prior-output").write_text("prior build")
            try:
                release.copy_build_source(source, root / "build-source")
                self.assertEqual(
                    (root / "build-source/README.md").read_text(), "application source"
                )
                self.assertFalse((root / "build-source/.git").exists())
                self.assertFalse((root / "build-source/.sovereign").exists())
                self.assertTrue(private.exists())
            finally:
                private.chmod(0o600)

    def test_runtime_host_must_be_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            (runtime / "dotnet").mkdir()
            host = runtime / "dotnet/dotnet"
            with self.assertRaisesRegex(ValueError, "executable"):
                release.check_runtime_host(runtime)
            host.write_bytes(b"host")
            host.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "executable"):
                release.check_runtime_host(runtime)
            host.chmod(0o755)
            release.check_runtime_host(runtime)

    def test_missing_retained_feed_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "retain NuGet"):
            release.check_contract(supply()[1:], SDK)

    def test_public_or_unpinned_sdk_is_rejected(self) -> None:
        for image in ("mcr.microsoft.com/dotnet/sdk:6.0", SDK.split("@")[0]):
            with (
                self.subTest(image=image),
                self.assertRaisesRegex(ValueError, "pinned retained"),
            ):
                release.check_contract(supply(), image)

    def test_duplicate_supply_cannot_replace_an_input(self) -> None:
        items = supply()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            release.check_contract(items + [items[0]], SDK)

    def test_build_inputs_never_ship_as_runtime_supply(self) -> None:
        runtime = release.runtime_supply(supply())
        self.assertEqual(len(runtime), 1)
        self.assertEqual(
            runtime[0]["dest"], "/usr/lib/x86_64-linux-gnu/libsqlite3.so.0"
        )

    def test_archive_cannot_write_outside_the_input_directory(self) -> None:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            entry = tarfile.TarInfo("../outside")
            entry.size = 1
            archive.addfile(entry, io.BytesIO(b"x"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(tarfile.FilterError):
                release.unpack(buffer.getvalue(), root / "inputs")
            self.assertFalse((root / "outside").exists())

    def test_bundled_cli_imports_only_server_owned_code(self) -> None:
        payload = release.bundled_source(Path(release.__file__).parent)
        result = subprocess.run(
            [sys.executable, "-I", "-c", payload.decode(), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--build-image", result.stdout)

    def test_dotnet_validator_accepts_retained_sdk_and_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path in ("docker", "src", "frontend"):
                (root / path).mkdir()
            dockerfile = root / "docker/Dockerfile"
            dockerfile.write_text(
                f"FROM {SDK} AS build\nFROM {BASE}\nCOPY --from=build /output /app\n"
            )
            dotnet_service.validate(root)
            dockerfile.write_text(f"FROM docker.io/{SDK} AS build\nFROM {BASE}\n")
            with self.assertRaisesRegex(ValueError, "digest-pinned local"):
                dotnet_service.validate(root)


if __name__ == "__main__":
    unittest.main()
