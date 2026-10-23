from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.opnsense_config import OUTPUTS, synchronize, validate


def inventory() -> dict:
    return {
        "networks": {
            "clients": {
                "cidr": "10.0.20.0/24",
                "gateway": "10.0.20.1",
                "dhcp": {"pools": ["10.0.20.100-10.0.20.249"]},
            }
        },
        "devices": {"printer": {"network": "clients", "address": "10.0.20.124"}},
    }


class InventoryValidationTests(unittest.TestCase):
    def test_reserved_device_inside_pool_is_supported(self) -> None:
        validate(inventory())

    def test_device_must_belong_to_its_network(self) -> None:
        for address in ("10.0.30.124", "10.0.20.0", "10.0.20.255", "10.0.20.1"):
            with (
                self.subTest(address=address),
                self.assertRaisesRegex(ValueError, "device address"),
            ):
                config = inventory()
                config["devices"]["printer"]["address"] = address
                validate(config)

    def test_gateway_must_be_usable(self) -> None:
        for gateway in ("10.0.30.1", "10.0.20.0", "10.0.20.255"):
            with (
                self.subTest(gateway=gateway),
                self.assertRaisesRegex(ValueError, "gateway"),
            ):
                config = inventory()
                config["networks"]["clients"]["gateway"] = gateway
                validate(config)

    def test_subnets_cannot_overlap(self) -> None:
        config = inventory()
        config["networks"]["other"] = copy.deepcopy(config["networks"]["clients"])
        with self.assertRaisesRegex(ValueError, "overlaps"):
            validate(config)

    def test_pool_must_be_in_subnet_and_ordered(self) -> None:
        for pool in (
            "10.0.30.100-10.0.30.200",
            "10.0.20.200-10.0.20.100",
            "10.0.20.100-10.0.20.255",
        ):
            with self.subTest(pool=pool), self.assertRaisesRegex(ValueError, "pool"):
                config = inventory()
                config["networks"]["clients"]["dhcp"]["pools"] = [pool]
                validate(config)

    def test_pool_cannot_include_gateway(self) -> None:
        config = inventory()
        config["networks"]["clients"]["dhcp"]["pools"] = ["10.0.20.1-10.0.20.100"]
        with self.assertRaisesRegex(ValueError, "includes its gateway"):
            validate(config)


class GeneratedOutputTests(unittest.TestCase):
    def test_check_detects_drift_without_overwriting_then_render_repairs_it(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rendered = root / "rendered"
            rendered.mkdir()
            for source, destination in OUTPUTS.items():
                (rendered / source).write_text(json.dumps({"source": source}))
                target = root / destination
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("{}")
            self.assertEqual(
                set(synchronize(rendered, root, check=True)), set(OUTPUTS.values())
            )
            self.assertTrue(
                all((root / target).read_text() == "{}" for target in OUTPUTS.values())
            )
            self.assertEqual(
                len(synchronize(rendered, root, check=False)), len(OUTPUTS)
            )
            self.assertEqual(synchronize(rendered, root, check=True), [])

    def test_whitespace_changes_do_not_count_as_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for source, destination in OUTPUTS.items():
                (root / source).write_text('{"same": true}')
                target = root / destination
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('{\n  "same": true\n}\n')
            self.assertEqual(synchronize(root, root, check=True), [])


if __name__ == "__main__":
    unittest.main()
