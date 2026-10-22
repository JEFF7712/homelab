from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "flake/modules/woodpecker-clone-retry.sh"


class CloneRetryTest(unittest.TestCase):
    def run_clone(
        self, failures: list[str]
    ) -> tuple[subprocess.CompletedProcess[str], int]:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            clone = directory / "clone"
            clone.write_text(
                "#!/bin/sh\n"
                'count=$(cat "$COUNT" 2>/dev/null || printf 0)\n'
                'count=$((count + 1)); printf %s "$count" > "$COUNT"\n'
                'message=$(sed -n "${count}p" "$FAILURES")\n'
                'if [ -n "$message" ]; then printf "%s\\n" "$message"; exit 128; fi\n'
                'printf "clone complete\\n"\n'
            )
            clone.chmod(0o755)
            sleep = directory / "sleep"
            sleep.write_text("#!/bin/sh\nexit 0\n")
            sleep.chmod(0o755)
            (directory / "failures").write_text("\n".join(failures) + "\n")
            environment = dict(
                os.environ,
                PATH=str(directory) + os.pathsep + os.environ["PATH"],
                COUNT=str(directory / "count"),
                FAILURES=str(directory / "failures"),
            )
            result = subprocess.run(
                ["sh", str(SCRIPT), str(clone)],
                env=environment,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            return result, int((directory / "count").read_text())

    def test_restarts_clone_after_transient_forgejo_outage(self) -> None:
        result, attempts = self.run_clone(
            ["fatal: The requested URL returned error: 502", "Failed to connect"]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(attempts, 3)
        self.assertIn("clone complete", result.stdout)

    def test_retries_are_bounded(self) -> None:
        result, attempts = self.run_clone(["Connection refused"] * 5)
        self.assertEqual(result.returncode, 128)
        self.assertEqual(attempts, 3)

    def test_permanent_errors_fail_without_retry(self) -> None:
        for message in (
            "fatal: Authentication failed",
            "fatal: The requested URL returned error: 401",
            "fatal: The requested URL returned error: 403",
            "fatal: The requested URL returned error: 404",
            "SSL certificate problem: certificate has expired",
            "fatal: couldn't find remote ref missing",
        ):
            with self.subTest(message=message):
                result, attempts = self.run_clone([message])
                self.assertEqual(result.returncode, 128)
                self.assertEqual(attempts, 1)
