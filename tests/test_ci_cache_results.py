from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.ci.cache_results import build_paths

ROOT = Path(__file__).resolve().parents[1]


def evaluation(index: int) -> dict[str, object]:
    return {
        "type": "EVAL",
        "attr": f"target{index}",
        "success": True,
        "drvPath": f"/nix/store/system{index}.drv",
        "outputs": {"out": f"/nix/store/system{index}"},
    }


class CacheResultsTest(unittest.TestCase):
    def test_collects_outputs_and_derivations_without_requiring_build_events(
        self,
    ) -> None:
        self.assertEqual(
            build_paths({"results": [evaluation(0), evaluation(1)]}, 2),
            (
                ["/nix/store/system0", "/nix/store/system1"],
                ["/nix/store/system0.drv", "/nix/store/system1.drv"],
            ),
        )

    def test_rejects_incomplete_failed_skipped_or_invalid_results(self) -> None:
        valid = evaluation(0)
        invalid = [
            {},
            {"results": []},
            {"results": [valid, valid]},
            {"results": [evaluation(1)]},
            {"results": [valid, {"type": "BUILD", "success": False}]},
        ]
        for changes in (
            {"success": False},
            {"skipped": True},
            {"outputs": {}},
            {"outputs": {"out": "/tmp/output"}},
            {"outputs": {"out": "/nix/store/output\nother"}},
            {"drvPath": None},
            {"drvPath": "/nix/store/output"},
        ):
            invalid.append({"results": [{**valid, **changes}]})
        for report in invalid:
            with (
                self.subTest(report=report),
                self.assertRaises((ValueError, TypeError)),
            ):
                build_paths(report, 1)

    def test_fast_builder_preserves_target_selection_and_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            fast = directory / "nix-fast-build"
            fast.write_text(
                f"#!{sys.executable}\n"
                "import json, os, pathlib, re, sys\n"
                "args = sys.argv[1:]\n"
                "assert args[args.index('--flake') + 1] == './flake#'\n"
                "assert args[args.index('--eval-workers') + 1] == '2'\n"
                "assert args[args.index('--eval-max-memory-size') + 1] == '2048'\n"
                "assert args[args.index('--max-jobs') + 1] == '2'\n"
                "assert '--skip-cached' not in args\n"
                "selection = args[args.index('--select') + 1]\n"
                "targets = re.findall(r'target(\\d+) = flake\\.([^;]+);', selection)\n"
                "assert len(targets) == 23\n"
                "assert any(t == 'checks.x86_64-linux.zot-registry-service' for _, t in targets)\n"
                "assert targets[0][1] == 'nixosConfigurations.nas-01.config.system.build.toplevel'\n"
                "assert targets[-1][1] == 'devShells.x86_64-linux.ops'\n"
                "assert sum('.config.system.build.toplevel' in t for _, t in targets) == 7\n"
                "results = [{'type': 'EVAL', 'attr': 'target' + i, 'success': True,\n"
                " 'drvPath': '/nix/store/system' + i + '.drv',\n"
                " 'outputs': {'out': '/nix/store/system' + i}} for i, _ in targets]\n"
                "pathlib.Path(args[args.index('--result-file') + 1]).write_text(json.dumps({'results': results}))\n"
                "sys.exit(int(os.environ['FAST_STATUS']))\n"
            )
            nix = directory / "nix"
            nix.write_text(
                f"#!{sys.executable}\n"
                "import json, sys\n"
                "assert sys.argv[1:3] == ['flake', 'archive'], sys.argv\n"
                "print(json.dumps({'path': '/nix/store/source'}))\n"
            )
            nix_store = directory / "nix-store"
            nix_store.write_text(
                f"#!{sys.executable}\n"
                "import sys\n"
                "assert sys.argv[1:4] == ['--query', '--requisites', '--include-outputs']\n"
                "assert len(sys.argv[4:]) == 23\n"
                "print('/nix/store/build-input')\n"
            )
            attic = directory / "attic"
            attic.write_text(
                f"#!{sys.executable}\n"
                "import os, pathlib, sys\n"
                "if sys.argv[1] == 'push':\n"
                " pathlib.Path(os.environ['PUSH_RECEIPT']).write_text(sys.stdin.read())\n"
            )
            for binary in (fast, nix, nix_store, attic):
                binary.chmod(0o755)
            receipt = directory / "pushed"
            for status in (0, 7):
                with self.subTest(status=status):
                    receipt.unlink(missing_ok=True)
                    result = subprocess.run(
                        ["bash", "scripts/ci/cache.sh"],
                        cwd=ROOT,
                        env={
                            **os.environ,
                            "PATH": str(directory) + os.pathsep + os.environ["PATH"],
                            "HOMELAB_CACHE_BUILDER": "fast",
                            "ATTIC_TOKEN": "test-only",
                            "FAST_STATUS": str(status),
                            "PUSH_RECEIPT": str(receipt),
                        },
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, status, result.stderr)
                    if status:
                        self.assertFalse(receipt.exists())
                    else:
                        self.assertEqual(
                            set(receipt.read_text().splitlines()),
                            {f"/nix/store/system{i}" for i in range(23)}
                            | {"/nix/store/source", "/nix/store/build-input"},
                        )


if __name__ == "__main__":
    unittest.main()
