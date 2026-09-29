from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

try:
    import jinja2

    HAS_JINJA2 = True
except ImportError:
    jinja2 = None  # type: ignore[assignment]
    HAS_JINJA2 = False

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_FILE = Path(__file__).resolve().parent / "jarvis_voice_eval_corpus.yaml"
TERSE_FILE = (
    REPO_ROOT / "home-assistant" / "custom_sentences" / "en" / "jarvis_terse.yaml"
)
CORE_FILE = REPO_ROOT / "home-assistant" / "core" / "configuration.yaml"
COLORS_FILE = (
    REPO_ROOT / "home-assistant" / "custom_sentences" / "en" / "jarvis_colors.yaml"
)

LOCAL_INTENTS = {"HassTurnOn", "HassTurnOff", "HassToggle", "HassLightSet"}
SIGNATURE_INTENT = "JarvisSignatureColor"
MUSIC_AUTOMATION = (
    REPO_ROOT / "home-assistant" / "automations" / "jarvis_voice_music_playback.yaml"
)
MUSIC_STOP_AUTOMATION = (
    REPO_ROOT / "home-assistant" / "automations" / "jarvis_music_stop.yaml"
)
MUSIC_SKIP_AUTOMATION = (
    REPO_ROOT / "home-assistant" / "automations" / "jarvis_music_skip.yaml"
)
MUSIC_VOLUME_AUTOMATION = (
    REPO_ROOT / "home-assistant" / "automations" / "jarvis_music_volume.yaml"
)
MUSIC_PREVIOUS_AUTOMATION = (
    REPO_ROOT / "home-assistant" / "automations" / "jarvis_music_previous.yaml"
)
MUSIC_RESUME_AUTOMATION = (
    REPO_ROOT / "home-assistant" / "automations" / "jarvis_music_resume.yaml"
)
MUSIC_MODES_AUTOMATION = (
    REPO_ROOT / "home-assistant" / "automations" / "jarvis_music_modes.yaml"
)
PLAY_MEDIA_SCRIPT = REPO_ROOT / "home-assistant" / "scripts" / "jarvis_play_media.yaml"
MUSIC_STOP_INTENT = "MusicStop"
MUSIC_SKIP_INTENT = "MusicSkip"
MUSIC_VOLUME_INTENT = "MusicVolume"
MUSIC_PREVIOUS_INTENT = "MusicPrevious"
MUSIC_RESUME_INTENT = "MusicResume"
MUSIC_MODES_INTENT = "MusicModes"
CLIMATE_AUTOMATION = (
    REPO_ROOT / "home-assistant" / "automations" / "jarvis_climate_control.yaml"
)
ROUTINES_AUTOMATION = (
    REPO_ROOT / "home-assistant" / "automations" / "jarvis_routines.yaml"
)
CLIMATE_INTENT = "JarvisClimateControl"
ROUTINES_INTENT = "JarvisRoutines"
SATELLITE_MEDIA_PLAYER = "media_player.homelab_05_satellite_media_player_2"
VOICE_EXTRA_TARGETS = frozenset({SATELLITE_MEDIA_PLAYER})
MUSIC_INTENTS = {
    "MusicArtist": "artist",
    "MusicPlaylist": "playlist",
    "MusicYoutubeArtist": "youtube_artist",
    "MusicYoutubeTrack": "youtube_track",
}
HOME_FILE = (
    REPO_ROOT / "home-assistant" / "custom_sentences" / "en" / "jarvis_home.yaml"
)
CONTROL_FILE = (
    REPO_ROOT
    / "home-assistant"
    / "custom_sentences"
    / "en"
    / "jarvis_light_control.yaml"
)
NUDGE_FILE = (
    REPO_ROOT / "home-assistant" / "custom_sentences" / "en" / "jarvis_nudge.yaml"
)
DONE_SCRIPTS = {
    "JarvisBrightnessNudge",
    "JarvisMovieMode",
    "JarvisCookingMode",
    "JarvisDinnerMode",
}
ALIAS_INTENTS = {
    "JarvisAliasOn": "light.turn_on",
    "JarvisAliasOff": "light.turn_off",
    "JarvisAliasColor": "light.turn_on",
}
DEFAULT_LIGHTS_INTENTS = {
    "JarvisDefaultLightsOn": "light.turn_on",
    "JarvisDefaultLightsOff": "light.turn_off",
    "JarvisDefaultLightsColor": "light.turn_on",
}
DEFAULT_LIGHTS_TARGET = "light.downstairs_lights"
ALIAS_COLOR_INTENT = "JarvisAliasColor"
ALIAS_FILE = (
    REPO_ROOT / "home-assistant" / "custom_sentences" / "en" / "jarvis_alias.yaml"
)
TEMPLATE_QUERIES = {
    "JarvisWhatsPlaying": "media_player.homelab_05_satellite_media_player_2",
    "JarvisTempDownstairs": "sensor.living_room_ac_ambient_temperature_degf",
    "JarvisKitchenLightsState": "light.kitchen_lights",
    "JarvisAcState": "sensor.living_room_ac_mode",
    "JarvisWeatherQuery": "weather.forecast_home",
    "JarvisRainQuery": "weather.forecast_home",
    "JarvisPrintStatus": "sensor.a1_03900d642327265_print_status",
}
BUILTIN_DYNAMIC = {"HassShoppingListAddItem"}
NEON_PINK_RGB = [255, 16, 240]
MIN_CASES = 30
MAX_CASES = 80


def _parse_template(template: str) -> list:
    parts: list = []
    buf = ""

    def flush() -> None:
        nonlocal buf
        for word in buf.split():
            parts.append(("lit", word.lower()))
        buf = ""

    i = 0
    while i < len(template):
        ch = template[i]
        if ch in "[{(":
            closer = {"[": "]", "{": "}", "(": ")"}[ch]
            j = template.index(closer, i)
            flush()
            inner = template[i + 1 : j]
            if ch == "{":
                parts.append(("slot",))
            elif ch == "(":
                parts.append(
                    (
                        "alt",
                        [[w.lower() for w in c.split()] for c in inner.split("|")],
                    )
                )
            elif "|" in inner:
                parts.append(
                    (
                        "optalt",
                        [[w.lower() for w in c.split()] for c in inner.split("|")],
                    )
                )
            else:
                parts.append(("opt", [w.lower() for w in inner.split()]))
            i = j + 1
        else:
            buf += ch
            i += 1
    flush()
    return parts


