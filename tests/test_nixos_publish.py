from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


class NixosPublishTests(unittest.TestCase):
    def run_publish(
        self, clone_sha: str
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "scripts/ci"
            scripts.mkdir(parents=True)
            shutil.copy(ROOT / "scripts/ci/nixos_publish.sh", scripts)
            (scripts / "cache.sh").write_text(
                'printf "%s\\n" "$2" > "$RECEIPT/cache"\n'
            )
            bin_dir = root / "bin"
            bin_dir.mkdir()
            python = bin_dir / "python3"
            python.write_text(f"#!{shutil.which('bash')}\nprintf '%s\\n' '{SHA}'\n")
            python.chmod(0o700)
            real_git = shutil.which("git")
            self.assertIsNotNone(real_git)
            git = bin_dir / "git"
            git.write_text(
                f"#!{sys.executable}\n"
                "import json, os, pathlib, subprocess, sys\n"
                "args = sys.argv[1:]\n"
                "receipt = pathlib.Path(os.environ['RECEIPT'])\n"
                "if 'clone' in args:\n"
                "    url = args[-2]\n"
                "    assert url == 'https://git.rupan.dev/JEFF7712/nixos-config.git'\n"
                f"    credentials = subprocess.check_output([{real_git!r}, '-c', "
                "'credential.helper=', 'credential', 'fill'], "
                "input='url=' + url + '\\n\\n', text=True)\n"
                "    assert 'username=homelab-ci-source\\n' in credentials\n"
                "    assert 'password=test-source-token\\n' in credentials\n"
                "    assert os.environ['GIT_TERMINAL_PROMPT'] == '0'\n"
                "    (receipt / 'clone').write_text(json.dumps({'url': url, 'authenticated': True}))\n"
                "    pathlib.Path(args[-1]).mkdir()\n"
                "else:\n"
                "    assert args[-2:] == ['rev-parse', 'HEAD']\n"
                "    print(os.environ['CLONE_SHA'])\n"
            )
            git.chmod(0o700)
            env = {
                **os.environ,
                "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                "FORGEJO_SOURCE_READ_TOKEN": "test-source-token",
                "RECEIPT": str(root),
                "CLONE_SHA": clone_sha,
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
            }
            result = subprocess.run(
                ["bash", str(scripts / "nixos_publish.sh")],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertTrue((root / "clone").exists(), result.stderr)
            receipts = {
                "clone": json.loads((root / "clone").read_text()),
                "cache": (root / "cache").read_text()
                if (root / "cache").exists()
                else None,
            }
            return result, receipts

    def test_authenticated_clone_publishes_verified_tip(self) -> None:
        result, receipts = self.run_publish(SHA)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(receipts["clone"]["authenticated"])
        self.assertEqual(receipts["cache"], "nixos-laptop\n")
        self.assertIn(f"published nixos-config {SHA}", result.stdout)
        self.assertNotIn("test-source-token", result.stdout + result.stderr)

    def test_changed_source_tip_cannot_publish(self) -> None:
        result, receipts = self.run_publish("b" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match resolved tip", result.stderr)
        self.assertIsNone(receipts["cache"])


if __name__ == "__main__":
    unittest.main()
