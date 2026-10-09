from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.ci.cache_sources import source_paths

ROOT = Path(__file__).resolve().parents[1]


class CacheSourcesTest(unittest.TestCase):
    def test_recursive_sources_are_deduplicated_and_sorted(self) -> None:
        archive = {
            "path": "/nix/store/root-source",
            "inputs": {
                "first": {"path": "/nix/store/shared-source"},
                "second": {
                    "path": "/nix/store/second-source",
                    "inputs": {"nested": {"path": "/nix/store/shared-source"}},
                },
            },
        }
        self.assertEqual(
            source_paths(archive),
            [
                "/nix/store/root-source",
                "/nix/store/second-source",
                "/nix/store/shared-source",
            ],
        )

    def test_incomplete_or_invalid_archives_fail(self) -> None:
        for archive in (
            [],
            {},
            {"path": "/tmp/source"},
            {"path": "/nix/store/source\n/nix/store/other"},
            {"path": "/nix/store/source", "inputs": []},
            {"path": "/nix/store/source", "inputs": {"missing": {}}},
        ):
            with self.subTest(archive=archive), self.assertRaises(ValueError):
                source_paths(archive)

    def test_publication_includes_sources_and_rejects_incomplete_archive(self) -> None:
        for scenario in ("valid", "invalid-archive", "failed-build-inputs"):
            with (
                self.subTest(scenario=scenario),
                tempfile.TemporaryDirectory() as temporary,
            ):
                directory = Path(temporary)
                archive = (
                    {"path": "/nix/store/source"}
                    if scenario != "invalid-archive"
                    else {}
                )
                nix = directory / "nix"
                nix.write_text(
                    f"#!{sys.executable}\n"
                    "import sys\n"
                    "if sys.argv[1] == 'build': print('/nix/store/system')\n"
                    "elif sys.argv[1] == 'path-info': print('/nix/store/devshell.drv')\n"
                    f"else: print({json.dumps(archive)!r})\n"
                )
                nix_store = directory / "nix-store"
                nix_store.write_text(
                    f"#!{sys.executable}\n"
                    "import os, sys\n"
                    "assert sys.argv[1:] == ['--query', '--requisites', '--include-outputs', '/nix/store/devshell.drv']\n"
                    "print('/nix/store/build-input')\n"
                    "sys.exit(int(os.environ['BUILD_INPUT_EXIT']))\n"
                )
                attic = directory / "attic"
                attic.write_text(
                    f"#!{sys.executable}\n"
                    "import os, pathlib, sys\n"
                    "if sys.argv[1] == 'push':\n"
                    " pathlib.Path(os.environ['PUSH_RECEIPT']).write_text(sys.stdin.read())\n"
                )
                nix.chmod(0o755)
                nix_store.chmod(0o755)
                attic.chmod(0o755)
                receipt = directory / "pushed"
                result = subprocess.run(
                    ["bash", str(ROOT / "scripts/ci/cache.sh")],
                    cwd=ROOT,
                    env={
                        **os.environ,
                        "PATH": str(directory) + os.pathsep + os.environ["PATH"],
                        "ATTIC_TOKEN": "test-only",
                        "PUSH_RECEIPT": str(receipt),
                        "BUILD_INPUT_EXIT": "5"
                        if scenario == "failed-build-inputs"
                        else "0",
                    },
                    capture_output=True,
                    text=True,
                )
                if scenario == "valid":
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        receipt.read_text().splitlines(),
                        [
                            "/nix/store/system",
                            "/nix/store/source",
                            "/nix/store/build-input",
                        ],
                    )
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(receipt.exists())


if __name__ == "__main__":
    unittest.main()
