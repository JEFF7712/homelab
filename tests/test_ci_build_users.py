from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.ci.build_users import GID, GROUP, missing_entries

ROOT = Path(__file__).resolve().parents[1]
IMAGE_PASSWD = (
    "root:x:0:0:System administrator:/root:/bin/bash\n"
    "nixbld1:x:30001:30000:Nix build user 1:/var/empty:/bin/false\n"
)
IMAGE_GROUP = "root:x:0:\nnixbld:x:30000:nixbld1\n"


class BuildUsersTest(unittest.TestCase):
    def test_users_avoid_host_build_uids(self) -> None:
        passwd, group = missing_entries(IMAGE_PASSWD, IMAGE_GROUP, 3)
        uids = [int(line.split(":")[2]) for line in passwd]
        self.assertEqual(uids, [GID + 1, GID + 2, GID + 3])
        # Host nix-daemons own 30001+ and kill those UIDs around their builds.
        self.assertTrue(all(uid > 30100 for uid in uids))
        self.assertEqual(group, [f"{GROUP}:x:{GID}:{GROUP}1,{GROUP}2,{GROUP}3"])

    def test_cli_appends_once_and_prints_setting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            passwd = Path(temporary) / "passwd"
            group = Path(temporary) / "group"
            passwd.write_text(IMAGE_PASSWD)
            group.write_text(IMAGE_GROUP.rstrip("\n"))
            for _ in range(2):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "scripts.ci.build_users",
                        "--passwd",
                        str(passwd),
                        "--group",
                        str(group),
                        "--count",
                        "2",
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                self.assertEqual(result.stdout, f"build-users-group = {GROUP}\n")
            self.assertEqual(passwd.read_text().count(f"{GROUP}1:"), 1)
            self.assertEqual(passwd.read_text().count(f"{GROUP}2:"), 1)
            self.assertEqual(
                group.read_text().splitlines(),
                [
                    "root:x:0:",
                    "nixbld:x:30000:nixbld1",
                    f"{GROUP}:x:{GID}:cibld1,cibld2",
                ],
            )


class CacheNixConfigTest(unittest.TestCase):
    def test_cache_build_falls_back_to_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            (repo / "scripts/ci").mkdir(parents=True)
            shutil.copy(ROOT / "scripts/ci/cache.sh", repo / "scripts/ci")
            binaries = repo / "bin"
            binaries.mkdir()
            nix = binaries / "nix"
            nix.write_text(
                f'#!{shutil.which("bash")}\nprintf "%s" "$NIX_CONFIG" > "$CONFIG_LOG"\n'
            )
            nix.chmod(0o755)
            env = dict(
                os.environ,
                PATH=f"{binaries}:{os.environ['PATH']}",
                ATTIC_TOKEN="",
                HOMELAB_CACHE_BUILDER="native",
                NIX_CONFIG="sandbox = false",
                CONFIG_LOG=str(repo / "config.log"),
                TMPDIR=temporary,
            )
            subprocess.run(
                ["bash", "scripts/ci/cache.sh"],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            lines = (repo / "config.log").read_text().splitlines()
            self.assertEqual(lines[0], "sandbox = false")
            self.assertIn("fallback = true", lines)


if __name__ == "__main__":
    unittest.main()
