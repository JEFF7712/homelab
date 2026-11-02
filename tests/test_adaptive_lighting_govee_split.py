from __future__ import annotations

import unittest
from collections.abc import Iterator
from pathlib import Path

import yaml

from scripts.home_assistant.canonical import DuplicateKeySafeLoader

REPO_ROOT = Path(__file__).resolve().parent.parent
HA = REPO_ROOT / "home-assistant"
SOURCES = [
    *sorted((HA / "automations").glob("*.yaml")),
    *sorted((HA / "scripts").glob("*.yaml")),
    HA / "core/configuration.yaml",
]

# The H6008 white range is 2700K-6500K, so the dining bulbs live on their own
# Adaptive Lighting entry with a 2700K floor (docs/gotchas/govee-lan-control.md).
SWITCH_LIGHTS = {
    "switch.adaptive_lighting_all_dimmable_lights": {
        "light.desk_lamp_desk_lamp",
        "light.floor_lamp_floor_lamp",
        "light.light_strip_light_strip",
        "light.kitchen_ceiling_light_1",
        "light.kitchen_ceiling_light_2",
        "light.living_room_floor_lamp",
        "light.living_room_tulip_lamp",
    },
    "switch.adaptive_lighting_govee_dining_bulbs": {
        "light.kitchen_dining_table_ceiling_light_1",
        "light.kitchen_dining_table_ceiling_light_2",
    },
}


def walk(node: object) -> Iterator[dict]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def adaptive_calls() -> Iterator[tuple[str, dict]]:
    for path in SOURCES:
        text = path.read_text(encoding="utf-8")
        data = yaml.load(text, Loader=DuplicateKeySafeLoader)
        for step in walk(data):
            service = step.get("action") or step.get("service")
            if isinstance(service, str) and service.startswith("adaptive_lighting."):
                yield f"{path.relative_to(REPO_ROOT)}:{service}", step.get("data") or {}


class AdaptiveLightingSwitchSplitTests(unittest.TestCase):
    def test_calls_only_name_lights_managed_by_their_switch(self) -> None:
        checked = 0
        for where, data in adaptive_calls():
            switch = data.get("entity_id")
            lights = data.get("lights")
            if not isinstance(switch, str) or not lights:
                continue
            self.assertIn(switch, SWITCH_LIGHTS, where)
            self.assertLessEqual(set(lights), SWITCH_LIGHTS[switch], where)
            checked += 1
        self.assertGreater(checked, 0)

    def test_late_night_sleep_mode_covers_both_entries(self) -> None:
        data = yaml.safe_load(
            (HA / "automations/shared_spaces_late_night.yaml").read_text(
                encoding="utf-8"
            )
        )
        targets = [
            step["target"]["entity_id"]
            for step in walk(data)
            if step.get("action") in ("switch.turn_on", "switch.turn_off")
        ]
        self.assertEqual(len(targets), 2)
        for target in targets:
            self.assertEqual(set(target), {f"{s}_sleep_mode" for s in SWITCH_LIGHTS})

    def test_shared_spaces_toggles_drive_both_entries(self) -> None:
        core = yaml.load(
            (HA / "core/configuration.yaml").read_text(encoding="utf-8"),
            Loader=DuplicateKeySafeLoader,
        )
        switches = {
            s["unique_id"]: s for block in core["template"] for s in block["switch"]
        }
        dashboard = (HA / "dashboards/shared-spaces.yaml").read_text(encoding="utf-8")
        for key in ("sleep_mode", "adapt_brightness", "adapt_color"):
            expected = {f"{s}_{key}" for s in SWITCH_LIGHTS}
            switch = switches[f"shared_spaces_{key}"]
            for verb in ("turn_on", "turn_off"):
                (step,) = switch[verb]
                self.assertEqual(step["action"], f"switch.{verb}", key)
                self.assertEqual(set(step["target"]["entity_id"]), expected, key)
            for entity in expected:
                self.assertIn(entity, switch["state"], key)
                # The dashboard shows only the combined toggle.
                self.assertNotIn(entity, dashboard, key)
            self.assertIn(f"switch.shared_spaces_{key}", dashboard, key)


if __name__ == "__main__":
    unittest.main()
