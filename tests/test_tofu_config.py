"""Generated-file synchronization must fail closed before changing source files."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.tofu_config import OUTPUTS, synchronize

ROOT = Path(__file__).resolve().parents[1]


class TofuRenderingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        self.rendered = Path(self.temporary.name) / "rendered"
        for name in OUTPUTS:
            target = self.rendered / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / "tofu" / name).read_bytes())

    def test_check_reports_missing_outputs_without_writing(self) -> None:
        self.assertEqual(
            synchronize(self.rendered, self.root, check=True),
            ["tofu/" + name for name in OUTPUTS],
        )
        self.assertFalse(self.root.exists())

    def test_render_then_check_ignores_json_formatting(self) -> None:
        self.assertEqual(
            len(synchronize(self.rendered, self.root, check=False)), len(OUTPUTS)
        )
        for name in OUTPUTS:
            target = self.root / "tofu" / name
            target.write_text(json.dumps(json.loads(target.read_text())))
        self.assertEqual(synchronize(self.rendered, self.root, check=True), [])

    def test_invalid_switch_identity_prevents_all_writes(self) -> None:
        inventory = self.rendered / "switches/inventory.json"
        value = json.loads(inventory.read_text())
        value["switches"]["upstairs"]["mac"] = "00:00:00:00:00:00"
        inventory.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "Identity"):
            synchronize(self.rendered, self.root, check=False)
        self.assertFalse(self.root.exists())

    def test_invalid_json_prevents_all_writes(self) -> None:
        (self.rendered / OUTPUTS[-1]).write_text("invalid")
        with self.assertRaises(ValueError):
            synchronize(self.rendered, self.root, check=False)
        self.assertFalse(self.root.exists())

    def test_runtime_blueprint_preserves_management_guards(self) -> None:
        config = json.loads((ROOT / "tofu/switches/main.tf.json").read_text())
        mode = config["resource"]["tplinkeasysmart_vlan_mode"]["switch"]
        condition = mode["lifecycle"]["precondition"][0]["condition"]
        for field in [
            "mac_address",
            "ip_address",
            "model",
            "hardware_version",
            "firmware_version",
        ]:
            self.assertIn("data.tplinkeasysmart_system_info.switch." + field, condition)
        for kind, instances in config["resource"].items():
            for resource in instances.values():
                self.assertTrue(resource["lifecycle"]["prevent_destroy"])
                if kind.endswith(("_port", "_group", "_pvid")):
                    self.assertFalse(resource["allow_disruptive"])
        self.assertEqual(len(config["import"]), 5)


if __name__ == "__main__":
    unittest.main()