def _consume(part: tuple, words: list, pos: int) -> set:
    kind = part[0]
    if kind == "lit":
        if pos < len(words) and words[pos] == part[1]:
            return {pos + 1}
        return set()
    if kind == "slot":
        return set(range(pos + 1, len(words) + 1))
    if kind in ("alt", "optalt"):
        out = {pos} if kind == "optalt" else set()
        for choice in part[1]:
            current = pos
            for word in choice:
                if current < len(words) and words[current] == word:
                    current += 1
                else:
                    break
            else:
                out.add(current)
        return out
    if kind == "opt":
        current = pos
        for word in part[1]:
            if current < len(words) and words[current] == word:
                current += 1
            else:
                return {pos}
        return {pos, current}
    raise AssertionError(f"unknown part {kind}")


def sentence_matches(template: str, say: str) -> bool:
    """Match an utterance against a hassil-style sentence template."""
    words = [w.strip(".,?!") for w in say.lower().split()]
    positions = {0}
    for part in _parse_template(template):
        following = set()
        for pos in positions:
            following.update(_consume(part, words, pos))
        positions = following
        if not positions:
            return False
    return len(words) in positions


def ha_loader() -> type[yaml.SafeLoader]:
    loader = type("HALoader", (yaml.SafeLoader,), {})
    loader.add_multi_constructor("!", lambda ldr, suffix, node: None)
    return loader


def known_entity_ids() -> set[str]:
    core = yaml.load(CORE_FILE.read_text(encoding="utf-8"), Loader=ha_loader())
    known: set[str] = set()
    for group in core.get("light", []):
        entities = group.get("entities", [])
        entity_id = group.get("entity_id")
        if isinstance(entity_id, str):
            known.add(entity_id)
        if isinstance(entities, list):
            known.update(e for e in entities if isinstance(e, str))
        name = group.get("name")
        if isinstance(name, str):
            slug = name.lower().replace(" ", "_")
            known.add(f"light.{slug}")
            known.add(f"switch.{slug}")
    for bridge in core.get("homekit", []):
        filt = bridge.get("filter", {}).get("include_entities", [])
        known.update(e for e in filt if isinstance(e, str))
    scripts_dir = REPO_ROOT / "home-assistant" / "scripts"
    if scripts_dir.is_dir():
        known.update(f"script.{f.stem}" for f in scripts_dir.glob("*.yaml"))
    scenes_dir = REPO_ROOT / "home-assistant" / "scenes"
    if scenes_dir.is_dir():
        for f in scenes_dir.glob("*.yaml"):
            try:
                name = yaml.safe_load(f.read_text(encoding="utf-8")).get("name", f.stem)
            except yaml.YAMLError:
                name = f.stem
            slug = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")
            known.add(f"scene.{slug}")
            known.add(f"scene.{f.stem}")
    return known


class EvalCorpusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = yaml.safe_load(CORPUS_FILE.read_text(encoding="utf-8"))
        cls.terse = yaml.safe_load(TERSE_FILE.read_text(encoding="utf-8"))
        cls.colors = yaml.safe_load(COLORS_FILE.read_text(encoding="utf-8"))
        cls.home = yaml.safe_load(HOME_FILE.read_text(encoding="utf-8"))
        cls.nudge = yaml.safe_load(NUDGE_FILE.read_text(encoding="utf-8"))
        cls.alias = yaml.safe_load(ALIAS_FILE.read_text(encoding="utf-8"))
        cls.core = yaml.load(CORE_FILE.read_text(encoding="utf-8"), Loader=ha_loader())
        cls.known = known_entity_ids()

    def test_corpus_size(self) -> None:
        self.assertGreaterEqual(len(self.cases), MIN_CASES)
        self.assertLessEqual(len(self.cases), MAX_CASES)

    def test_ids_unique(self) -> None:
        ids = [c["id"] for c in self.cases]
        self.assertEqual(len(ids), len(set(ids)))

    def test_schema(self) -> None:
        for case in self.cases:
            self.assertIn(case["path"], ("local", "llm"), case["id"])
            self.assertTrue(case["say"].strip().rstrip(".").strip(), case["id"])
            if case["path"] == "local":
                self.assertIn(
                    case["intent"],
                    LOCAL_INTENTS
                    | {SIGNATURE_INTENT}
                    | set(MUSIC_INTENTS)
                    | {
                        MUSIC_STOP_INTENT,
                        MUSIC_SKIP_INTENT,
                        MUSIC_VOLUME_INTENT,
                        MUSIC_PREVIOUS_INTENT,
                        MUSIC_RESUME_INTENT,
                        MUSIC_MODES_INTENT,
                        CLIMATE_INTENT,
                        ROUTINES_INTENT,
                    }
                    | DONE_SCRIPTS
                    | set(TEMPLATE_QUERIES)
                    | BUILTIN_DYNAMIC
                    | set(ALIAS_INTENTS)
                    | set(DEFAULT_LIGHTS_INTENTS),
                    case["id"],
                )
                if case.get("dynamic"):
                    self.assertEqual(case["targets"], [], case["id"])
                else:
                    self.assertTrue(case["targets"], case["id"])
                self.assertTrue(case["response"].strip(), case["id"])
            else:
                self.assertTrue(case["tool"], case["id"])
                self.assertTrue(case.get("notes", "").strip(), case["id"])

    def test_local_responses_are_terse(self) -> None:
        for case in self.cases:
            if case["path"] != "local" or case.get("dynamic"):
                continue
            self.assertLessEqual(len(case["response"].split()), 3, case["id"])

    def test_local_intents_have_terse_override(self) -> None:
        overrides = self.terse.get("responses", {}).get("intents", {})
        for case in self.cases:
            if case["path"] != "local" or case["intent"] not in LOCAL_INTENTS:
                continue
            self.assertIn(case["intent"], overrides, case["id"])
            self.assertEqual(
                overrides[case["intent"]].get("default"), "Done.", case["id"]
            )
            self.assertEqual(case["response"], "Done.", case["id"])

    def test_light_set_slot_responses_are_terse(self) -> None:
        light_set = self.terse["responses"]["intents"]["HassLightSet"]
        for slot in ("brightness", "color", "temperature"):
            self.assertEqual(light_set.get(slot), "Done.", slot)

    def test_builtin_sentence_extensions(self) -> None:
        control = yaml.safe_load(CONTROL_FILE.read_text(encoding="utf-8"))
        sentences = control["intents"]["HassLightSet"]["data"][0]["sentences"]
        self.assertTrue(any("dim" in s for s in sentences), "dim phrasing present")
        self.assertTrue(
            any("brighten" in s for s in sentences), "brighten phrasing present"
        )
        for sentence in sentences:
            self.assertIn("{name}", sentence)
            if "dim" in sentence or "brighten" in sentence:
                self.assertIn("{brightness}", sentence)
        toggle = control["intents"]["JarvisToggle"]["data"][0]["sentences"]
        self.assertTrue(any("toggle" in s for s in toggle), "toggle phrasing present")
        for sentence in toggle:
            self.assertIn("{name}", sentence)
        temp = control["intents"]["HassLightSet"]["data"][0]["sentences"]
        self.assertTrue(
            any("color_temperature_names" in s for s in temp),
            "temperature-name phrasing present",
        )
        full = control["intents"]["JarvisBrightenFull"]["data"][0]["sentences"]
        self.assertTrue(
            any("full brightness" in s for s in full),
            "full-brightness phrasing present",
        )

    def test_toggle_and_brighten_scripts_exist(self) -> None:
        scripts = self.core["intent_script"]
        toggle = scripts["JarvisToggle"]
        self.assertEqual(toggle["action"][0]["service"], "homeassistant.toggle")
        self.assertIn("targets.entities", toggle["action"][0]["target"]["entity_id"])
        self.assertEqual(toggle["speech"]["text"], "Done.")
        full = scripts["JarvisBrightenFull"]
        self.assertEqual(full["action"][0]["service"], "light.turn_on")
        self.assertEqual(full["action"][0]["data"]["brightness_pct"], 100)
        self.assertEqual(full["speech"]["text"], "Done.")

    def test_signature_color_palette(self) -> None:
        self.assertEqual(self.colors.get("language"), "en")
        light_lists = {v["out"] for v in self.colors["lists"]["jarvis_light"]["values"]}
        color_lists = {v["out"] for v in self.colors["lists"]["jarvis_color"]["values"]}
        self.assertIn("neon_pink", color_lists)
        for entity_id in light_lists:
            self.assertIn(entity_id, self.known, entity_id)
        script = self.core["intent_script"][SIGNATURE_INTENT]
        self.assertEqual(script["speech"]["text"], "Done.")
        actions = script["action"]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["service"], "light.turn_on")
        self.assertEqual(actions[0]["data"]["rgb_color"], NEON_PINK_RGB)

    def test_signature_color_cases_match_palette(self) -> None:
        light_lists = {v["out"] for v in self.colors["lists"]["jarvis_light"]["values"]}
        for case in self.cases:
            if case.get("intent") != SIGNATURE_INTENT:
                continue
            self.assertEqual(case["response"], "Done.", case["id"])
            for entity_id in case["targets"]:
                self.assertIn(entity_id, light_lists, f"{case['id']}: {entity_id}")

    def test_local_targets_exist_in_source(self) -> None:
        allowed = self.known | VOICE_EXTRA_TARGETS
        for case in self.cases:
            if case["path"] != "local":
                continue
            for entity_id in case["targets"]:
                self.assertIn(entity_id, allowed, f"{case['id']}: {entity_id}")

    def test_music_stop_cases_match_trigger_sentences(self) -> None:
        automation = yaml.safe_load(MUSIC_STOP_AUTOMATION.read_text(encoding="utf-8"))
        by_id = {
            t.get("id"): t.get("command", []) for t in automation.get("triggers", [])
        }
        self.assertTrue(by_id.get("stop"), "stop trigger present")
        actions = automation.get("actions", [])
        pause_call = next(
            a for a in actions if a.get("action") == "media_player.media_pause"
        )
        self.assertEqual(pause_call["target"]["entity_id"], SATELLITE_MEDIA_PLAYER)
        response = next(
            a["set_conversation_response"]
            for a in actions
            if "set_conversation_response" in a
        )
        self.assertIn("Paused.", response)
        for case in self.cases:
            if case.get("intent") != MUSIC_STOP_INTENT:
                continue
            say = case["say"].strip().rstrip(".?!")
            matched = any(sentence_matches(template, say) for template in by_id["stop"])
            self.assertTrue(matched, f"{case['id']}: {say!r} matches no stop sentence")
            self.assertEqual(case["response"], "Paused.", case["id"])
            self.assertEqual(case["targets"], [SATELLITE_MEDIA_PLAYER], case["id"])

    def test_music_skip_cases_match_trigger_sentences(self) -> None:
        automation = yaml.safe_load(MUSIC_SKIP_AUTOMATION.read_text(encoding="utf-8"))
        by_id = {
            t.get("id"): t.get("command", []) for t in automation.get("triggers", [])
        }
        self.assertTrue(by_id.get("skip"), "skip trigger present")
        actions = automation.get("actions", [])
        next_call = next(
            a for a in actions if a.get("action") == "media_player.media_next_track"
        )
        self.assertEqual(next_call["target"]["entity_id"], SATELLITE_MEDIA_PLAYER)
        response = next(
            a["set_conversation_response"]
            for a in actions
            if "set_conversation_response" in a
        )
        self.assertIn("Done.", response)
        for case in self.cases:
            if case.get("intent") != MUSIC_SKIP_INTENT:
                continue
            say = case["say"].strip().rstrip(".?!")
            matched = any(sentence_matches(template, say) for template in by_id["skip"])
            self.assertTrue(matched, f"{case['id']}: {say!r} matches no skip sentence")
            self.assertEqual(case["response"], "Done.", case["id"])
            self.assertEqual(case["targets"], [SATELLITE_MEDIA_PLAYER], case["id"])

    def test_music_previous_cases_match_trigger_sentences(self) -> None:
        automation = yaml.safe_load(
            MUSIC_PREVIOUS_AUTOMATION.read_text(encoding="utf-8")
        )
        by_id = {
            t.get("id"): t.get("command", []) for t in automation.get("triggers", [])
        }
        self.assertTrue(by_id.get("previous"), "previous trigger present")
        actions = automation.get("actions", [])
        prev_call = next(
            a for a in actions if a.get("action") == "media_player.media_previous_track"
        )
        self.assertEqual(prev_call["target"]["entity_id"], SATELLITE_MEDIA_PLAYER)
        response = next(
            a["set_conversation_response"]
            for a in actions
            if "set_conversation_response" in a
        )
        self.assertIn("Done.", response)
        for case in self.cases:
            if case.get("intent") != MUSIC_PREVIOUS_INTENT:
                continue
            say = case["say"].strip().rstrip(".?!")
            matched = any(
                sentence_matches(template, say) for template in by_id["previous"]
            )
            self.assertTrue(
                matched, f"{case['id']}: {say!r} matches no previous sentence"
            )
            self.assertEqual(case["response"], "Done.", case["id"])
            self.assertEqual(case["targets"], [SATELLITE_MEDIA_PLAYER], case["id"])

    def test_music_resume_cases_match_trigger_sentences(self) -> None:
        automation = yaml.safe_load(MUSIC_RESUME_AUTOMATION.read_text(encoding="utf-8"))
        by_id = {
            t.get("id"): t.get("command", []) for t in automation.get("triggers", [])
        }
        self.assertTrue(by_id.get("resume"), "resume trigger present")
        actions = automation.get("actions", [])
        play_call = next(
            a for a in actions if a.get("action") == "media_player.media_play"
        )
        self.assertEqual(play_call["target"]["entity_id"], SATELLITE_MEDIA_PLAYER)
        response = next(
            a["set_conversation_response"]
            for a in actions
            if "set_conversation_response" in a
        )
        self.assertIn("Done.", response)
        for case in self.cases:
            if case.get("intent") != MUSIC_RESUME_INTENT:
                continue
            say = case["say"].strip().rstrip(".?!")
            matched = any(
                sentence_matches(template, say) for template in by_id["resume"]
            )
            self.assertTrue(
                matched, f"{case['id']}: {say!r} matches no resume sentence"
            )
            self.assertEqual(case["response"], "Done.", case["id"])
            self.assertEqual(case["targets"], [SATELLITE_MEDIA_PLAYER], case["id"])

    def test_music_volume_cases_match_trigger_sentences(self) -> None:
        automation = yaml.safe_load(MUSIC_VOLUME_AUTOMATION.read_text(encoding="utf-8"))
        by_id = {
            t.get("id"): t.get("command", []) for t in automation.get("triggers", [])
        }
        all_templates = [tmpl for commands in by_id.values() for tmpl in commands]
        for case in self.cases:
            if case.get("intent") != MUSIC_VOLUME_INTENT:
                continue
            say = case["say"].strip().rstrip(".?!")
            matched = any(sentence_matches(template, say) for template in all_templates)
            self.assertTrue(
                matched, f"{case['id']}: {say!r} matches no volume sentence"
            )
            self.assertEqual(case["response"], "Done.", case["id"])
            self.assertEqual(case["targets"], [SATELLITE_MEDIA_PLAYER], case["id"])

    def test_music_modes_cases_match_trigger_sentences(self) -> None:
        automation = yaml.safe_load(MUSIC_MODES_AUTOMATION.read_text(encoding="utf-8"))
        by_id = {
            t.get("id"): t.get("command", []) for t in automation.get("triggers", [])
        }
        all_templates = [tmpl for commands in by_id.values() for tmpl in commands]
        for case in self.cases:
            if case.get("intent") != MUSIC_MODES_INTENT:
                continue
            say = case["say"].strip().rstrip(".?!")
            matched = any(sentence_matches(template, say) for template in all_templates)
            self.assertTrue(matched, f"{case['id']}: {say!r} matches no mode sentence")
            self.assertEqual(case["response"], "Done.", case["id"])
            self.assertEqual(case["targets"], [SATELLITE_MEDIA_PLAYER], case["id"])

    def test_climate_cases_match_trigger_sentences(self) -> None:
        automation = yaml.safe_load(CLIMATE_AUTOMATION.read_text(encoding="utf-8"))
        by_id = {
            t.get("id"): t.get("command", []) for t in automation.get("triggers", [])
        }
        all_templates = [tmpl for commands in by_id.values() for tmpl in commands]
        for case in self.cases:
            if case.get("intent") != CLIMATE_INTENT:
                continue
            say = case["say"].strip().rstrip(".?!")
            matched = any(sentence_matches(template, say) for template in all_templates)
            self.assertTrue(
                matched, f"{case['id']}: {say!r} matches no climate sentence"
            )
            self.assertEqual(case["response"], "Done.", case["id"])
            self.assertEqual(
                case["targets"],
                ["climate.living_room_ac_living_room_ac_thermostat"],
                case["id"],
            )

    def test_routines_cases_match_trigger_sentences(self) -> None:
        automation = yaml.safe_load(ROUTINES_AUTOMATION.read_text(encoding="utf-8"))
        by_id = {
            t.get("id"): t.get("command", []) for t in automation.get("triggers", [])
        }
        all_templates = [tmpl for commands in by_id.values() for tmpl in commands]
        for case in self.cases:
            if case.get("intent") != ROUTINES_INTENT:
                continue
            say = case["say"].strip().rstrip(".?!")
            matched = any(sentence_matches(template, say) for template in all_templates)
            self.assertTrue(
                matched, f"{case['id']}: {say!r} matches no routine sentence"
            )
            self.assertEqual(case["targets"], ["light.downstairs_lights"], case["id"])

    def test_done_scripts(self) -> None:
        scripts = self.core["intent_script"]
        nudge = scripts["JarvisBrightnessNudge"]
        self.assertEqual(nudge["speech"]["text"], "Done.")
        flat = json.dumps(nudge["action"])
        self.assertIn("light.downstairs_lights", flat)
        self.assertIn("brightness_step_pct", flat)
        movie = scripts["JarvisMovieMode"]
        self.assertEqual(movie["speech"]["text"], "Done.")
        self.assertEqual(
            movie["action"][0]["target"]["entity_id"], "scene.movie_low_living_room"
        )
        cooking = scripts["JarvisCookingMode"]
        self.assertEqual(cooking["speech"]["text"], "Done.")
        self.assertEqual(
            cooking["action"][0]["target"]["entity_id"], "scene.cooking_bright_kitchen"
        )
        dinner = scripts["JarvisDinnerMode"]
        self.assertEqual(dinner["speech"]["text"], "Done.")
        self.assertEqual(
            dinner["action"][0]["target"]["entity_id"], "scene.dinner_warm_dim"
        )
        directions = {
            v["in"]: v["out"] for v in self.nudge["lists"]["jarvis_direction"]["values"]
        }
        self.assertEqual(directions, {"darker": "decrease", "brighter": "increase"})

    def test_template_queries(self) -> None:
        scripts = self.core["intent_script"]
        for intent, entity_id in TEMPLATE_QUERIES.items():
            speech = scripts[intent]["speech"]["text"]
            self.assertTrue("{{" in speech or "{%" in speech, intent)
            self.assertIn(entity_id, speech, intent)
        for case in self.cases:
            if case.get("intent") not in TEMPLATE_QUERIES:
                continue
            self.assertTrue(case.get("dynamic"), case["id"])

    def test_kitchen_lights_group_covers_all_kitchen_lights(self) -> None:
        kitchen_group = next(
            group
            for group in self.core["light"]
            if group.get("name") == "Kitchen Lights"
        )
        self.assertEqual(
            set(kitchen_group["entities"]),
            {
                "light.kitchen_light_lan",
                "light.kitchen_light_2_lan",
                "light.kitchen_mushroom_lamp",
                "light.kitchen_fuck_off_sign",
            },
        )

    def test_builtin_dynamic(self) -> None:
        cases = [c for c in self.cases if c.get("intent") in BUILTIN_DYNAMIC]
        self.assertTrue(cases)
        for case in cases:
            self.assertTrue(case.get("dynamic"), case["id"])

    def test_alias_routing(self) -> None:
        self.assertEqual(self.alias.get("language"), "en")
        outs = {v["out"] for v in self.alias["lists"]["jarvis_alias"]["values"]}
        self.assertEqual(outs, {"light.bedroom_roku_lights", "light.all_govee_lights"})
        for entity_id in outs:
            self.assertIn(entity_id, self.known, entity_id)
        scripts = self.core["intent_script"]
        for intent, service in ALIAS_INTENTS.items():
            entry = scripts[intent]
            self.assertEqual(entry["speech"]["text"], "Done.")
            self.assertEqual(entry["action"][0]["service"], service)

    def test_alias_color_cases_match_lists(self) -> None:
        colors = {
            v["in"] for v in self.alias["lists"]["jarvis_general_color"]["values"]
        }
        script = self.core["intent_script"][ALIAS_COLOR_INTENT]
        self.assertEqual(script["speech"]["text"], "Done.")
        self.assertEqual(script["action"][0]["service"], "light.turn_on")
        self.assertIn("jarvis_general_color", script["action"][0]["data"]["color_name"])
        for case in self.cases:
            if case.get("intent") != ALIAS_COLOR_INTENT:
                continue
            self.assertEqual(case["response"], "Done.", case["id"])
            say = case["say"].strip().rstrip(".?!").lower()
            self.assertIn(say.rsplit(" ", 1)[-1], colors, case["id"])

    def test_default_lights_cases_match_sentences(self) -> None:
        control = yaml.safe_load(CONTROL_FILE.read_text(encoding="utf-8"))
        sentences: dict[str, list[str]] = {}
        for intent in DEFAULT_LIGHTS_INTENTS:
            source = self.alias if intent == "JarvisDefaultLightsColor" else control
            for block in source["intents"][intent]["data"]:
                sentences.setdefault(intent, []).extend(block["sentences"])
        scripts = self.core["intent_script"]
        for intent, service in DEFAULT_LIGHTS_INTENTS.items():
            entry = scripts[intent]
            self.assertEqual(entry["speech"]["text"], "Done.", intent)
            self.assertEqual(entry["action"][0]["service"], service, intent)
            self.assertEqual(
                entry["action"][0]["target"]["entity_id"],
                DEFAULT_LIGHTS_TARGET,
                intent,
            )
        for case in self.cases:
            if case.get("intent") not in DEFAULT_LIGHTS_INTENTS:
                continue
            say = case["say"].strip().rstrip(".?!")
            matched = any(
                sentence_matches(template, say)
                for template in sentences[case["intent"]]
            )
            self.assertTrue(matched, f"{case['id']}: {say!r} matches no sentence")
            self.assertEqual(case["response"], "Done.", case["id"])
            self.assertEqual(case["targets"], [DEFAULT_LIGHTS_TARGET], case["id"])
        govee_mishearings = {
            "gobi",
            "gobi lights",
            "all gobi lights",
            "all the gobi lights",
            "gopi",
            "gopi lights",
            "all gopi lights",
            "all the gopi lights",
        }
        alias_values = {
            v["in"]: v["out"] for v in self.alias["lists"]["jarvis_alias"]["values"]
        }
        light_values = {
            v["in"]: v["out"] for v in self.colors["lists"]["jarvis_light"]["values"]
        }
        for heard in govee_mishearings:
            self.assertEqual(alias_values.get(heard), "light.all_govee_lights", heard)
            self.assertEqual(light_values.get(heard), "light.all_govee_lights", heard)

    def test_home_sentences_match_cases(self) -> None:
        sentences: dict[str, list[str]] = {}
        for source in (self.home, self.nudge, self.alias):
            self.assertEqual(source.get("language"), "en")
            for intent, body in source.get("intents", {}).items():
                for block in body.get("data", []):
                    sentences.setdefault(intent, []).extend(block["sentences"])
        for case in self.cases:
            if case.get("intent") not in DONE_SCRIPTS | set(TEMPLATE_QUERIES) | set(
                ALIAS_INTENTS
            ):
                continue
            say = case["say"].strip().rstrip(".?!")
            matched = any(
                sentence_matches(template, say)
                for template in sentences[case["intent"]]
            )
            self.assertTrue(matched, f"{case['id']}: {say!r} matches no sentence")

    def test_language_is_english(self) -> None:
        self.assertEqual(self.terse.get("language"), "en")

    def test_music_cases_match_trigger_sentences(self) -> None:
        automation = yaml.safe_load(MUSIC_AUTOMATION.read_text(encoding="utf-8"))
        by_id = {
            t.get("id"): t.get("command", []) for t in automation.get("triggers", [])
        }
        actions = automation.get("actions", [])
        response = next(
            a["set_conversation_response"]
            for a in actions
            if "set_conversation_response" in a
        )
        self.assertIn("Done,", response)
        self.assertIn("playback", response)
        script_call = next(
            a for a in actions if a.get("action") == "script.jarvis_play_media"
        )
        self.assertEqual(script_call.get("response_variable"), "playback")
        self.assertTrue(script_call.get("continue_on_error"))
        self.assertIn("trigger.id", script_call["data"]["media_content_type"])
        self.assertIn("youtube_music", script_call["data"]["platform"])
        for trigger_id in MUSIC_INTENTS.values():
            self.assertTrue(by_id.get(trigger_id), trigger_id)
            self.assertIn(trigger_id, script_call["data"]["media_content_type"])
        for case in self.cases:
            if case.get("intent") not in MUSIC_INTENTS:
                continue
            trigger_id = MUSIC_INTENTS[case["intent"]]
            say = case["say"].strip().rstrip(".?!")
            matched = any(
                sentence_matches(template, say) for template in by_id[trigger_id]
            )
            self.assertTrue(
                matched, f"{case['id']}: {say!r} matches no {trigger_id} sentence"
            )
            self.assertEqual(case["response"], "Done.", case["id"])
            self.assertEqual(case["targets"], ["script.jarvis_play_media"], case["id"])

    def test_artist_branch_plays_endless_mix(self) -> None:
        script = yaml.safe_load(PLAY_MEDIA_SCRIPT.read_text(encoding="utf-8"))
        choose = next(step["choose"] for step in script["sequence"] if "choose" in step)
        artist_branch = next(
            branch
            for branch in choose
            if "artist" in json.dumps(branch.get("conditions", []))
        )
        play_calls = [
            action
            for action in artist_branch["sequence"]
            if action.get("action") == "music_assistant.play_media"
        ]
        self.assertTrue(play_calls, "artist branch uses music_assistant.play_media")
        for call in play_calls:
            self.assertEqual(call["data"]["media_type"], "artist")
            self.assertTrue(call["data"]["radio_mode"])
            self.assertEqual(call["data"]["enqueue"], "replace")
            self.assertIn("artist_uri", call["data"]["media_id"])
        self.assertNotIn("media_player.play_media", json.dumps(artist_branch))
        variable_steps = [
            step for step in artist_branch["sequence"] if "variables" in step
        ]
        self.assertEqual(len(variable_steps), 4)
        self.assertEqual(set(variable_steps[0]["variables"]), {"artist_uris"})
        self.assertEqual(set(variable_steps[1]["variables"]), {"artist_uri"})
        self.assertIn("artist_uris", variable_steps[1]["variables"]["artist_uri"])
        self.assertIn(
            "default({}, true)", variable_steps[0]["variables"]["artist_uris"]
        )
        self.assertEqual(set(variable_steps[2]["variables"]), {"artist_name"})
        self.assertEqual(
            set(variable_steps[3]["variables"]), {"confirm_kind", "confirm_needle"}
        )
        self.assertEqual(variable_steps[3]["variables"]["confirm_kind"].strip(), "name")

    def test_jarvis_play_media_autoplay(self) -> None:
        script = yaml.safe_load(PLAY_MEDIA_SCRIPT.read_text(encoding="utf-8"))
        fields = script.get("fields", {})
        self.assertIn("autoplay", fields)
        self.assertIn("boolean", fields["autoplay"]["selector"])
        self.assertTrue(fields["autoplay"].get("default"))

        # Verify variables derive use_radio_mode
        sequence = script.get("sequence", [])
        var_step = next(s for s in sequence if "variables" in s)
        vars_dict = var_step["variables"]
        self.assertIn("use_radio_mode", vars_dict)
        radio_expr = vars_dict["use_radio_mode"]
        self.assertIn("autoplay", radio_expr)
        self.assertIn("music", radio_expr)
        self.assertIn("track", radio_expr)

        # Verify media_player.play_media actions pass extra with radio_mode
        choose_step = next(s for s in sequence if "choose" in s)
        branches = choose_step["choose"]
        # Non-artist branches that play media
        for branch in branches:
            play_actions = [
                a
                for a in branch.get("sequence", [])
                if a.get("action") == "media_player.play_media"
            ]
            for action in play_actions:
                data = action.get("data", {})
                self.assertIn("extra", data)
                self.assertIn("radio_mode", data["extra"])

        default_actions = [
            a
            for a in choose_step.get("default", [])
            if a.get("action") == "media_player.play_media"
        ]
        self.assertTrue(default_actions)
        for action in default_actions:
            data = action.get("data", {})
            self.assertIn("extra", data)
            self.assertIn("radio_mode", data["extra"])

    def test_track_branch_plays_song_and_enqueues_artist(self) -> None:
        script = yaml.safe_load(PLAY_MEDIA_SCRIPT.read_text(encoding="utf-8"))
        choose = next(step["choose"] for step in script["sequence"] if "choose" in step)
        track_branch = next(
            branch
            for branch in choose
            if "track" in json.dumps(branch.get("conditions", []))
        )
        # Verify search for track
        search_action = next(
            a
            for a in track_branch["sequence"]
            if a.get("action") == "music_assistant.search"
        )
        self.assertEqual(search_action["data"]["media_type"], "track")

        # Verify track play action
        play_track_call = next(
            a
            for a in track_branch["sequence"]
            if a.get("action") == "music_assistant.play_media"
        )
        self.assertEqual(play_track_call["data"]["media_type"], "track")
        self.assertEqual(play_track_call["data"]["enqueue"], "replace")
        self.assertIn("track_uri", play_track_call["data"]["media_id"])

        # Verify conditional artist enqueue replace_next
        if_step = next(
            step
            for step in track_branch["sequence"]
            if (
                "if" in step
                and "artist_uri" in json.dumps(step["if"])
                and "use_radio_mode" in json.dumps(step["if"])
            )
        )
        self.assertIn("use_radio_mode", json.dumps(if_step["if"]))
        self.assertIn("artist_uri", json.dumps(if_step["if"]))
        artist_call = if_step["then"][0]
        self.assertEqual(artist_call["action"], "music_assistant.play_media")
        self.assertEqual(artist_call["data"]["media_type"], "artist")
        self.assertEqual(artist_call["data"]["enqueue"], "replace_next")
        self.assertIn("artist_uri", artist_call["data"]["media_id"])


