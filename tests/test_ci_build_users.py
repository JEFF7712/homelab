from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.ci.build_users import (
    COUNT,
    GID,
    GROUP,
    NIX_CONF,
    NIX_SETTING,
    entries,
    setup_command,
)

ROOT = Path(__file__).resolve().parents[1]
IMAGE_PASSWD = (
    "root:x:0:0:System administrator:/root:/bin/bash\n"
    "nixbld1:x:30001:30000:Nix build user 1:/var/empty:/bin/false\n"
)
IMAGE_GROUP = "root:x:0:\nnixbld:x:30000:nixbld1\n"
# The nixos/nix image's nix.conf, from Nix's docker.nix.
IMAGE_NIX_CONF = (
    "build-users-group = nixbld\n"
    "sandbox = false\n"
    "trusted-public-keys = cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY=\n"
)


class BuildUsersTest(unittest.TestCase):
    def test_users_avoid_host_build_uids(self) -> None:
        passwd, group = entries(3)
        uids = [int(line.split(":")[2]) for line in passwd]
        self.assertEqual(uids, [GID + 1, GID + 2, GID + 3])
        # Host nix-daemons own 30001+ and kill those UIDs around their builds.
        self.assertTrue(all(uid > 30100 for uid in uids))
        self.assertEqual(group, f"{GROUP}:x:{GID}:{GROUP}1,{GROUP}2,{GROUP}3")
        self.assertEqual(NIX_SETTING, f"build-users-group = {GROUP}")

    def test_setup_command_survives_woodpecker_substitution(self) -> None:
        # Woodpecker expands $VAR and ${VAR} in step commands.
        self.assertNotIn("$", setup_command())

    def run_setup(self, directory: Path, times: int = 1) -> None:
        command = setup_command().replace(NIX_CONF, str(directory / "nix.conf"))
        command = command.replace("/etc/passwd", str(directory / "passwd"))
        command = command.replace("/etc/group", str(directory / "group"))
        for _ in range(times):
            subprocess.run(["sh", "-c", command], check=True)

    def test_setup_command_adds_the_users(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            passwd = Path(temporary) / "passwd"
            group = Path(temporary) / "group"
            passwd.write_text(IMAGE_PASSWD)
            group.write_text(IMAGE_GROUP)
            (Path(temporary) / "nix.conf").write_text(IMAGE_NIX_CONF)
            self.run_setup(Path(temporary))
            expected_passwd, expected_group = entries()
            self.assertEqual(
                passwd.read_text().splitlines(),
                IMAGE_PASSWD.splitlines() + expected_passwd,
            )
            self.assertEqual(len(expected_passwd), COUNT)
            self.assertEqual(
                group.read_text().splitlines(),
                IMAGE_GROUP.splitlines() + [expected_group],
            )

    def test_setup_command_makes_cibld_the_image_build_users_group(self) -> None:
        # A root Nix that loses NIX_CONFIG reads this file and re-chowns
        # /nix/store to its group, so it must name cibld, not nixbld.
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "passwd").write_text(IMAGE_PASSWD)
            (directory / "group").write_text(IMAGE_GROUP)
            (directory / "nix.conf").write_text(IMAGE_NIX_CONF)
            self.run_setup(directory, times=2)
            lines = (directory / "nix.conf").read_text().splitlines()
            self.assertEqual(
                [line for line in lines if line.startswith("build-users-group")],
                [NIX_SETTING],
            )
            self.assertEqual(
                [line for line in lines if not line.startswith("build-users-group")],
                IMAGE_NIX_CONF.splitlines()[1:],
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
                NIKS3_TOKEN="",
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
            self.assertEqual(lines, ["sandbox = false", "fallback = true"])


if __name__ == "__main__":
    unittest.main()
