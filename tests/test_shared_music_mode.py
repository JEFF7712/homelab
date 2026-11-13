from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


class HaLoader(yaml.SafeLoader):
    """SafeLoader that reads Home Assistant tags such as !include as null."""


HaLoader.add_multi_constructor("!", lambda loader, suffix, node: None)

AREAS = (
    "kitchen_ceiling",
    "dining_table",
    "floor_lamp",
    "tulip_lamp",
    "ceiling_strip",
)


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

    def _automation(self) -> dict:
        return yaml.safe_load(
            (
                REPO_ROOT / "home-assistant" / "automations" / "shared_music_mode.yaml"
            ).read_text(encoding="utf-8")
        )

    def _area_script(self) -> dict:
        return yaml.safe_load(
            (
                REPO_ROOT / "home-assistant" / "scripts" / "shared_music_area.yaml"
            ).read_text(encoding="utf-8")
        )

    def test_every_area_has_a_helper_registry_row_and_dashboard_tile(self) -> None:
        registry = yaml.safe_load(
            (REPO_ROOT / "home-assistant" / "registries" / "entities.yaml").read_text(
                encoding="utf-8"
            )
        )
        registered = {entry.get("entity_id") for entry in registry}
        dashboard = (
            REPO_ROOT / "home-assistant" / "dashboards" / "shared-spaces.yaml"
        ).read_text(encoding="utf-8")
        for area in ("led_strip", *AREAS):
            entity = f"input_boolean.music_mode_{area}"
            helper = yaml.safe_load(
                (
                    REPO_ROOT
                    / "home-assistant"
                    / "helpers"
                    / f"input_boolean_music_mode_{area}.yaml"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(helper["entity_id"], entity)
            # No initial: the toggles keep their last values across restarts.
            self.assertNotIn("initial", helper)
            self.assertIn(entity, registered)
            self.assertIn(f"entity: {entity}", dashboard)

    def test_area_script_and_director_agree_on_areas(self) -> None:
        script_areas = self._area_script()["sequence"][0]["variables"]["areas"]
        self.assertEqual(tuple(script_areas), AREAS)
        automation_areas = self._automation()["actions"][0]["variables"]["areas"]
        self.assertEqual(tuple(automation_areas), AREAS)
        core = yaml.load(
            (REPO_ROOT / "home-assistant" / "core" / "configuration.yaml").read_text(
                encoding="utf-8"
            ),
            Loader=HaLoader,
        )
        director = core["music_director"]
        self.assertEqual(tuple(director["areas"]), AREAS)
        self.assertEqual(director["enabled"], "input_boolean.shared_music_mode")
        self.assertEqual(director["strip"], "input_boolean.music_mode_led_strip")

    def test_area_script_covers_every_shared_light(self) -> None:
        areas = self._area_script()["sequence"][0]["variables"]["areas"]
        lights = {light for area in areas.values() for light in area["lights"]}
        self.assertEqual(
            lights,
            {
                "light.kitchen_ceiling_light_1",
                "light.kitchen_ceiling_light_2",
                "light.living_room_floor_lamp",
                "light.living_room_tulip_lamp",
                "light.kitchen_dining_table_ceiling_light_1",
                "light.kitchen_dining_table_ceiling_light_2",
                "light.apollo_led_strip_apollo_led_strip",
            },
        )
        # The Govee dining bulbs have their own Adaptive Lighting entry.
        self.assertEqual(
            areas["dining_table"]["adaptive"],
            "switch.adaptive_lighting_govee_dining_bulbs",
        )

    def test_mushroom_lamp_is_always_off_during_music_mode(self) -> None:
        automation = self._automation()
        branches = automation["actions"][1]["choose"]

        def branch(trigger_id: str) -> list:
            return next(
                b["sequence"]
                for b in branches
                if {"condition": "trigger", "id": trigger_id} in b["conditions"]
            )

        mode_on = yaml.safe_dump(branch("mode_on"))
        self.assertIn("scene_id: pre_music_mushroom_lamp", mode_on)
        self.assertIn("light.turn_off", mode_on)
        self.assertIn("light.kitchen_mushroom_lamp", mode_on)
        self.assertIn(
            "scene.pre_music_mushroom_lamp", yaml.safe_dump(branch("mode_off"))
        )
        # No toggle: it is not an area and nothing re-forces it off mid-session.
        self.assertNotIn("music_mode_mushroom_lamp", yaml.safe_dump(automation))
        self.assertFalse(
            (
                REPO_ROOT
                / "home-assistant"
                / "helpers"
                / "input_boolean_music_mode_mushroom_lamp.yaml"
            ).exists()
        )

    def test_music_mode_disables_adaptive_lighting_until_it_ends(self) -> None:
        branches = self._automation()["actions"][1]["choose"]

        def branch(trigger_id: str) -> list:
            return next(
                b["sequence"]
                for b in branches
                if {"condition": "trigger", "id": trigger_id} in b["conditions"]
            )

        switches = [
            "switch.adaptive_lighting_all_dimmable_lights",
            "switch.adaptive_lighting_govee_dining_bulbs",
        ]
        snapshot, turn_off = branch("mode_on")[:2]
        self.assertEqual(snapshot["data"]["scene_id"], "pre_music_adaptive_lighting")
        self.assertEqual(snapshot["data"]["snapshot_entities"], switches)
        self.assertEqual(turn_off["action"], "switch.turn_off")
        self.assertEqual(turn_off["target"]["entity_id"], switches)
        # Restored before the areas leave, so leave can hand them back.
        restore = branch("mode_off")[0]
        self.assertEqual(
            restore["then"][0]["target"]["entity_id"],
            "scene.pre_music_adaptive_lighting",
        )

    def test_area_script_snapshots_restores_and_hands_back_adaptive_lighting(
        self,
    ) -> None:
        raw = (
            REPO_ROOT / "home-assistant" / "scripts" / "shared_music_area.yaml"
        ).read_text(encoding="utf-8")
        self.assertEqual(self._area_script()["mode"], "parallel")
        for needle in (
            "scene.create",
            'scene_id: "pre_music_{{ area }}"',
            "scene.turn_on",
            "adaptive_lighting.set_manual_control",
            "adaptive_lighting.apply",
            "turn_on_lights: false",
            "state_attr('sensor.music_director', 'area_colors')",
            "state_attr('sensor.music_director', 'brightness_pct') | int(70)",
        ):
            self.assertIn(needle, raw)
        self.assertNotIn("delay:", raw)

    def test_automation_joins_leaves_and_repaints_taking_part_areas(self) -> None:
        automation = self._automation()
        self.assertEqual(automation["id"], "shared_music_mode")
        self.assertEqual(automation["alias"], "Lighting: Shared Music Mode")
        self.assertNotIn("initial_state", automation)
        triggers = {t["id"]: t for t in automation["triggers"]}
        self.assertEqual(
            set(triggers), {"mode_on", "mode_off", "area", "strip", "palette"}
        )
        self.assertEqual(
            triggers["area"]["entity_id"],
            [f"input_boolean.music_mode_{area}" for area in AREAS],
        )
        # The palette trigger must also fire on attribute-only changes.
        self.assertNotIn("to", triggers["palette"])
        raw = yaml.safe_dump(automation)
        steps = json.dumps(automation)
        for step in ("'join'", "'standby'", "'relight'", "'paint'", '"leave"'):
            self.assertIn(step, steps)
        self.assertNotIn("delay:", raw)
        # The director owns the strip's effects; here it is only switched.
        self.assertNotIn("rest_command.ledfx", raw)
        self.assertEqual(
            triggers["strip"]["entity_id"], "input_boolean.music_mode_led_strip"
        )
        # Brightness-only changes repaint too.
        self.assertIn("brightness_pct", raw)

    def test_only_selected_lights_are_on_during_music_mode(self) -> None:
        automation = self._automation()
        branches = automation["actions"][1]["choose"]

        def branch(trigger_id: str) -> str:
            return json.dumps(
                next(
                    b["sequence"]
                    for b in branches
                    if {"condition": "trigger", "id": trigger_id} in b["conditions"]
                )
            )

        # Starting snapshots every area: selected ones join, the rest go off.
        mode_on = branch("mode_on")
        self.assertIn('"for_each": "{{ areas }}"', mode_on)
        self.assertIn("'join' if repeat.item in taking_part else 'standby'", mode_on)
        self.assertIn('"scene_id": "pre_music_led_strip"', mode_on)
        self.assertIn("is_state('input_boolean.music_mode_led_strip', 'on')", mode_on)
        # Mid-session toggles paint or switch off, keeping the snapshot.
        area = branch("area")
        self.assertIn("'relight' if trigger.to_state.state == 'on' else 'off'", area)
        self.assertNotIn("scene.create", area)
        strip = branch("strip")
        self.assertIn("trigger.to_state.state == 'on'", strip)
        # WLED brightness scales LedFx's stream, so the strip joins at full.
        self.assertIn('"brightness": 255', strip)
        self.assertIn('"brightness": 255', mode_on)
        # Stopping restores every area and the strip.
        mode_off = branch("mode_off")
        self.assertIn('"for_each": "{{ areas }}"', mode_off)
        self.assertIn("scene.pre_music_led_strip", mode_off)

        script = json.dumps(self._area_script())
        self.assertIn("step in ['join', 'standby']", script)
        self.assertIn("step in ['standby', 'off']", script)
        self.assertIn("light.turn_off", script)
        # New tracks recolour lit lights only and leave their brightness.
        self.assertIn("select('is_state', 'on')", script)
        palette = branch("palette")
        self.assertIn("'relight' if trigger.from_state is not none", palette)
        self.assertIn("else 'paint'", palette)

    def test_music_snapshots_are_watchman_exempt(self) -> None:
        raw = (
            REPO_ROOT
            / "home-assistant"
            / "automations"
            / "watchman_missing_report.yaml"
        ).read_text(encoding="utf-8")
        for area in (*AREAS, "mushroom_lamp", "led_strip", "adaptive_lighting"):
            self.assertIn(f"scene.pre_music_{area}", raw)

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

    def test_area_toggles_sit_in_their_own_music_mode_section(self) -> None:
        dashboard = yaml.safe_load(
            (
                REPO_ROOT / "home-assistant" / "dashboards" / "shared-spaces.yaml"
            ).read_text(encoding="utf-8")
        )

        def sections(node: object):
            if isinstance(node, dict):
                if isinstance(node.get("cards"), list):
                    yield node["cards"]
                for value in node.values():
                    yield from sections(value)
            elif isinstance(node, list):
                for value in node:
                    yield from sections(value)

        music = next(
            cards
            for cards in sections(dashboard)
            if cards and cards[0].get("heading") == "Music Mode"
        )
        entities = [card.get("entity") for card in music]
        self.assertEqual(entities[1], "input_boolean.shared_music_mode")
        # Colour is picked on the integration's light; brightness on its number.
        colour = music[entities.index("light.music_mode_colour")]
        self.assertEqual(colour["type"], "custom:mushroom-light-card")
        self.assertTrue(colour["show_color_control"])
        self.assertIn("number.music_mode_brightness", entities)
        strip = music[entities.index("select.music_mode_strip_colours")]
        self.assertEqual(strip["features"], [{"type": "select-options"}])
        self.assertNotIn("input_select.music_mode_color", entities)
        # The preview sits under the toggle and only shows while it is on.
        preview = music[2]
        self.assertEqual(preview["type"], "markdown")
        self.assertEqual(
            preview["visibility"],
            [
                {
                    "condition": "state",
                    "entity": "input_boolean.shared_music_mode",
                    "state": "on",
                }
            ],
        )
        for attribute in ("area_colors", "gradient", "palette_spread", "intensity"):
            self.assertIn(f"state_attr(d, '{attribute}')", preview["content"])
        for area in ("led_strip", *AREAS):
            entity = f"input_boolean.music_mode_{area}"
            card = music[entities.index(entity)]
            self.assertEqual(card["tap_action"]["action"], "toggle")
            # Each card says whether its light takes part, not just on/off.
            self.assertIn("Joins", card["secondary"])
            self.assertIn("Not in music mode", card["secondary"])
        # Area toggles no longer sit among the Adaptive Lighting switches.
        adaptive = next(
            cards
            for cards in sections(dashboard)
            if cards and cards[0].get("heading") == "Adaptive Lighting"
        )
        self.assertFalse(
            any(
                str(c.get("entity", "")).startswith("input_boolean.music_mode")
                for c in adaptive
            )
        )


if __name__ == "__main__":
    unittest.main()
