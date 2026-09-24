"""Cross-surface target/action sync checks (A3).

HA local routing spreads target vocabulary across the Jev router, the L0
grammar, custom sentences, and intent scripts. These tests pin the shared
vocabulary so a rename in one surface fails loudly instead of silently
weakening local routing. They deliberately do not merge the surfaces: HA
local intents keep their own grammars and Jev keeps its closed vocabulary.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "home-assistant" / "custom_components" / "jarvis_jev"
SENTENCES_DIR = ROOT / "home-assistant" / "custom_sentences" / "en"
CORE_FILE = ROOT / "home-assistant" / "core" / "configuration.yaml"
PLAYBACK_AUTOMATION = (
    ROOT / "home-assistant" / "automations" / "jarvis_voice_music_playback.yaml"
)
PLAY_MEDIA_SCRIPT = ROOT / "home-assistant" / "scripts" / "jarvis_play_media.yaml"


def _load_modules():
    package = types.ModuleType("jarvis_jev_sync")
    package.__path__ = [str(PACKAGE_DIR)]
    sys.modules["jarvis_jev_sync"] = package
    modules = {}
    for name in ("const", "router", "l0"):
        spec = importlib.util.spec_from_file_location(
            f"jarvis_jev_sync.{name}", PACKAGE_DIR / f"{name}.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"jarvis_jev_sync.{name}"] = module
        spec.loader.exec_module(module)
        modules[name] = module
    return modules


MODULES = _load_modules()
router = MODULES["router"]
l0 = MODULES["l0"]


def _sentence_yaml(name: str) -> dict:
    return yaml.safe_load((SENTENCES_DIR / name).read_text(encoding="utf-8"))


def _ha_loader() -> type[yaml.SafeLoader]:
    loader = type("HALoader", (yaml.SafeLoader,), {})
    loader.add_multi_constructor("!", lambda ldr, suffix, node: None)
    return loader


class TargetSyncTest(unittest.TestCase):
    def test_router_light_entities_have_sentence_coverage(self) -> None:
        colors = _sentence_yaml("jarvis_colors.yaml")
        outs = {value["out"] for value in colors["lists"]["jarvis_light"]["values"]}
        self.assertTrue(outs)
        missing = {
            target.entity_id
            for target in router.TARGETS.values()
            if target.entity_id.startswith("light.") and target.entity_id not in outs
        }
        self.assertEqual(missing, set())

    def test_alias_targets_are_router_entities(self) -> None:
        entities = set(router.TARGETS[target].entity_id for target in router.TARGETS)
        alias = _sentence_yaml("jarvis_alias.yaml")
        outs = {value["out"] for value in alias["lists"]["jarvis_alias"]["values"]}
        self.assertTrue(outs)
        self.assertEqual(outs - entities, set())

    def test_l0_resolutions_are_router_targets(self) -> None:
        for _pattern, key in l0._TARGET_KEY:
            if key == "area":
                continue
            self.assertIn(key, router.TARGETS, key)
        for area, target in l0._AREA_KEY.items():
            self.assertIn(target, router.TARGETS, area)

    def test_static_light_targets_in_intent_scripts_are_router_entities(self) -> None:
        core = yaml.load(CORE_FILE.read_text(encoding="utf-8"), Loader=_ha_loader())
        entities = set(router.TARGETS[target].entity_id for target in router.TARGETS)

        def walk(node: object, found: list[str]) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "entity_id" and isinstance(value, str):
                        found.append(value)
                    else:
                        walk(value, found)
            elif isinstance(node, list):
                for item in node:
                    walk(item, found)

        checked = 0
        for intent, body in core["intent_script"].items():
            found: list[str] = []
            walk(body.get("action", []), found)
            for target in found:
                if target.startswith("light.") and "{{" not in target:
                    self.assertIn(target, entities, intent)
                    checked += 1
        self.assertGreater(checked, 0)

    def test_speaker_vocabulary_matches_music_routing(self) -> None:
        speaker = _sentence_yaml("jarvis_speaker.yaml")
        names = set(speaker["lists"]["speaker"]["values"])
        self.assertEqual(names, {"Rupan", "Sam"})
        automation = PLAYBACK_AUTOMATION.read_text(encoding="utf-8")
        script = PLAY_MEDIA_SCRIPT.read_text(encoding="utf-8")
        for name in names:
            self.assertIn(name, automation + script)


if __name__ == "__main__":
    unittest.main()
