from __future__ import annotations

import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestGoveeMusicMode(unittest.TestCase):
    def test_helper_definition(self) -> None:
        helper_file = (
            REPO_ROOT
            / "home-assistant"
            / "helpers"
            / "input_boolean_govee_music_mode.yaml"
        )
        self.assertTrue(helper_file.is_file(), "Helper file must exist")
        data = yaml.safe_load(helper_file.read_text(encoding="utf-8"))
        self.assertEqual(data.get("entity_id"), "input_boolean.govee_music_mode")
        self.assertEqual(data.get("name"), "Govee Music Mode")

    def test_entity_registry_registration(self) -> None:
        entities_file = REPO_ROOT / "home-assistant" / "registries" / "entities.yaml"
        data = yaml.safe_load(entities_file.read_text(encoding="utf-8"))
        registered = [
            entry.get("entity_id") for entry in data if isinstance(entry, dict)
        ]
        self.assertIn("input_boolean.govee_music_mode", registered)

    def test_effects_script(self) -> None:
        script_file = (
            REPO_ROOT / "home-assistant" / "scripts" / "effects_music_mode.yaml"
        )
        self.assertTrue(script_file.is_file(), "Effects script must exist")
        data = yaml.safe_load(script_file.read_text(encoding="utf-8"))
        self.assertEqual(data.get("alias"), "Effects: Music Mode")
        sequence = data.get("sequence", [])
        self.assertEqual(len(sequence), 1)
        self.assertEqual(sequence[0].get("action"), "input_boolean.toggle")
        self.assertEqual(
            sequence[0].get("target", {}).get("entity_id"),
            "input_boolean.govee_music_mode",
        )

    def test_lighting_automation_uses_native_effects_and_no_rate_limit_loop(
        self,
    ) -> None:
        auto_file = (
            REPO_ROOT / "home-assistant" / "automations" / "govee_music_mode.yaml"
        )
        self.assertTrue(auto_file.is_file(), "Automation file must exist")
        raw_text = auto_file.read_text(encoding="utf-8")
        data = yaml.safe_load(raw_text)

        self.assertEqual(data.get("id"), "govee_music_mode")
        self.assertEqual(data.get("alias"), "Lighting: Govee Music Mode")

        # Must NOT contain repeating loop or delays that violate Govee Cloud API limits
        self.assertNotIn("repeat:", raw_text)
        self.assertNotIn("delay:", raw_text)
        self.assertNotIn("states.media_player", raw_text)

        # Must call LedFx scene activation
        self.assertIn("rest_command.ledfx_activate_scene", raw_text)
        self.assertIn("rest_command.ledfx_deactivate_scene", raw_text)
        self.assertIn("music-mode", raw_text)

        # Must lock and restore Adaptive Lighting properly
        self.assertIn("adaptive_lighting.set_manual_control", raw_text)
        self.assertIn("adaptive_lighting.apply", raw_text)
        self.assertIn("turn_on_lights: false", raw_text)

    def test_voice_automation(self) -> None:
        voice_file = (
            REPO_ROOT
            / "home-assistant"
            / "automations"
            / "jarvis_voice_music_mode.yaml"
        )
        self.assertTrue(voice_file.is_file(), "Voice automation file must exist")
        data = yaml.safe_load(voice_file.read_text(encoding="utf-8"))
        self.assertEqual(data.get("id"), "jarvis_voice_music_mode")
        self.assertEqual(data.get("alias"), "Jarvis: Voice Music Mode")

        triggers = data.get("triggers", [])
        trigger_ids = [t.get("id") for t in triggers]
        self.assertIn("on", trigger_ids)
        self.assertIn("off", trigger_ids)

    def test_dashboards_contain_music_mode(self) -> None:
        shared_spaces = (
            REPO_ROOT / "home-assistant" / "dashboards" / "shared-spaces.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("script.effects_music_mode", shared_spaces)
        self.assertIn("input_boolean.govee_music_mode", shared_spaces)

        lovelace_jarvis = (
            REPO_ROOT / "home-assistant" / "dashboards" / "lovelace-jarvis.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("input_boolean.govee_music_mode", lovelace_jarvis)


if __name__ == "__main__":
    unittest.main()
