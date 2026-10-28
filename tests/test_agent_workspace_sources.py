from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILTER = ROOT / "flake/tests/agent-workspace-python-source.nix"


@unittest.skipUnless(shutil.which("nix"), "requires Nix source evaluation")
class WorkspaceSourcesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source = subprocess.check_output(
            [
                "nix",
                "eval",
                "--offline",
                "--impure",
                "--raw",
                "--expr",
                "(builtins.getFlake (toString ./flake)).inputs.nixpkgs.outPath",
            ],
            cwd=ROOT,
            text=True,
            timeout=120,
        )
        cls.lib = Path(source) / "lib"

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / "repository"
        self.relevant = {
            "scripts/__init__.py": "",
            "scripts/agent_workspaces/__init__.py": "",
            "scripts/agent_workspaces/core.py": "SCHEMA_VERSION = 1\n",
            "scripts/agent_workspaces/lifecycle.py": "from .core import SCHEMA_VERSION\n",
            "scripts/agent_workspaces/data/contract.json": '{"version": 1}\n',
        }
        for name, contents in {
            **self.relevant,
            "scripts/voice_gateway.py": "VOICE = 1\n",
            "docs/README.md": "documentation\n",
            ".git/config": "metadata\n",
            "scripts/agent_workspaces/__pycache__/core.pyc": "bytecode\n",
            "scripts/agent_workspaces/orphan.pyc": "bytecode\n",
        }.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents)
        self.expression = self.directory / "source.nix"
        self.expression.write_text(
            "let\n"
            f"  lib = import (builtins.toPath {json.dumps(str(self.lib))});\n"
            f"  root = builtins.toPath {json.dumps(str(self.root))};\n"
            f"in toString (import (builtins.toPath {json.dumps(str(FILTER))}) {{ inherit lib root; }})\n"
        )

    def source(self) -> Path:
        return Path(
            subprocess.check_output(
                [
                    "nix",
                    "eval",
                    "--offline",
                    "--impure",
                    "--raw",
                    "--file",
                    str(self.expression),
                ],
                text=True,
                timeout=30,
            )
        )

    def test_source_contains_package_initializers_and_data_only(self) -> None:
        source = self.source()
        files = {
            str(path.relative_to(source))
            for path in source.rglob("*")
            if path.is_file()
        }
        self.assertEqual(files, set(self.relevant))
        for name, contents in self.relevant.items():
            self.assertEqual((source / name).read_text(), contents)

    def test_unrelated_changes_preserve_source_identity(self) -> None:
        before = self.source()
        for name in (
            "scripts/voice_gateway.py",
            "docs/README.md",
            ".git/config",
            "scripts/agent_workspaces/__pycache__/core.pyc",
            "scripts/agent_workspaces/orphan.pyc",
        ):
            (self.root / name).write_text("unrelated edit\n")
        (self.root / "scripts/new_unrelated.py").write_text("new unrelated script\n")
        self.assertEqual(self.source(), before)

    def test_every_package_input_and_new_module_invalidates_source(self) -> None:
        before = self.source()
        for name, contents in self.relevant.items():
            with self.subTest(path=name):
                path = self.root / name
                path.write_text(contents + "\n")
                self.assertNotEqual(self.source(), before)
                path.write_text(contents)
        new_module = self.root / "scripts/agent_workspaces/new_module.py"
        new_module.write_text("NEW = 1\n")
        self.assertNotEqual(self.source(), before)
        new_module.unlink()
        self.assertEqual(self.source(), before)


if __name__ == "__main__":
    unittest.main()
