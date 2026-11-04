"""Generated-file synchronization must fail closed before changing source files."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import opnsense_config
from scripts.tofu_config import OUTPUTS, TEXT_OUTPUTS, synchronize, synchronize_all

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

        for source, target in TEXT_OUTPUTS.items():
            output = self.rendered / source
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes((ROOT / target).read_bytes())

    def test_check_reports_missing_outputs_without_writing(self) -> None:
        self.assertEqual(
            synchronize(self.rendered, self.root, check=True),
            ["tofu/" + name for name in OUTPUTS] + list(TEXT_OUTPUTS.values()),
        )
        self.assertFalse(self.root.exists())

    def test_render_then_check_ignores_json_formatting(self) -> None:
        self.assertEqual(
            len(synchronize(self.rendered, self.root, check=False)),
            len(OUTPUTS) + len(TEXT_OUTPUTS),
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

    def opnsense_outputs(self) -> None:
        rendered = self.rendered / "opnsense"
        rendered.mkdir()
        for source, target in (
            opnsense_config.OUTPUTS | opnsense_config.TEXT_OUTPUTS
        ).items():
            (rendered / source).write_bytes((ROOT / target).read_bytes())
        (rendered / "inventory.json").write_text('{"networks":{},"devices":{}}')

    def test_unified_render_checks_every_stack_and_document(self) -> None:
        self.opnsense_outputs()
        changed = synchronize_all(self.rendered, self.root, check=False)
        self.assertIn("tofu/opnsense/main.tf.json", changed)
        self.assertIn("tofu/switches/inventory.json", changed)
        self.assertIn("docs/network/switch-port-map.md", changed)
        self.assertEqual(synchronize_all(self.rendered, self.root, check=True), [])
        (self.root / "tofu/opnsense/main.tf.json").write_text("{}")
        self.assertEqual(
            synchronize_all(self.rendered, self.root, check=True),
            ["tofu/opnsense/main.tf.json"],
        )

    def test_failed_opnsense_validation_does_not_write_other_stacks(self) -> None:
        self.opnsense_outputs()
        (self.rendered / "opnsense/inventory.json").write_text(
            '{"networks":{"bad":{"cidr":"invalid"}},"devices":{}}'
        )
        with self.assertRaises(ValueError):
            synchronize_all(self.rendered, self.root, check=False)
        self.assertFalse(self.root.exists())

    def test_failed_switch_validation_does_not_write_opnsense(self) -> None:
        self.opnsense_outputs()
        value = json.loads((self.rendered / "switches/inventory.json").read_text())
        value["switches"]["upstairs"]["mac"] = "00:00:00:00:00:00"
        (self.rendered / "switches/inventory.json").write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "Identity"):
            synchronize_all(self.rendered, self.root, check=False)
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
