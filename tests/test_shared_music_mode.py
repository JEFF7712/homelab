from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path

import yaml

try:
    import jinja2

    HAS_JINJA2 = True
except ImportError:
    jinja2 = None  # type: ignore[assignment]
    HAS_JINJA2 = False

REPO_ROOT = Path(__file__).resolve().parent.parent


def _walk(node):
    """Yield every dict and list nested in a script sequence."""
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


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

    def test_device_alert_covers_every_music_mode_light(self) -> None:
        script = self._area_script()
        areas = script["sequence"][0]["variables"]["areas"]
        expected = {"light.living_room_led_strip": "input_boolean.music_mode_led_strip"}
        for area, config in areas.items():
            for light in config["lights"]:
                expected[light] = f"input_boolean.music_mode_{area}"
        alert = yaml.load(
            (
                REPO_ROOT
                / "home-assistant"
                / "automations"
                / "music_mode_device_alert.yaml"
            ).read_text(encoding="utf-8"),
            Loader=HaLoader,
        )
        owners = alert["actions"][0]["variables"]["owners"]
        self.assertEqual(owners, expected)
        light_trigger = next(t for t in alert["triggers"] if t["id"] == "light")
        self.assertEqual(set(light_trigger["entity_id"]), set(expected))
        self.assertEqual(
            alert["conditions"],
            [
                {
                    "condition": "state",
                    "entity_id": "input_boolean.shared_music_mode",
                    "state": "on",
                }
            ],
        )

    def test_variables_steps_only_read_earlier_steps(self) -> None:
        # Each variable must render with only earlier steps defined, so the
        # result never depends on key order within a step.
        for path, steps in (
            (
                "automations/music_mode_device_alert.yaml",
                lambda doc: doc["actions"],
            ),
            (
                "scripts/jarvis_play_media.yaml",
                lambda doc: next(
                    choice["sequence"]
                    for step in doc["sequence"]
                    if "choose" in step
                    for choice in step["choose"]
                    if any("by_parts" in str(s) for s in choice["sequence"])
                ),
            ),
        ):
            doc = yaml.load(
                (REPO_ROOT / "home-assistant" / path).read_text(encoding="utf-8"),
                Loader=HaLoader,
            )
            defined: set[str] = set()
            for step in steps(doc):
                names = set(step.get("variables", {}))
                for name, value in step.get("variables", {}).items():
                    for other in names - {name}:
                        self.assertNotRegex(
                            str(value), rf"(?<![\w.]){other}(?!\w)", f"{path}: {name}"
                        )
                defined |= names
            self.assertTrue(defined)

    def test_leaving_music_mode_hands_back_to_adaptive_lighting_quickly(
        self,
    ) -> None:
        # adaptive_lighting.apply defaults to the entry's 45 s transition.
        script = self._area_script()
        applies = [
            step
            for step in _walk(script["sequence"])
            if isinstance(step, dict)
            and step.get("action") == "adaptive_lighting.apply"
        ]
        self.assertTrue(applies)
        for step in applies:
            self.assertLessEqual(step["data"]["transition"], 2)

    def test_led_strip_entity_drives_both_wled_segments(self) -> None:
        # Turning the strip off once left WLED segment 1 running its effect.
        config = yaml.load(
            (REPO_ROOT / "home-assistant" / "core" / "configuration.yaml").read_text(
                encoding="utf-8"
            ),
            Loader=HaLoader,
        )
        group = next(
            item
            for item in config["light"]
            if item.get("platform") == "group"
            and item.get("name") == "Living Room LED Strip"
        )
        self.assertEqual(group["unique_id"], "living_room_led_strip_group")
        self.assertEqual(
            group["entities"],
            [
                "light.living_room_wled_gledopto_segment_0",
                "light.living_room_wled_gledopto_segment_1",
            ],
        )

    def test_automation_joins_leaves_and_repaints_taking_part_areas(self) -> None:
        automation = self._automation()
        self.assertEqual(automation["id"], "shared_music_mode")
        self.assertEqual(automation["alias"], "Lighting: Shared Music Mode")
        self.assertNotIn("initial_state", automation)
        triggers = {t["id"]: t for t in automation["triggers"]}
        self.assertEqual(
            set(triggers), {"mode_on", "mode_off", "area", "strip", "palette", "idle"}
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
        # Switching off has no fade: Zigbee bulbs fade to off slowly.
        off = next(
            step
            for step in self._area_script()["sequence"]
            if "step in ['standby', 'off']" in json.dumps(step)
        )
        self.assertNotIn("transition", json.dumps(off["then"]))
        # New tracks recolour lit lights only and leave their brightness.
        self.assertIn("select('is_state', 'on')", script)

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
        # Pins: a colour wheel to pin, edit or remove the album's colour.
        pin = music[entities.index("light.music_mode_pin")]
        self.assertEqual(pin["type"], "custom:mushroom-light-card")
        self.assertTrue(pin["show_color_control"])
        # Effect pins: Auto or an effect for the playing album.
        effect_pin = music[entities.index("select.music_mode_effect_pin")]
        self.assertEqual(effect_pin["features"], [{"type": "select-options"}])
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


class _State:
    """A trigger from_state / to_state stand-in."""

    def __init__(self, **attributes: object) -> None:
        self.attributes = attributes


def _render(template: str, **context: object) -> object:
    """Render a Home Assistant template with real Jinja2 and parse the result
    the way HA does for variables (numbers, booleans and None come back
    native)."""
    env = jinja2.Environment()
    text = env.from_string(template).render(**context).strip()
    lowered = text.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered == "none":
        return None
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            pass
    return text


@unittest.skipUnless(HAS_JINJA2, "jinja2 is in the nix devShell")
class TestMusicModeTemplates(unittest.TestCase):
    """Rendered templates: section brightness, the palette filter and the
    idle timeout."""

    def setUp(self) -> None:
        self.automation = yaml.safe_load(
            (
                REPO_ROOT / "home-assistant" / "automations" / "shared_music_mode.yaml"
            ).read_text(encoding="utf-8")
        )
        self.script = yaml.safe_load(
            (
                REPO_ROOT / "home-assistant" / "scripts" / "shared_music_area.yaml"
            ).read_text(encoding="utf-8")
        )

    def _palette_steps(self) -> list:
        branches = self.automation["actions"][1]["choose"]
        return next(
            b["sequence"]
            for b in branches
            if {"condition": "trigger", "id": "palette"} in b["conditions"]
        )

    def _palette_step(self, old: dict, new: dict) -> tuple[str, object]:
        trigger = {
            "from_state": _State(**old) if old is not None else None,
            "to_state": _State(**new),
        }
        context: dict[str, object] = {"trigger": trigger}
        for step in self._palette_steps():
            for name, template in step.get("variables", {}).items():
                context[name] = _render(template, **context)
        call = self._palette_steps()[-1]["repeat"]["sequence"][0]
        return context["step"], _render(call["data"]["from_pct"], **context)

    def _section_lights(
        self, levels: dict[str, int | None], from_pct: object
    ) -> tuple[list[str], list[str]]:
        """Which lit lights the section step moves to the new level, and
        which it only recolours."""
        step = next(
            choice
            for item in _walk(self.script["sequence"])
            if isinstance(item, dict) and "choose" in item
            for choice in item["choose"]
            if "'section'" in json.dumps(choice["conditions"])
        )
        context: dict[str, object] = {
            "lit": list(levels),
            "from_pct": from_pct,
            "state_attr": lambda entity, _attr: levels[entity],
        }
        calls = []
        for action in step["sequence"]:
            for name, template in action.get("variables", {}).items():
                value = _render(template, **context)
                # HA hands later steps native lists, not their text.
                if isinstance(value, str) and value.startswith("["):
                    value = ast.literal_eval(value)
                context[name] = value
            if "if" in action:
                calls.append(action["then"][0])
        follow, hold = calls
        self.assertEqual(follow["target"]["entity_id"], "{{ following }}")
        self.assertEqual(follow["data"]["brightness_pct"], "{{ brightness_pct }}")
        self.assertEqual(hold["target"]["entity_id"], "{{ held }}")
        self.assertNotIn("brightness", hold["data"])
        self.assertNotIn("brightness_pct", hold["data"])
        return context["following"], context["held"]

    def _passes(self, old: dict | None, new: dict, mode: str = "on") -> bool:
        template = self.automation["conditions"][0]["value_template"]
        return _render(
            template,
            trigger={
                "id": "palette",
                "from_state": _State(**old) if old is not None else None,
                "to_state": _State(**new),
            },
            is_state=lambda entity, state: mode == state,
        )

    def test_section_change_passes_the_previous_level(self) -> None:
        colours = {"kitchen_ceiling": [255, 0, 0]}
        base = {"area_colors": colours, "brightness_pct": 70}
        step, from_pct = self._palette_step(
            {**base, "area_brightness_pct": 70},
            {**base, "area_brightness_pct": 56},
        )
        self.assertEqual((step, from_pct), ("section", 70))

    def test_brightness_slider_relights_and_new_tracks_only_paint(self) -> None:
        old = {"area_colors": {}, "brightness_pct": 70, "area_brightness_pct": 70}
        step, from_pct = self._palette_step(
            old, {**old, "brightness_pct": 50, "area_brightness_pct": 50}
        )
        self.assertEqual((step, from_pct), ("relight", 0))
        step, _ = self._palette_step(old, {**old, "area_colors": {"x": [0, 0, 255]}})
        self.assertEqual(step, "paint")
        # The first decision after the sensor was off has no level to scale.
        step, _ = self._palette_step(
            {}, {"area_colors": {}, "brightness_pct": 70, "area_brightness_pct": 70}
        )
        self.assertEqual(step, "relight")

    def test_attribute_noise_never_queues_a_run(self) -> None:
        state = {
            "area_colors": {"floor_lamp": [0, 0, 255]},
            "brightness_pct": 70,
            "area_brightness_pct": 70,
        }
        self.assertFalse(self._passes(state, {**state, "next_ready": "A - B"}))
        self.assertTrue(self._passes(state, {**state, "area_brightness_pct": 80}))
        self.assertTrue(
            self._passes(state, {**state, "area_colors": {"floor_lamp": [1, 2, 3]}})
        )
        self.assertFalse(
            self._passes(state, {**state, "area_brightness_pct": 80}, mode="off")
        )
        # The sensor's off state carries no colours.
        self.assertFalse(self._passes(state, {}))
        # Other triggers always run.
        template = self.automation["conditions"][0]["value_template"]
        self.assertTrue(_render(template, trigger={"id": "mode_off"}))

    def test_section_moves_bulbs_at_the_music_level_and_holds_hand_set_ones(
        self,
    ) -> None:
        # 70% is 178/255; bulbs report a level or two off what was sent.
        following, held = self._section_lights(
            {
                "light.at_level": 178,
                "light.rounded": 180,
                "light.by_hand": 250,
                "light.dimmed": 60,
                "light.no_level": None,
                # A name inside another must not be mistaken for it.
                "light.lamp": 250,
                "light.lamp_2": 178,
            },
            "70",
        )
        self.assertEqual(
            following,
            ["light.at_level", "light.rounded", "light.no_level", "light.lamp_2"],
        )
        self.assertEqual(held, ["light.by_hand", "light.dimmed", "light.lamp"])

    def test_hand_set_bulbs_come_back_unchanged_after_a_loud_part(self) -> None:
        # The old ratio scaling clipped 250 at 255 on the way up and brought
        # it back to 222. Held, it is never touched, so there is no drift.
        _, held = self._section_lights({"light.by_hand": 250}, "70")
        self.assertEqual(held, ["light.by_hand"])
        _, held = self._section_lights({"light.by_hand": 250}, "80")
        self.assertEqual(held, ["light.by_hand"])

    def test_idle_soundbar_ends_music_mode(self) -> None:
        trigger = next(t for t in self.automation["triggers"] if t["id"] == "idle")
        self.assertEqual(trigger["for"], "00:15:00")
        states = {
            "input_boolean.shared_music_mode": "on",
            "media_player.homelab_05_speaker": "paused",
        }

        def fires() -> bool:
            return _render(
                trigger["value_template"],
                is_state=lambda entity, state: states[entity] == state,
            )

        self.assertTrue(fires())
        states["media_player.homelab_05_speaker"] = "playing"
        self.assertFalse(fires())
        states.update(
            {
                "input_boolean.shared_music_mode": "off",
                "media_player.homelab_05_speaker": "idle",
            }
        )
        self.assertFalse(fires())
        branch = next(
            b["sequence"]
            for b in self.automation["actions"][1]["choose"]
            if {"condition": "trigger", "id": "idle"} in b["conditions"]
        )
        self.assertEqual(
            branch,
            [
                {
                    "action": "input_boolean.turn_off",
                    "target": {"entity_id": "input_boolean.shared_music_mode"},
                }
            ],
        )


class TestMusicModeEndsBeforeSharedLightChanges(unittest.TestCase):
    """Lights Off, Lights On and the empty house end Music Mode and wait for
    its restore before they snapshot or switch the lights."""

    END = "script.shared_music_mode_end"

    def test_end_script_turns_off_and_waits_for_the_restore(self) -> None:
        script = yaml.safe_load(
            (
                REPO_ROOT / "home-assistant" / "scripts" / "shared_music_mode_end.yaml"
            ).read_text(encoding="utf-8")
        )
        sequence = script["sequence"]
        self.assertEqual(sequence[0]["condition"], "state")
        self.assertEqual(sequence[0]["state"], "on")
        self.assertEqual(sequence[1]["action"], "input_boolean.turn_off")
        wait = sequence[-1]
        self.assertTrue(wait["continue_on_timeout"])
        # The waited-on automation is the one the alias names.
        alias = yaml.safe_load(
            (
                REPO_ROOT / "home-assistant" / "automations" / "shared_music_mode.yaml"
            ).read_text(encoding="utf-8")
        )["alias"]
        entity = "automation." + re.sub(r"[^a-z0-9]+", "_", alias.lower()).strip("_")
        self.assertIn(f"state_attr('{entity}', 'current')", wait["wait_template"])

    def _first_actions(self, steps: list) -> list[str]:
        return [step.get("action", "") for step in steps if isinstance(step, dict)]

    def test_shared_light_scripts_end_music_mode_first(self) -> None:
        for name in ("shared_lights_off", "shared_lights_on"):
            with self.subTest(name=name):
                script = yaml.safe_load(
                    (
                        REPO_ROOT / "home-assistant" / "scripts" / f"{name}.yaml"
                    ).read_text(encoding="utf-8")
                )
                self.assertEqual(script["sequence"][0].get("action"), self.END)

    def test_empty_house_ends_music_mode_before_its_snapshot(self) -> None:
        automation = yaml.safe_load(
            (
                REPO_ROOT
                / "home-assistant"
                / "automations"
                / "shared_spaces_empty_lights_off.yaml"
            ).read_text(encoding="utf-8")
        )
        for trigger_id in ("left_empty", "restart"):
            with self.subTest(trigger=trigger_id):
                branch = next(
                    b["sequence"]
                    for b in automation["actions"][0]["choose"]
                    if {"condition": "trigger", "id": trigger_id} in b["conditions"]
                )
                self.assertEqual(self._first_actions(branch)[0], self.END)


if __name__ == "__main__":
    unittest.main()
