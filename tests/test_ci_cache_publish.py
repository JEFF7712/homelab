from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = "/nix/store/" + "a" * 32 + "-nixos-iso"
DEPENDENCY = "/nix/store/" + "b" * 32 + "-dependency"

STUBS = {
    "nix": f"""
case "$1" in
  build) printf '%s\\n' {OUTPUT} ;;
  flake) printf '{{}}\\n' ;;
  path-info) printf '%s\\n' /nix/store/{"c" * 32}-nixos-iso.drv ;;
esac
""",
    "nix-store": f"printf '%s\\n' {OUTPUT} {DEPENDENCY}\n",
    "python": "exit 0\n",
    "attic": 'if [ "$1" = push ]; then cat >> "$RECEIPT/attic"; fi\n',
    "niks3": """
token=""
previous=""
for argument in "$@"; do
  [ "$previous" = --auth-token-path ] && token=$(cat "$argument")
  previous=$argument
done
printf '%s' "$token" > "$RECEIPT/niks3-token"
while read -r path; do
  printf '%s\\n' "$path" >> "$RECEIPT/niks3"
  if [ "$NIKS3_MODE" = error ] && [ "$path" = "$FAIL_PATH" ]; then
    printf '{"path":"%s","status":"error","message":"upload refused"}\\n' "$path"
  else
    printf '{"path":"%s","status":"ok"}\\n' "$path"
  fi
done
[ "$NIKS3_MODE" = crash ] && exit 3
exit 0
""",
}


class CachePublishTests(unittest.TestCase):
    def publish(
        self, niks3_token: str | None, mode: str = "ok"
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, str | None]]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts/ci").mkdir(parents=True)
            shutil.copy(ROOT / "scripts/ci/cache.sh", root / "scripts/ci")
            receipt = root / "receipt"
            receipt.mkdir()
            bin_dir = root / "bin"
            bin_dir.mkdir()
            for name, body in STUBS.items():
                stub = bin_dir / name
                stub.write_text(f"#!{shutil.which('bash')}\n{body}")
                stub.chmod(0o700)
            env = {
                **os.environ,
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "HOMELAB_CACHE_BUILDER": "native",
                "ATTIC_TOKEN": "test-attic-token",
                "RECEIPT": str(receipt),
                "NIKS3_MODE": mode,
                "FAIL_PATH": DEPENDENCY,
                "TMPDIR": directory,
            }
            env.pop("NIKS3_TOKEN", None)
            if niks3_token is not None:
                env["NIKS3_TOKEN"] = niks3_token
            result = subprocess.run(
                ["bash", str(root / "scripts/ci/cache.sh"), "./flake", "nixos-iso"],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            receipts = {
                name: (receipt / name).read_text()
                if (receipt / name).exists()
                else None
                for name in ["attic", "niks3", "niks3-token"]
            }
            return result, receipts

    def test_pushes_every_unique_path_to_attic_and_niks3(self) -> None:
        result, receipts = self.publish("test-niks3-token")
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = f"{OUTPUT}\n{DEPENDENCY}\n"
        self.assertEqual(receipts["attic"], expected)
        self.assertEqual(receipts["niks3"], expected)
        self.assertEqual(receipts["niks3-token"], "test-niks3-token")
        self.assertIn("niks3 push: ok=2", result.stdout)
        self.assertNotIn("test-niks3-token", result.stdout + result.stderr)

    def test_niks3_path_error_fails_after_attic_push(self) -> None:
        result, receipts = self.publish("test-niks3-token", mode="error")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(receipts["attic"], f"{OUTPUT}\n{DEPENDENCY}\n")
        self.assertIn("niks3 failed 1 path(s) (exit 0)", result.stderr)
        self.assertIn(f"{DEPENDENCY}: upload refused", result.stderr)

    def test_niks3_exit_status_fails_without_path_errors(self) -> None:
        result, _ = self.publish("test-niks3-token", mode="crash")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("niks3 failed 0 path(s) (exit 3)", result.stderr)

    def test_missing_niks3_token_keeps_attic_only_publication(self) -> None:
        result, receipts = self.publish(None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(receipts["attic"], f"{OUTPUT}\n{DEPENDENCY}\n")
        self.assertIsNone(receipts["niks3"])
        self.assertIn("NIKS3_TOKEN is not set", result.stdout)


if __name__ == "__main__":
    unittest.main()
