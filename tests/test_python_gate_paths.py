import re
import shlex
import shutil
import subprocess
import unittest
from pathlib import Path

from scripts.agent.checks import PYTHON_GITOPS_PREFIXES
from scripts.ci.scope import _PYTHON_GITOPS_PREFIXES

ROOT = Path(__file__).resolve().parents[1]
PYTHON_SH = ROOT / "scripts/checks/python.sh"


def _bash_array(name: str) -> list[str]:
    text = PYTHON_SH.read_text(encoding="utf-8")
    match = re.search(rf"^{name}=\((.*?)^\)", text, re.MULTILINE | re.DOTALL)
    if match is None:
        raise AssertionError(f"{name} array not found in {PYTHON_SH}")
    lines = [line.split("#", 1)[0] for line in match.group(1).splitlines()]
    return shlex.split(" ".join(lines))


class PythonGatePathsTest(unittest.TestCase):
    def test_justfile_formats_the_paths_python_sh_lints(self) -> None:
        just = shutil.which("just")
        if just is None:
            self.skipTest("just is not installed")
        evaluated = subprocess.run(
            [just, "--justfile", str(ROOT / "justfile"), "--evaluate", "python_paths"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        self.assertEqual(evaluated, _bash_array("ruff_paths"))

    def test_gate_paths_exist(self) -> None:
        for path in (
            _bash_array("ruff_paths")
            + _bash_array("ruff_lint_only_paths")
            + _bash_array("pyright_paths")
        ):
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).exists())

    def test_lint_only_paths_are_not_formatted(self) -> None:
        formatted = _bash_array("ruff_paths")
        for path in _bash_array("ruff_lint_only_paths"):
            with self.subTest(path=path):
                self.assertNotIn(path, formatted)

    def test_routed_gitops_python_is_linted_and_type_checked(self) -> None:
        self.assertEqual(PYTHON_GITOPS_PREFIXES, _PYTHON_GITOPS_PREFIXES)
        ruff = _bash_array("ruff_paths") + _bash_array("ruff_lint_only_paths")
        pyright = _bash_array("pyright_paths")
        for prefix in PYTHON_GITOPS_PREFIXES:
            with self.subTest(prefix=prefix):
                self.assertTrue(
                    any(prefix.startswith(path.rstrip("/") + "/") for path in ruff),
                    f"{prefix} is not under a ruff path",
                )
                scripts = sorted((ROOT / prefix).glob("*.py"))
                self.assertTrue(scripts)
                for script in scripts:
                    self.assertIn(str(script.relative_to(ROOT)), pyright)


if __name__ == "__main__":
    unittest.main()
