from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from scripts.home_assistant.canonical import DuplicateKeySafeLoader

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE = REPO_ROOT / "home-assistant/core/configuration.yaml"
SHARED_LIGHTS_OFF = REPO_ROOT / "home-assistant/scripts/shared_lights_off.yaml"
ADAPTIVE_SWITCH = "switch.adaptive_lighting_all_dimmable_lights"


def intent_actions(name: str) -> list[dict]:
    data = yaml.load(CORE.read_text(encoding="utf-8"), Loader=DuplicateKeySafeLoader)
    return data["intent_script"][name]["action"]


def step_service(step: dict) -> str:
    # intent_script steps use `service:`; scripts use the newer `action:`.
    return step.get("service") or step.get("action") or ""


def release_lights(actions: list[dict]) -> set[str]:
    for step in actions:
        if step_service(step) != "adaptive_lighting.set_manual_control":
            continue
        data = step["data"]
        if data["entity_id"] == ADAPTIVE_SWITCH and data["manual_control"] is False:
            return set(data["lights"])
    return set()


def services(actions: list[dict]) -> list[str]:
    return [step_service(step) for step in actions]


class JarvisBareLightsReleaseAdaptiveLightingTests(unittest.TestCase):
    """A stale manual-control flag left shared bulbs green across voice on/off."""

    def setUp(self) -> None:
        script = yaml.safe_load(SHARED_LIGHTS_OFF.read_text(encoding="utf-8"))
        self.expected = release_lights(script["sequence"])
        self.assertTrue(self.expected)

    def test_lights_on_releases_before_turn_on(self) -> None:
        actions = intent_actions("JarvisDefaultLightsOn")
        self.assertEqual(release_lights(actions), self.expected)
        order = services(actions)
        self.assertLess(
            order.index("adaptive_lighting.set_manual_control"),
            order.index("light.turn_on"),
        )

    def test_lights_off_releases_after_turn_off(self) -> None:
        actions = intent_actions("JarvisDefaultLightsOff")
        self.assertEqual(release_lights(actions), self.expected)
        order = services(actions)
        self.assertLess(
            order.index("light.turn_off"),
            order.index("adaptive_lighting.set_manual_control"),
        )


if __name__ == "__main__":
    unittest.main()
