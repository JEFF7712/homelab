"""Kubelet image garbage-collection thresholds per k3s node."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODES = ["homelab-01", "homelab-02", "homelab-03", "homelab-04", "homelab-05"]


@unittest.skipIf(os.getenv("SKIP_NIX_EVAL") == "1", "Nix evaluation disabled")
@unittest.skipUnless(shutil.which("nix"), "Nix required")
class KubeletImageGcTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        expression = """
          let
            flake = builtins.getFlake __FLAKE__;
            kubelet = name: builtins.filter
              (flag: builtins.match "--kubelet-arg=(image-gc|eviction).*" flag != null)
              flake.nixosConfigurations.${name}.config.services.k3s.extraFlags;
          in builtins.listToAttrs (map (name: { inherit name; value = kubelet name; }) __NODES__)
        """.replace("__FLAKE__", json.dumps(f"git+file://{ROOT}?dir=flake")).replace(
            "__NODES__", "[ " + " ".join(json.dumps(node) for node in NODES) + " ]"
        )
        result = subprocess.run(
            ["nix", "eval", "--impure", "--json", "--expr", expression],
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr)
        cls.flags = json.loads(result.stdout)

    def thresholds(self, node: str) -> tuple[int, int]:
        flags = self.flags[node]
        high = [
            f for f in flags if f.startswith("--kubelet-arg=image-gc-high-threshold=")
        ]
        low = [
            f for f in flags if f.startswith("--kubelet-arg=image-gc-low-threshold=")
        ]
        self.assertEqual((len(high), len(low)), (1, 1), flags)
        return int(high[0].rsplit("=", 1)[1]), int(low[0].rsplit("=", 1)[1])

    def test_homelab_04_collects_only_near_full_shared_persist(self) -> None:
        self.assertEqual(self.thresholds("homelab-04"), (95, 90))

    def test_other_nodes_keep_fleet_default(self) -> None:
        # homelab-02/03 have 58 GiB disks: 70% starts GC well before the
        # 15 GiB imagefs eviction threshold (74% used there).
        for node in ["homelab-01", "homelab-02", "homelab-03", "homelab-05"]:
            with self.subTest(node=node):
                self.assertEqual(self.thresholds(node), (70, 60))

    def test_image_gc_starts_before_imagefs_eviction(self) -> None:
        # Image filesystem sizes: 953 GiB on homelab-04, 58 GiB on the
        # smallest nodes. GC must trigger with more free space than eviction.
        for node, size_gib in [("homelab-04", 953), ("homelab-02", 58)]:
            with self.subTest(node=node):
                high, _ = self.thresholds(node)
                eviction = [f for f in self.flags[node] if "eviction-hard=" in f]
                self.assertEqual(len(eviction), 1)
                self.assertIn("imagefs.available<15Gi", eviction[0])
                self.assertGreater(size_gib * (100 - high) / 100, 15)


if __name__ == "__main__":
    unittest.main()
