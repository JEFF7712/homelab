from __future__ import annotations

import unittest
from pathlib import Path

import yaml
from jinja2.nativetypes import NativeEnvironment

from scripts.home_assistant.canonical import DuplicateKeySafeLoader

HA = Path(__file__).resolve().parent.parent / "home-assistant"
BULB = "light.kitchen_dining_table_ceiling_light_1"
DINING = "light.kitchen_dining_table_ceiling_light_2"


def load(path: str):
    return yaml.load((HA / path).read_text(), Loader=DuplicateKeySafeLoader)


class SamsRoomTests(unittest.TestCase):
    def setUp(self) -> None:
        self.automation = load("automations/sams_room_openrgb_sync.yaml")
        self.env = NativeEnvironment()
        self.env.filters["is_number"] = lambda value: self.is_number(value)

    @staticmethod
    def is_number(value: object) -> bool:
        try:
            float(value)
            return True
        except (ValueError, TypeError):
            return False

    def choose(self, payload):
        context = {"trigger": {"event": {"data": payload}}}
        for name, template in self.automation["actions"][0]["variables"].items():
            context[name] = self.env.from_string(template).render(context)
        for branch in self.automation["actions"][1]["choose"]:
            condition = branch["conditions"][0]["value_template"]
            if self.env.from_string(condition).render(context):
                step = branch["sequence"][0]
                return step["action"], {
                    key: self.env.from_string(value).render(context)
                    for key, value in step.get("data", {}).items()
                }
        return None

    def test_event_and_targets(self) -> None:
        self.assertEqual(
            self.automation["triggers"],
            [{"trigger": "event", "event_type": "sams_room_openrgb_sync"}],
        )
        for branch in self.automation["actions"][1]["choose"]:
            self.assertEqual(branch["sequence"][0]["target"], {"entity_id": BULB})

    def test_rgb_and_clamps(self) -> None:
        self.assertEqual(
            self.choose({"rgb_color": [-10, 400, 64], "brightness_pct": 140}),
            ("light.turn_on", {"rgb_color": [0, 255, 64], "brightness_pct": 100}),
        )
        self.assertEqual(
            self.choose({"rgb": [10, 20, 30]}),
            ("light.turn_on", {"rgb_color": [10, 20, 30]}),
        )

    def test_off_black_and_zero_brightness(self) -> None:
        for payload in ({"state": "OFF"}, {"rgb": [0, 0, 0]}, {"brightness_pct": -1}):
            with self.subTest(payload=payload):
                self.assertEqual(self.choose(payload), ("light.turn_off", {}))

    def test_kelvin_and_invalid_payloads(self) -> None:
        for value, expected in ((1000, 2700), (9000, 6500), ("3100", 3100)):
            self.assertEqual(
                self.choose({"color_temp_kelvin": value, "brightness_pct": "25"}),
                (
                    "light.turn_on",
                    {"color_temp_kelvin": expected, "brightness_pct": 25},
                ),
            )
        for payload in (
            {},
            None,
            [],
            {"rgb": "red"},
            {"rgb": [1, 2]},
            {"color_temp_kelvin": "bad"},
        ):
            with self.subTest(payload=payload):
                self.assertIsNone(self.choose(payload))

    def test_shared_controls_exclude_sams_room(self) -> None:
        for directory in ("scripts", "automations"):
            for path in (HA / directory).glob("*.yaml"):
                if path.name != "sams_room_openrgb_sync.yaml":
                    self.assertNotIn(BULB, path.read_text(), str(path))
        core = load("core/configuration.yaml")
        for group in core["light"]:
            if group.get("platform") == "group":
                self.assertNotIn(BULB, group["entities"])
        self.assertNotIn(BULB, (HA / "dashboards/shared-spaces.yaml").read_text())
        self.assertIn(DINING, (HA / "dashboards/shared-spaces.yaml").read_text())

    def test_registry_and_dashboard(self) -> None:
        entities = {row["entity_id"]: row for row in load("registries/entities.yaml")}
        self.assertEqual(entities[BULB]["area_id"], "sams_room")
        self.assertNotIn("shared_space", entities[BULB]["labels"])
        self.assertEqual(entities[DINING]["area_id"], "kitchen")
        devices = {row["id"]: row for row in load("registries/devices.yaml")}
        self.assertEqual(
            devices["9bea576dabb697ba6e18167f2953ffc9"]["area_id"], "sams_room"
        )
        cards = load("dashboards/sams-room.yaml")["views"][0]["sections"][0]["cards"]
        self.assertEqual(cards[0]["entity"], BULB)
        self.assertEqual(cards[1]["entity"], "automation.sams_room_openrgb_sync")
        self.assertEqual(cards[1]["tap_action"], {"action": "toggle"})
        self.assertIn("/sams-room", (HA / "dashboards/home-overview.yaml").read_text())
