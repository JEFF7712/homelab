from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.ci.cache_sources import source_paths

ROOT = Path(__file__).resolve().parents[1]


class CacheTargetsTest(unittest.TestCase):
    def population_targets(self) -> set[str]:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "scripts/ci").mkdir(parents=True)
            shutil.copy(ROOT / "scripts/ci/cache.sh", directory / "scripts/ci")
            shutil.copy(ROOT / "justfile", directory)
            binaries = directory / "bin"
            binaries.mkdir()
            nix = binaries / "nix"
            nix.write_text(
                f"#!{sys.executable}\n"
                "import json, os, pathlib, sys\n"
                "assert sys.argv[1:4] == ['build', '--no-link', '--print-out-paths']\n"
                "pathlib.Path(os.environ['TARGETS']).write_text(json.dumps(sys.argv[4:]))\n"
            )
            attic = binaries / "attic"
            attic.write_text(f"#!{sys.executable}\nimport sys\nsys.stdin.read()\n")
            nix.chmod(0o755)
            attic.chmod(0o755)
            receipt = directory / "targets.json"
            env = dict(
                os.environ,
                PATH=f"{binaries}:{os.environ['PATH']}",
                ATTIC_TOKEN="",
                HOMELAB_CACHE_BUILDER="native",
                HOMELAB_DEV_SHELL="1",
                TARGETS=str(receipt),
            )
            targets = []
            for command in (
                ["bash", "scripts/ci/cache.sh"],
                ["just", "cache-populate"],
            ):
                result = subprocess.run(
                    command, cwd=directory, env=env, text=True, capture_output=True
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                targets.append(set(json.loads(receipt.read_text())))
            self.assertEqual(targets[0], targets[1])
            return targets[0]

    def test_local_and_ci_publication_include_service_vm_check(self) -> None:
        self.assertIn(
            "./flake#checks.x86_64-linux.zot-registry-service",
            self.population_targets(),
        )

    @unittest.skipUnless(shutil.which("nix"), "requires Nix source evaluation")
    def test_publication_covers_every_evaluated_flake_check(self) -> None:
        result = subprocess.run(
            [
                "nix",
                "eval",
                "--json",
                "--offline",
                "./flake#checks.x86_64-linux",
                "--apply",
                "builtins.attrNames",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        checks = {
            target.split("#checks.x86_64-linux.", 1)[1]
            for target in self.population_targets()
            if "#checks.x86_64-linux." in target
        }
        self.assertEqual(checks, set(json.loads(result.stdout)))


class CacheSourcesTest(unittest.TestCase):
    def test_relative_inputs_keep_parent_and_external_descendants(self) -> None:
        archive = {
            "path": "/nix/store/root-source",
            "inputs": {
                "intentd": {
                    "inputs": {
                        "nested-relative": {"inputs": {}},
                        "external": {"path": "/nix/store/external-source"},
                    },
                },
            },
        }
        self.assertEqual(
            source_paths(archive),
            ["/nix/store/external-source", "/nix/store/root-source"],
        )

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
            {"inputs": {}},
            {"path": "/nix/store/source", "inputs": {"bad": {"inputs": []}}},
            {
                "path": "/nix/store/source",
                "inputs": {"bad": {"path": None, "inputs": {}}},
            },
        ):
            with self.subTest(archive=archive), self.assertRaises(ValueError):
                source_paths(archive)

    def test_publication_includes_sources_and_rejects_incomplete_archive(self) -> None:
        for scenario in (
            "valid",
            "invalid-archive",
            "failed-derivations",
            "failed-build-inputs",
        ):
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
                    "import json, os, pathlib, sys\n"
                    "targets = pathlib.Path(os.environ['BUILD_TARGETS_RECEIPT'])\n"
                    "if sys.argv[1] == 'build':\n"
                    " targets.write_text(json.dumps(sys.argv[4:]))\n"
                    " print('/nix/store/system\\n/nix/store/devshell')\n"
                    "elif sys.argv[1] == 'path-info':\n"
                    " assert sys.argv[2] == '--derivation'\n"
                    " assert sys.argv[3:] == json.loads(targets.read_text())\n"
                    " print('/nix/store/system.drv\\n/nix/store/devshell.drv')\n"
                    " sys.exit(int(os.environ['DERIVATION_EXIT']))\n"
                    f"else: print({json.dumps(archive)!r})\n"
                )
                nix_store = directory / "nix-store"
                nix_store.write_text(
                    f"#!{sys.executable}\n"
                    "import os, sys\n"
                    "assert sys.argv[1:] == ['--query', '--requisites', '--include-outputs', '/nix/store/system.drv', '/nix/store/devshell.drv']\n"
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
                        "HOMELAB_CACHE_BUILDER": "native",
                        "PUSH_RECEIPT": str(receipt),
                        "BUILD_TARGETS_RECEIPT": str(directory / "build-targets"),
                        "DERIVATION_EXIT": "5"
                        if scenario == "failed-derivations"
                        else "0",
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
                            "/nix/store/devshell",
                            "/nix/store/source",
                            "/nix/store/build-input",
                        ],
                    )
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(receipt.exists())


if __name__ == "__main__":
    unittest.main()
