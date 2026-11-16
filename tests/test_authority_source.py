"""The CI host's authority baseline is computed from a narrowed source.

flake/modules/woodpecker.nix reads the protected prefixes from the same
Python tuples snapshot() uses and keeps only those paths and .nix files, so
documentation and manifest commits leave homelab-04's system unchanged. These
tests prove the narrowed source yields the same baseline as the whole tree.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from scripts.ci import deployment_risk
from scripts.verifier import enforcement

ROOT = Path(__file__).resolve().parent.parent

# The line grammar woodpecker.nix's pythonTuple accepts.
TUPLE_ENTRY = re.compile(r' *"([^"]+)", *')
TUPLE_SKIP = re.compile(r" *(#.*)?")


def nix_tuple(path: Path, name: str) -> list[str]:
    lines = path.read_text().split("\n")
    start = lines.index(f"{name} = (")
    rest = lines[start + 1 :]
    entries = []
    for line in rest[: rest.index(")")]:
        literal = TUPLE_ENTRY.fullmatch(line)
        if literal:
            entries.append(literal.group(1))
        elif not TUPLE_SKIP.fullmatch(line):
            raise AssertionError(f"woodpecker.nix cannot read {name} line {line!r}")
    return entries


class AuthorityPrefixesTest(unittest.TestCase):
    def test_nix_reads_every_protected_prefix_snapshot_uses(self) -> None:
        prefixes = (
            nix_tuple(ROOT / "scripts/ci/deployment_risk.py", "RUNTIME_PATHS")
            + nix_tuple(ROOT / "scripts/verifier/enforcement.py", "ENFORCEMENT_PATHS")
            + nix_tuple(ROOT / "scripts/verifier/enforcement.py", "DATA_PATHS")
        )
        self.assertEqual(tuple(prefixes), deployment_risk.ALL_PATHS)
        self.assertEqual(
            deployment_risk.ALL_PATHS,
            deployment_risk.RUNTIME_PATHS
            + enforcement.ENFORCEMENT_PATHS
            + enforcement.DATA_PATHS,
        )

    def test_woodpecker_baseline_reads_the_narrowed_source(self) -> None:
        module = (ROOT / "flake/modules/woodpecker.nix").read_text()
        self.assertIn("deployment_risk ${authoritySource} >", module)
        self.assertNotIn("${../..}", module)


@unittest.skipIf(os.getenv("SKIP_NIX_EVAL") == "1", "Nix evaluation disabled")
@unittest.skipUnless(shutil.which("nix"), "Nix required")
class AuthoritySourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        expression = """
          let
            flake = builtins.getFlake __FLAKE__;
            root = flake.sourceInfo.outPath;
            module = import (root + "/flake/modules/woodpecker.nix") {
              config.homelab.woodpecker = { };
              lib = flake.inputs.nixpkgs.lib;
              pkgs = { };
            };
            authority = module.options.homelab.woodpecker.authority.default;
          in {
            root = toString root;
            source = toString authority.source;
            prefixes = authority.prefixes;
          }
        """.replace("__FLAKE__", json.dumps(f"git+file://{ROOT}?dir=flake"))
        result = subprocess.run(
            ["nix", "eval", "--impure", "--json", "--expr", expression],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr)
        cls.evaluated = json.loads(result.stdout)

    def test_prefixes_match_snapshot(self) -> None:
        self.assertEqual(tuple(self.evaluated["prefixes"]), deployment_risk.ALL_PATHS)

    def test_narrowed_source_yields_the_whole_tree_baseline(self) -> None:
        whole = deployment_risk.snapshot(Path(self.evaluated["root"]))
        narrowed = deployment_risk.snapshot(Path(self.evaluated["source"]))
        self.assertTrue(any(path.endswith(".nix") for path in whole))
        self.assertIn("scripts/verifier/enforcement.py", whole)
        self.assertEqual(
            json.dumps(narrowed, sort_keys=True), json.dumps(whole, sort_keys=True)
        )

    def test_narrowed_source_holds_nothing_else(self) -> None:
        source = Path(self.evaluated["source"])
        files = {
            path.relative_to(source).as_posix()
            for path in source.rglob("*")
            if path.is_file() or path.is_symlink()
        }
        self.assertEqual(files, set(deployment_risk.snapshot(source)))
        self.assertFalse(any(name.startswith("docs/") for name in files))


if __name__ == "__main__":
    unittest.main()
