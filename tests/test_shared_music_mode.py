from __future__ import annotations

import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestSharedMusicMode(unittest.TestCase):
    def test_helper_definition(self) -> None:
        helper_file = (
            REPO_ROOT
            / "home-assistant"
            / "helpers"
            / "input_boolean_shared_music_mode.yaml"
        )
        self.assertTrue(helper_file.is_file(), "Helper file must exist")
        data = yaml.safe_load(helper_file.read_text(encoding="utf-8"))
        self.assertEqual(data.get("entity_id"), "input_boolean.shared_music_mode")
        self.assertEqual(data.get("name"), "Shared Music Mode")

    def test_entity_registry_registration(self) -> None:
        entities_file = REPO_ROOT / "home-assistant" / "registries" / "entities.yaml"
        data = yaml.safe_load(entities_file.read_text(encoding="utf-8"))
        registered = [
            entry.get("entity_id") for entry in data if isinstance(entry, dict)
        ]
        self.assertIn("input_boolean.shared_music_mode", registered)

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
            "input_boolean.shared_music_mode",
        )

    def test_lighting_automation_uses_native_effects_and_no_rate_limit_loop(
        self,
    ) -> None:
        auto_file = (
            REPO_ROOT / "home-assistant" / "automations" / "shared_music_mode.yaml"
        )
        self.assertTrue(auto_file.is_file(), "Automation file must exist")
        raw_text = auto_file.read_text(encoding="utf-8")
        data = yaml.safe_load(raw_text)

        self.assertEqual(data.get("id"), "shared_music_mode")
        self.assertEqual(data.get("alias"), "Lighting: Shared Music Mode")

        # One colour write per track: no loops or delays, and LedFx no
        # longer drives the shared bulbs.
        self.assertNotIn("repeat:", raw_text)
        self.assertNotIn("delay:", raw_text)
        self.assertNotIn("states.media_player", raw_text)
        self.assertNotIn("ledfx", raw_text.split("actions:", 1)[1])

        track = next(t for t in data["triggers"] if t.get("id") == "track")
        self.assertEqual(track["attribute"], "entity_picture")
        self.assertEqual(raw_text.count("action: script.shared_music_album_color"), 2)

        # Must lock and restore Adaptive Lighting properly
        self.assertIn("adaptive_lighting.set_manual_control", raw_text)
        self.assertIn("adaptive_lighting.apply", raw_text)
        self.assertIn("turn_on_lights: false", raw_text)

        # Must snapshot pre-music state on entry and restore it on exit, so
        # bulbs never keep LedFx's last frame (black when the tap is silent).
        self.assertIn("scene.create", raw_text)
        self.assertIn("pre_music_mode_shared", raw_text)
        self.assertIn("scene.turn_on", raw_text)

    def test_music_snapshot_is_watchman_exempt(self) -> None:
        watchman_file = (
            REPO_ROOT
            / "home-assistant"
            / "automations"
            / "watchman_missing_report.yaml"
        )
        raw_text = watchman_file.read_text(encoding="utf-8")
        self.assertIn("scene.pre_music_mode_shared", raw_text)

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

        raw_text = voice_file.read_text(encoding="utf-8")
        self.assertIn("turn on [the] shared music mode", raw_text)
        self.assertIn("turn off [the] shared music mode", raw_text)
        self.assertIn("turn on [the] shared spaces music mode", raw_text)
        self.assertNotRegex(raw_text.lower(), r"govee|zigbee")

    def test_automation_is_enabled_and_leaves_the_apollo_strip_alone(self) -> None:
        auto_file = (
            REPO_ROOT / "home-assistant" / "automations" / "shared_music_mode.yaml"
        )
        raw_text = auto_file.read_text(encoding="utf-8")
        self.assertNotIn("initial_state", yaml.safe_load(raw_text))
        self.assertNotIn("light.apollo_led_strip_apollo_led_strip", raw_text)

    def test_dashboards_contain_music_mode(self) -> None:
        shared_spaces = (
            REPO_ROOT / "home-assistant" / "dashboards" / "shared-spaces.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("script.effects_music_mode", shared_spaces)
        self.assertIn("input_boolean.shared_music_mode", shared_spaces)

        lovelace_jarvis = (
            REPO_ROOT / "home-assistant" / "dashboards" / "lovelace-jarvis.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("input_boolean.shared_music_mode", lovelace_jarvis)

    def test_album_colour_script_covers_all_shared_bulbs(self) -> None:
        script = yaml.safe_load(
            (
                REPO_ROOT
                / "home-assistant"
                / "scripts"
                / "shared_music_album_color.yaml"
            ).read_text(encoding="utf-8")
        )
        call = next(s for s in script["sequence"] if "action" in s)
        self.assertEqual(call["action"], "color_extractor.turn_on")
        self.assertEqual(
            set(call["data"]["entity_id"]),
            {
                "light.kitchen_ceiling_light_1",
                "light.kitchen_ceiling_light_2",
                "light.living_room_floor_lamp",
                "light.living_room_tulip_lamp",
                "light.kitchen_dining_table_ceiling_light_1",
                "light.kitchen_dining_table_ceiling_light_2",
            },
        )
        self.assertIn("brightness_pct", call["data"])
        core = (REPO_ROOT / "home-assistant" / "core" / "configuration.yaml").read_text(
            encoding="utf-8"
        )
        self.assertRegex(
            core, r"allowlist_external_urls:\n\s+- http://127\.0\.0\.1:8123\n"
        )


if __name__ == "__main__":
    unittest.main()
