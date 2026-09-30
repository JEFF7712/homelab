from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from typing import Self

from scripts.registry.gc_fixture import (
    DisposableRegistry,
    build_config,
    build_image,
    free_port,
    write_auth_file,
    write_htpasswd,
)


class ConfigurableRegistry(DisposableRegistry):
    def __init__(
        self,
        binary: str,
        root: pathlib.Path,
        port: int,
        custom_cfg: dict[str, object],
    ) -> None:
        super().__init__(binary, root, port)
        self.custom_cfg = custom_cfg

    def __enter__(self) -> Self:
        self.root.mkdir(parents=True, exist_ok=True)
        write_htpasswd(self.htpasswd, self.user, self.password)
        write_auth_file(self.auth_file, self.host, self.user, self.password)
        self.config_path.write_text(json.dumps(self.custom_cfg), encoding="utf-8")
        self._log = (self.root / "zot.log").open("ab")
        self.process = subprocess.Popen(
            [self.binary, "serve", str(self.config_path)],
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )
        self._await_ready()
        return self


class ZotFastRestartTests(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("zot") is None:
            raise unittest.SkipTest("zot is required on PATH")
        self.workspace = pathlib.Path(tempfile.mkdtemp(prefix="fast-restart-test-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_fast_restart_skips_full_storage_parse_on_subsequent_runs(self) -> None:
        layouts = self.workspace / "layouts"
        layouts.mkdir()
        build_image(layouts / "img1", b"payload1")

        port = free_port()
        reg_dir = self.workspace / "registry"
        reg_dir.mkdir()
        htpasswd = reg_dir / "htpasswd"
        write_htpasswd(htpasswd, "fixture", "fixturepass")

        cfg = build_config(reg_dir / "storage", htpasswd, port, "fixture")
        cfg["log"]["level"] = "info"
        cfg["storage"]["fastRestart"] = True

        # First run: initializes meta.db and writes fast-restart stamp
        with ConfigurableRegistry("zot", reg_dir, port, cfg) as reg:
            reg.push(layouts / "img1", "test/app", "v1")
            self.assertTrue(reg.manifest_exists("test/app", "v1"))

        first_log = (reg_dir / "zot.log").read_text(errors="replace")
        self.assertIn("parsing storage and initializing", first_log)

        # Second run: stamp matches, skipping full parse
        with ConfigurableRegistry("zot", reg_dir, port, cfg) as reg:
            self.assertTrue(reg.manifest_exists("test/app", "v1"))

        second_log = (reg_dir / "zot.log").read_text(errors="replace")
        self.assertIn(
            "metaDB fast-restart stamp matches, skipping full storage parse",
            second_log,
        )

    def test_disabled_fast_restart_always_reparses_storage(self) -> None:
        layouts = self.workspace / "layouts"
        layouts.mkdir()
        build_image(layouts / "img1", b"payload1")

        port = free_port()
        reg_dir = self.workspace / "registry"
        reg_dir.mkdir()
        htpasswd = reg_dir / "htpasswd"
        write_htpasswd(htpasswd, "fixture", "fixturepass")

        cfg = build_config(reg_dir / "storage", htpasswd, port, "fixture")
        cfg["log"]["level"] = "info"
        cfg["storage"]["fastRestart"] = False

        with ConfigurableRegistry("zot", reg_dir, port, cfg) as reg:
            reg.push(layouts / "img1", "test/app", "v1")

        (reg_dir / "zot.log").unlink()

        with ConfigurableRegistry("zot", reg_dir, port, cfg) as reg:
            self.assertTrue(reg.manifest_exists("test/app", "v1"))

        second_log = (reg_dir / "zot.log").read_text(errors="replace")
        self.assertIn("parsing storage and initializing", second_log)
        self.assertNotIn(
            "metaDB fast-restart stamp matches, skipping full storage parse",
            second_log,
        )


if __name__ == "__main__":
    unittest.main()
