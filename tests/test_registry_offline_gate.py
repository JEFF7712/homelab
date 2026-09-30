from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class RegistryOfflineGateTest(unittest.TestCase):
    def test_offline_gate_never_refreshes_or_contacts_cluster(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checks = root / "scripts/checks"
            checks.mkdir(parents=True)
            shutil.copy2(source / "scripts/checks/registry.sh", checks)
            (checks / "registry-refresh.sh").write_text("exit 89\n")
            tools = root / "bin"
            tools.mkdir()
            log = root / "commands"
            python = tools / "python"
            python.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$COMMAND_LOG"\n')
            python.chmod(0o755)
            for name in ("kubectl", "sudo"):
                tool = tools / name
                tool.write_text("#!/bin/sh\nexit 88\n")
                tool.chmod(0o755)
            result = subprocess.run(
                ["bash", str(checks / "registry.sh")],
                env={
                    **os.environ,
                    "PATH": f"{tools}:{os.environ['PATH']}",
                    "COMMAND_LOG": str(log),
                },
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                log.read_text().splitlines(),
                [
                    "-m scripts.registry check --lock registry/images.lock.json",
                    "-m scripts.registry plan --lock registry/images.lock.json",
                ],
            )