class MusicFailureContractTest(unittest.TestCase):
    """Accepted requests are not reported as verified playback (A4/B4)."""

    def test_music_routes_default_to_spotify_when_identity_is_unknown(self) -> None:
        automation = yaml.safe_load(MUSIC_AUTOMATION.read_text(encoding="utf-8"))
        track_trigger = next(
            trigger
            for trigger in automation["triggers"]
            if trigger.get("id") == "track"
        )
        self.assertIn("play {query}", track_trigger["command"])
        self.assertIn("[speaker {speaker}] play {query}", track_trigger["command"])

        actions = automation["actions"]
        self.assertEqual(actions[0]["action"], "script.jarvis_play_media")
        platform = actions[0]["data"]["platform"]
        self.assertIn("trigger.slots.speaker | lower == 'sam'", platform)
        self.assertIn("else 'spotify'", platform)
        self.assertNotIn("I couldn't identify who is speaking", json.dumps(actions))

    def test_youtube_music_track_search_never_falls_back_to_spotify(self) -> None:
        script = yaml.safe_load(PLAY_MEDIA_SCRIPT.read_text(encoding="utf-8"))
        choose = next(step["choose"] for step in script["sequence"] if "choose" in step)
        track_branch = next(
            branch
            for branch in choose
            if "media_content_type in ['music', 'track']"
            in json.dumps(branch.get("conditions", []))
        )
        sequence = track_branch["sequence"]
        track_uri = next(
            step["variables"]["track_uri"]
            for step in sequence
            if "track_uri" in step.get("variables", {})
        )
        self.assertIn("default('', true)", track_uri)
        self.assertIn("No track result was found on", json.dumps(sequence))

    def test_play_media_serializes_queue_updates(self) -> None:
        script = yaml.safe_load(PLAY_MEDIA_SCRIPT.read_text(encoding="utf-8"))
        self.assertEqual(script.get("mode"), "queued")

    def test_play_media_distinguishes_accepted_from_verified(self) -> None:
        script = yaml.safe_load(PLAY_MEDIA_SCRIPT.read_text(encoding="utf-8"))
        flat = json.dumps(script["sequence"])
        self.assertNotIn("status: playing", flat)
        self.assertNotIn("queue_before", flat)
        self.assertNotIn("request_started", flat)
        self.assertNotIn("media_position", flat)
        self.assertNotIn("default(9999", flat)
        for branch, needle_source in (
            ("artist", "artist_name"),
            ("track", "track_uri"),
            ("youtube", "yt_uri"),
            ("default", "media_content_id"),
        ):
            with self.subTest(branch=branch):
                self.assertIn("confirm_kind", flat)
                self.assertIn("confirm_needle", flat)
                self.assertIn(needle_source, flat)
        repeat: dict = {}
        for step in script["sequence"]:
            if "repeat" in step:
                repeat = step["repeat"]
            for then in step.get("then", []):
                if "repeat" in then:
                    repeat = then["repeat"]
        self.assertTrue(repeat, "confirmation repeat block exists")
        self.assertNotIn("count", repeat)
        self.assertIn("repeat.index >= 6", repeat["until"][0]["value_template"])
        wait = next(step for step in repeat["sequence"] if "wait_for_trigger" in step)
        self.assertEqual(wait["timeout"], {"seconds": 1})
        self.assertTrue(wait.get("continue_on_timeout"))
        self.assertNotIn("seconds: 6", flat)
        until = json.dumps(repeat["until"])
        self.assertIn("confirm_needle", until)
        self.assertIn("media_content_id", until)
        result = next(
            step["variables"]["playback_result"]
            for step in script["sequence"]
            if isinstance(step, dict) and "playback_result" in step.get("variables", {})
        )
        self.assertIn("accepted", result["status"])
        self.assertIn("playing", result["status"])
        self.assertIn("confirm_needle", result["status"])
        self.assertIn("verified", result)
        stop = next(step for step in script["sequence"] if "stop" in step)
        self.assertEqual(stop.get("response_variable"), "playback_result")

    def test_playback_automation_never_claims_unreported_playback(self) -> None:
        automation = yaml.safe_load(MUSIC_AUTOMATION.read_text(encoding="utf-8"))
        actions = automation["actions"]
        response = next(
            a["set_conversation_response"]
            for a in actions
            if "set_conversation_response" in a
        )
        self.assertIn("playback.status", response)
        self.assertIn("accepted", response)
        self.assertIn("playing", response)
        self.assertIn("Could not start playback.", response)

    def test_single_step_music_actions_do_not_suppress_errors(self) -> None:
        for path in (
            MUSIC_STOP_AUTOMATION.parent / "jarvis_music_skip.yaml",
            MUSIC_VOLUME_AUTOMATION,
        ):
            with self.subTest(automation=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("continue_on_error", text)


@unittest.skipUnless(HAS_JINJA2, "jinja2 not installed in host python env")
class PlaybackConfirmationRenderTest(unittest.TestCase):
    """Render the real confirmation template against player fixtures.

    The template is extracted from the script source, rendered with the
    pinned Jinja2 engine, and checked against the false-positive scenarios:
    stale playback, natural queue advance, and same-track restart at
    position zero (which needs no position attribute at all).
    """

    @classmethod
    def setUpClass(cls) -> None:
        script = yaml.safe_load(PLAY_MEDIA_SCRIPT.read_text(encoding="utf-8"))
        result = next(
            step["variables"]["playback_result"]
            for step in script["sequence"]
            if isinstance(step, dict) and "playback_result" in step.get("variables", {})
        )
        cls.status_template = result["status"]
        cls.env = jinja2.Environment(undefined=jinja2.Undefined)

    def test_confirmation_loop_is_bounded_without_exclusive_repeat_modes(self):
        script = yaml.safe_load(PLAY_MEDIA_SCRIPT.read_text())
        branch = next(
            step
            for step in script["sequence"]
            if "if" in step
            and "then" in step
            and any("repeat" in item for item in step["then"])
        )
        repeat = branch["then"][0]["repeat"]
        self.assertEqual(
            set(repeat) & {"count", "until", "while", "for_each"}, {"until"}
        )
        template = self.env.from_string(repeat["until"][0]["value_template"])
        context = dict(
            is_state=lambda *_: False,
            state_attr=lambda *_: None,
            satellite_player="test",
            confirm_kind="none",
            confirm_needle="",
        )
        for index in range(1, 7):
            self.assertEqual(
                template.render(repeat={"index": index}, **context).strip(),
                str(index >= 6),
            )
        context.update(
            is_state=lambda *_: True,
            state_attr=lambda *_: "track12345",
            confirm_kind="id",
            confirm_needle="track12345",
        )
        self.assertEqual(
            template.render(repeat={"index": 1}, **context).strip(), "True"
        )

    def render_status(
        self,
        *,
        state: str,
        content_id: str | None,
        artist: str = "",
        title: str = "",
        kind: str,
        needle: str,
    ) -> str:
        attrs = {
            "media_content_id": content_id,
            "media_artist": artist,
            "media_title": title,
        }
        return (
            self.env.from_string(self.status_template)
            .render(
                is_state=lambda entity, want: state == want,
                state_attr=lambda entity, attr: attrs.get(attr),
                satellite_player="media_player.test",
                confirm_kind=kind,
                confirm_needle=needle,
            )
            .strip()
        )

    def test_stale_previous_track_stays_accepted(self) -> None:
        self.assertEqual(
            self.render_status(
                state="playing",
                content_id="spotify--conn://track/OLD123",
                kind="id",
                needle="NEW456",
            ),
            "accepted",
        )

    def test_naturally_advanced_unrelated_track_stays_accepted(self) -> None:
        self.assertEqual(
            self.render_status(
                state="playing",
                content_id="spotify--conn://track/OTHER999",
                kind="id",
                needle="NEW456",
            ),
            "accepted",
        )

    def test_same_track_restart_at_zero_is_playing(self) -> None:
        self.assertEqual(
            self.render_status(
                state="playing",
                content_id="spotify--conn://track/SAME123",
                kind="id",
                needle="SAME123",
            ),
            "playing",
        )

    def test_artist_name_match_is_playing(self) -> None:
        self.assertEqual(
            self.render_status(
                state="playing",
                content_id="spotify--conn://track/WHATEVER",
                artist="MF DOOM",
                title="MM..FOOD",
                kind="name",
                needle="mf doom",
            ),
            "playing",
        )

    def test_artist_name_mismatch_stays_accepted(self) -> None:
        self.assertEqual(
            self.render_status(
                state="playing",
                content_id="spotify--conn://track/WHATEVER",
                artist="The Weeknd",
                title="Out of Time",
                kind="name",
                needle="mf doom",
            ),
            "accepted",
        )

    def test_name_match_in_title_is_playing(self) -> None:
        self.assertEqual(
            self.render_status(
                state="playing",
                content_id="spotify--conn://track/WHATEVER",
                artist="Someone Else",
                title="MF DOOM Tribute",
                kind="name",
                needle="mf doom",
            ),
            "playing",
        )

    def test_missing_correlation_kind_stays_accepted(self) -> None:
        self.assertEqual(
            self.render_status(
                state="playing",
                content_id="spotify--conn://track/NEW456",
                kind="none",
                needle="NEW456",
            ),
            "accepted",
        )

    def test_short_needle_stays_accepted(self) -> None:
        self.assertEqual(
            self.render_status(
                state="playing",
                content_id="spotify--conn://track/abc",
                kind="id",
                needle="abc",
            ),
            "accepted",
        )

    def test_paused_player_with_matching_needle_stays_accepted(self) -> None:
        self.assertEqual(
            self.render_status(
                state="paused",
                content_id="spotify--conn://track/NEW456",
                kind="id",
                needle="NEW456",
            ),
            "accepted",
        )


class SatelliteMuteContractTest(unittest.TestCase):
    """The satellite mute switch is a manual shut-up control (B6).

    No automation may drive it as automatic echo suppression: engaging it
    stops TTS mid-utterance, and unmuting on idle would undo a deliberate
    manual mute. Satellite-local playback inhibition is the replacement and
    lives outside HA automations.
    """

    MUTE_SWITCH = "switch.homelab_05_satellite_mute"

    def test_no_automation_drives_mute_switch(self) -> None:
        automations = REPO_ROOT / "home-assistant" / "automations"
        offenders = [
            path.name
            for path in sorted(automations.glob("*.yaml"))
            if self.MUTE_SWITCH in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
