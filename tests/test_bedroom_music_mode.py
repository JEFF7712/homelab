from __future__ import annotations

import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

BEDROOM_LIGHTS = {
    "light.desk_lamp_desk_lamp",
    "light.floor_lamp_floor_lamp",
    "light.light_strip_light_strip",
}

SHARED_GOVEE_LIGHTS = {
    "light.kitchen_light",
    "light.kitchen_light_2",
    "light.living_room_mushroom_lamp",
    "light.living_room_tulip_lamp",
}


class TestBedroomMusicMode(unittest.TestCase):
    def test_helper_definition(self) -> None:
        helper_file = (
            REPO_ROOT
            / "home-assistant"
            / "helpers"
            / "input_boolean_bedroom_music_mode.yaml"
        )
        self.assertTrue(helper_file.is_file(), "Helper file must exist")
        data = yaml.safe_load(helper_file.read_text(encoding="utf-8"))
        self.assertEqual(data.get("entity_id"), "input_boolean.bedroom_music_mode")
        self.assertEqual(data.get("name"), "Bedroom Music Mode")

    def test_entity_registry_registration(self) -> None:
        entities_file = REPO_ROOT / "home-assistant" / "registries" / "entities.yaml"
        data = yaml.safe_load(entities_file.read_text(encoding="utf-8"))
        registered = [
            entry.get("entity_id") for entry in data if isinstance(entry, dict)
        ]
        self.assertIn("input_boolean.bedroom_music_mode", registered)
        # Separate toggle from the shared-spaces one.
        self.assertIn("input_boolean.govee_music_mode", registered)

    def test_effects_script(self) -> None:
        script_file = (
            REPO_ROOT / "home-assistant" / "scripts" / "effects_bedroom_music_mode.yaml"
        )
        self.assertTrue(script_file.is_file(), "Effects script must exist")
        data = yaml.safe_load(script_file.read_text(encoding="utf-8"))
        self.assertEqual(data.get("alias"), "Effects: Bedroom Music Mode")
        sequence = data.get("sequence", [])
        self.assertEqual(len(sequence), 1)
        self.assertEqual(sequence[0].get("action"), "input_boolean.toggle")
        self.assertEqual(
            sequence[0].get("target", {}).get("entity_id"),
            "input_boolean.bedroom_music_mode",
        )

    def test_lighting_automation_uses_separate_ledfx_scene(self) -> None:
        auto_file = (
            REPO_ROOT / "home-assistant" / "automations" / "bedroom_music_mode.yaml"
        )
        self.assertTrue(auto_file.is_file(), "Automation file must exist")
        raw_text = auto_file.read_text(encoding="utf-8")
        data = yaml.safe_load(raw_text)

        self.assertEqual(data.get("id"), "bedroom_music_mode")
        self.assertEqual(data.get("alias"), "Lighting: Bedroom Music Mode")

        # Must NOT contain repeating loop or delays, mirroring the Govee contract.
        self.assertNotIn("repeat:", raw_text)
        self.assertNotIn("delay:", raw_text)
        self.assertNotIn("states.media_player", raw_text)

        # Separate LedFx scene so bedroom and shared spaces run independently.
        self.assertIn("rest_command.ledfx_activate_scene", raw_text)
        self.assertIn("rest_command.ledfx_deactivate_scene", raw_text)
        self.assertIn("bedroom-music-mode", raw_text)
        self.assertNotIn('"music-mode"', raw_text)

        # Must lock and restore Adaptive Lighting properly.
        self.assertIn("adaptive_lighting.set_manual_control", raw_text)
        self.assertIn("adaptive_lighting.apply", raw_text)
        self.assertIn("turn_on_lights: false", raw_text)

        # Bedroom Roku bulbs only; shared Govee lights must stay untouched.
        for light in BEDROOM_LIGHTS:
            self.assertIn(light, raw_text)
        for light in SHARED_GOVEE_LIGHTS:
            self.assertNotIn(light, raw_text)
        self.assertIn("input_boolean.bedroom_music_mode", raw_text)
        self.assertNotIn("input_boolean.govee_music_mode", raw_text)

    def test_voice_automation_has_bedroom_triggers(self) -> None:
        voice_file = (
            REPO_ROOT
            / "home-assistant"
            / "automations"
            / "jarvis_voice_music_mode.yaml"
        )
        data = yaml.safe_load(voice_file.read_text(encoding="utf-8"))
        triggers = data.get("triggers", [])
        trigger_ids = [t.get("id") for t in triggers]
        self.assertIn("on", trigger_ids)
        self.assertIn("off", trigger_ids)
        self.assertIn("bedroom_on", trigger_ids)
        self.assertIn("bedroom_off", trigger_ids)

        raw_text = voice_file.read_text(encoding="utf-8")
        self.assertIn("input_boolean.bedroom_music_mode", raw_text)
        self.assertIn("input_boolean.govee_music_mode", raw_text)
        # Bare "music mode" keeps targeting shared spaces; bedroom needs qualifier.
        self.assertIn("turn on bedroom music mode", raw_text)
        self.assertIn("turn off bedroom music mode", raw_text)

    def test_dashboards_contain_bedroom_music_mode(self) -> None:
        bedroom = (
            REPO_ROOT / "home-assistant" / "dashboards" / "bedroom-lights.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("script.effects_bedroom_music_mode", bedroom)
        self.assertIn("input_boolean.bedroom_music_mode", bedroom)

        lovelace_jarvis = (
            REPO_ROOT / "home-assistant" / "dashboards" / "lovelace-jarvis.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("input_boolean.bedroom_music_mode", lovelace_jarvis)
        self.assertIn("input_boolean.govee_music_mode", lovelace_jarvis)


if __name__ == "__main__":
    unittest.main()
