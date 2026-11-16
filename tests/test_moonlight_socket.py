import json
import os
import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(os.getenv("SKIP_NIX_EVAL") == "1", "Nix evaluation disabled")
@unittest.skipUnless(shutil.which("nix"), "Nix required")
class MoonlightSocketTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        expression = (
            f"(builtins.getFlake {json.dumps(f'git+file://{ROOT}?dir=flake')})"
            ".nixosConfigurations.homelab-05.config.systemd.services.moonlight-stream.script"
        )
        result = subprocess.run(
            ["nix", "eval", "--impure", "--json", "--expr", expression],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        script = json.loads(result.stdout)
        cls.script = (
            script[: script.rfind("exec ")] + 'test "$WAYLAND_DISPLAY" = wayland-0\n'
        )

    def test_accepts_a_real_socket_and_rejects_an_absent_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "XDG_RUNTIME_DIR": directory}
            command = ["bash", "-c", "sleep() { :; };\n" + self.script]
            missing = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("Wayland socket did not appear", missing.stderr)
            with socket.socket(socket.AF_UNIX) as sock:
                sock.bind(str(Path(directory) / "wayland-0"))
                present = subprocess.run(
                    command, env=env, capture_output=True, text=True
                )
            self.assertEqual(present.returncode, 0, present.stderr)
