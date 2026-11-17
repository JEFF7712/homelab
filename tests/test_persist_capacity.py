"""Tests for homelab-04 /persist capacity: usage metrics and Nix retention."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "flake" / "modules" / "persist_usage.py"


def load_module():
    spec = importlib.util.spec_from_file_location("persist_usage", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


persist_usage = load_module()
GIB = 1 << 30


class SelectionTest(unittest.TestCase):
    def test_keeps_children_and_only_large_grandchildren(self) -> None:
        rows = persist_usage.parse_du(
            b"".join(
                f"{size}\t{path}\0".encode()
                for path, size in [
                    ("/persist/docker/containerd", 40 * GIB),
                    ("/persist/docker/volumes", 4096),
                    ("/persist/docker", 41 * GIB),
                    ("/persist/tor", 8192),
                    ("/persist", 700 * GIB),
                ]
            )
        )
        self.assertEqual(
            persist_usage.select(rows, "/persist", GIB),
            [
                ("/persist", 700 * GIB),
                ("/persist/docker", 41 * GIB),
                ("/persist/docker/containerd", 40 * GIB),
                ("/persist/tor", 8192),
            ],
        )

    def test_tree_argument_defaults_to_total_only(self) -> None:
        self.assertEqual(
            persist_usage.parse_tree("/nix/store"), persist_usage.Tree("/nix/store", 0)
        )
        self.assertEqual(
            persist_usage.parse_tree("/persist/:2"), persist_usage.Tree("/persist", 2)
        )

    def test_labels_are_escaped(self) -> None:
        body = persist_usage.render([('/persist/a"b\\c', 1)], None, 0, 0.5, 10)
        self.assertIn('path="/persist/a\\"b\\\\c"} 1', body)
        self.assertNotIn("homelab_nix_system_generations", body)


class CollectorTest(unittest.TestCase):
    def test_writes_usage_generations_and_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            persist = root / "persist"
            (persist / "big" / "nested").mkdir(parents=True)
            (persist / "big" / "nested" / "blob").write_bytes(os.urandom(64 * 1024))
            (persist / "small").mkdir()
            profiles = root / "profiles"
            profiles.mkdir()
            for number in (1, 2, 3):
                (profiles / f"system-{number}-link").symlink_to("/nonexistent")
            (profiles / "system").symlink_to("system-3-link")
            output = root / "metrics"

            persist_usage.main(
                [
                    "--tree",
                    f"{persist}:2",
                    "--tree",
                    f"{root / 'missing'}:0",
                    "--min-bytes",
                    "32768",
                    "--profiles",
                    str(profiles),
                    "--output",
                    str(output),
                ]
            )

            body = (output / persist_usage.OUTPUT_NAME).read_text()
            sizes = {
                line.split('"')[1]: int(line.rsplit(" ", 1)[1])
                for line in body.splitlines()
                if line.startswith("homelab_path_disk_usage_bytes{")
            }
            self.assertEqual(
                set(sizes),
                {
                    str(persist),
                    f"{persist}/big",
                    f"{persist}/small",
                    f"{persist}/big/nested",
                },
            )
            self.assertGreaterEqual(sizes[f"{persist}/big/nested"], 64 * 1024)
            self.assertIn("homelab_nix_system_generations 3\n", body)
            # A missing path is skipped, not a failure.
            self.assertIn("homelab_path_disk_usage_failures 0\n", body)
            self.assertEqual(
                oct((output / persist_usage.OUTPUT_NAME).stat().st_mode & 0o777),
                "0o644",
            )
            self.assertEqual(
                [p.name for p in output.iterdir()], [persist_usage.OUTPUT_NAME]
            )

    def test_failed_walk_is_counted_not_raised(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            original = persist_usage.subprocess.run
            persist_usage.subprocess.run = lambda *a, **k: subprocess.CompletedProcess(
                a, 2, b"", b"du: boom"
            )
            try:
                persist_usage.main(
                    ["--tree", f"{temporary}:1", "--output", f"{temporary}/out"]
                )
            finally:
                persist_usage.subprocess.run = original
            body = Path(temporary, "out", persist_usage.OUTPUT_NAME).read_text()
            self.assertIn("homelab_path_disk_usage_failures 1\n", body)


@unittest.skipIf(os.getenv("SKIP_NIX_EVAL") == "1", "Nix evaluation disabled")
@unittest.skipUnless(shutil.which("nix"), "Nix required")
class HomelabFourRetentionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        expression = """
          let
            flake = builtins.getFlake __FLAKE__;
            config = flake.nixosConfigurations.homelab-04.config;
            other = flake.nixosConfigurations.homelab-01.config;
            gc = config.systemd.services.nix-gc;
            usage = config.systemd.services.persist-usage-metrics;
          in {
            gcStartAt = gc.startAt;
            gcOptions = config.nix.gc.options;
            gcPreStart = gc.preStart;
            minFree = config.nix.settings.min-free;
            maxFree = config.nix.settings.max-free;
            exporterFlags = config.services.prometheus.exporters.node.extraFlags;
            collectors = config.services.prometheus.exporters.node.enabledCollectors;
            usageExec = usage.serviceConfig.ExecStart;
            usageTimer = config.systemd.timers.persist-usage-metrics.timerConfig.OnCalendar;
            otherStartAt = other.systemd.services.nix-gc.startAt;
            otherPreStart = other.systemd.services.nix-gc.preStart or "";
          }
        """.replace("__FLAKE__", json.dumps(f"git+file://{ROOT}?dir=flake"))
        result = subprocess.run(
            ["nix", "eval", "--impure", "--json", "--expr", expression],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr)
        cls.config = json.loads(result.stdout)

    def test_collects_daily_and_keeps_twenty_system_generations(self) -> None:
        self.assertEqual(self.config["gcStartAt"], ["daily"])
        self.assertEqual(self.config["gcOptions"], "--delete-older-than 30d")
        self.assertIn(
            "--profile /nix/var/nix/profiles/system", self.config["gcPreStart"]
        )
        self.assertIn("--delete-generations +20", self.config["gcPreStart"])

    def test_store_collects_itself_under_pressure(self) -> None:
        self.assertEqual(self.config["minFree"], 32 * GIB)
        self.assertEqual(self.config["maxFree"], 64 * GIB)

    def test_usage_metrics_reach_the_host_exporter(self) -> None:
        self.assertIn("textfile", self.config["collectors"])
        self.assertIn(
            "--collector.textfile.directory=/var/lib/persist-usage-metrics",
            self.config["exporterFlags"],
        )
        self.assertIn("persist_usage.py", self.config["usageExec"])
        self.assertIn("--tree /persist:2 ", self.config["usageExec"])
        self.assertEqual(self.config["usageTimer"], "*-*-* 00/2:17:00")

    def test_other_hosts_keep_the_fleet_policy(self) -> None:
        self.assertEqual(self.config["otherStartAt"], ["weekly"])
        self.assertNotIn("delete-generations", self.config["otherPreStart"])


if __name__ == "__main__":
    unittest.main()
