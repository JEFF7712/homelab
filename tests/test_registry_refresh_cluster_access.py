from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


def _run_refresh(
    source: Path, root: Path, *, ci: str
) -> subprocess.CompletedProcess[str]:
    """Run the real refresh script against kubectl/sudo stubs that reach nothing."""
    checks = root / "scripts/checks"
    checks.mkdir(parents=True)
    shutil.copy2(source / "scripts/checks/registry-refresh.sh", checks)
    tools = root / "bin"
    tools.mkdir()
    for name in ("kubectl", "sudo"):
        tool = tools / name
        tool.write_text("#!/bin/sh\nexit 88\n")
        tool.chmod(0o755)
    return subprocess.run(
        ["bash", str(checks / "registry-refresh.sh")],
        env={
            **os.environ,
            "PATH": f"{tools}:{os.environ['PATH']}",
            "CI": ci,
        },
        capture_output=True,
        text=True,
        check=False,
    )


class RegistryRefreshClusterAccessTest(unittest.TestCase):
    """An unreachable cluster must not let CI report a check it never performed.

    The check reads registry/observed-images.json to decide whether every image
    still running is pinned, so falling back to the committed copy turns the
    verdict into a claim about the last commit. That is how apps/quartz stayed
    green while the cluster ran a digest the lock no longer described.
    """

    def test_unreachable_cluster_warns_when_not_in_ci(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            result = _run_refresh(source, Path(directory), ci="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARNING: no reachable cluster", result.stderr)

    def test_unreachable_cluster_fails_in_ci(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            result = _run_refresh(source, Path(directory), ci="true")
        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR: no reachable cluster in CI", result.stderr)

    def test_ci_failure_names_the_remedy(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            result = _run_refresh(source, Path(directory), ci="true")
        self.assertIn("KUBECONFIG", result.stderr)
