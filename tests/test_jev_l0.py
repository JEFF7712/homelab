"""Tests for the L0 deterministic grammar fast path.

L0 parses unambiguous canonical commands without a model. The corpus
benchmark below is a safety instrument: L0 must never parse a case the
corpus expects to clarify (or fall back), and coverage must not regress.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
import unittest.mock
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "home-assistant" / "custom_components" / "jarvis_jev"
CORPUS_PATH = ROOT / "tests" / "jev_decision_corpus.yaml"
ADVERSARIAL_PATH = ROOT / "tests" / "fixtures" / "l1_adversarial.yaml"

MIN_CORPUS_COVERAGE = 59


def load_l0():
    package = types.ModuleType("jarvis_jev")
    package.__path__ = [str(PACKAGE_DIR)]
    sys.modules["jarvis_jev"] = package
    for name in ("const", "router", "l0"):
        spec = importlib.util.spec_from_file_location(
            f"jarvis_jev.{name}", PACKAGE_DIR / f"{name}.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"jarvis_jev.{name}"] = module
        spec.loader.exec_module(module)
    return sys.modules["jarvis_jev.l0"]


l0 = load_l0()


class CanonicalParseTest(unittest.TestCase):
    def test_turn_off(self) -> None:
        command = l0.parse_canonical("Turn off the kitchen lights.")
        assert command is not None
        self.assertEqual(
            (command.target, command.action, command.confidence),
            ("kitchen_lights", "turn_off", 1.0),
        )

    def test_polite_and_speaker_prefixes(self) -> None:
        for say in (
            "Please turn off the kitchen lights.",
            "speaker Rupan turn off the kitchen lights.",
        ):
            command = l0.parse_canonical(say)
            assert command is not None
            self.assertEqual(command.target, "kitchen_lights")

    def test_verb_orders(self) -> None:
        cases = {
            "Switch the good vibes sign on.": ("good_vibes_sign", "turn_on"),
            "Switch off the stairs light.": ("stairs_light", "turn_off"),
            "Turn the downstairs lights on.": ("downstairs_lights", "turn_on"),
            "Turn the lights off in the kitchen.": ("kitchen_lights", "turn_off"),
            "Toggle the stairs light.": ("stairs_light", "toggle"),
        }
        for say, (target, action) in cases.items():
            command = l0.parse_canonical(say)
            assert command is not None, say
            self.assertEqual((command.target, command.action), (target, action))

    def test_brightness_and_range(self) -> None:
        command = l0.parse_canonical("Set the kitchen lights to 40 percent.")
        assert command is not None
        self.assertEqual(
            (command.target, command.action, command.value),
            ("kitchen_lights", "set_brightness", 40.0),
        )
        self.assertEqual(command.color, "none_or_unknown")
        self.assertIsNone(l0.parse_canonical("Set the kitchen lights to 150 percent."))
        self.assertIsNone(l0.parse_canonical("Set the thermostat to 70 or 72 degrees."))

    def test_color(self) -> None:
        command = l0.parse_canonical("Set the kitchen lights to blue.")
        assert command is not None
        self.assertEqual(
            (command.target, command.action, command.color),
            ("kitchen_lights", "set_color", "blue"),
        )
        self.assertIsNone(l0.parse_canonical("Make the kitchen lights chartreuse."))

    def test_temperature_media_scene(self) -> None:
        command = l0.parse_canonical("Set the living room thermostat to 70 degrees.")
        assert command is not None
        self.assertEqual(
            (command.target, command.action, command.value),
            ("living_room_thermostat", "set_temperature", 70.0),
        )
        command = l0.parse_canonical("Stop the satellite speaker.")
        assert command is not None
        self.assertEqual(
            (command.target, command.action),
            ("satellite_media_player", "pause_media"),
        )
        command = l0.parse_canonical("Activate movie mode in the living room.")
        assert command is not None
        self.assertEqual(
            (command.target, command.action), ("movie_mode", "activate_scene")
        )

    def test_ambiguous_and_unsupported_are_none(self) -> None:
        for say in (
            "Turn that back on.",
            "Turn the light off.",
            "Turn off the upstairs lights.",
            "Turn on the garage lights.",
            "Turn off the kitchen lights and turn on the living room lights.",
            "Lock the front door.",
            "What is the weather tomorrow?",
            "Turn everything off.",
            "Is the kitchen light on?",
        ):
            self.assertIsNone(l0.parse_canonical(say), say)

    def test_canonical_play_parses_to_music(self) -> None:
        for say in (
            "Play some Kanye West.",
            "Put on Abbey Road.",
            "Listen to MF DOOM.",
            "I want to hear Miles Davis.",
            "speaker Rupan play some jazz.",
        ):
            command = l0.parse_canonical(say)
            assert command is not None, say
            self.assertEqual(
                (command.target, command.action),
                ("satellite_media_player", "play_music"),
            )

    def test_play_with_home_words_or_no_query_is_none(self) -> None:
        for say in (
            "Play.",
            "Put on the kitchen lights.",
            "Play the thermostat.",
            "Play some jazz and blues.",
        ):
            self.assertIsNone(l0.parse_canonical(say), say)

    def test_scene_still_wins_over_play(self) -> None:
        command = l0.parse_canonical("Put on movie mode.")
        assert command is not None
        self.assertEqual(
            (command.target, command.action),
            ("movie_mode", "activate_scene"),
        )

    def test_domain_action_mismatch_is_none(self) -> None:
        for say in (
            "Set the stairs light to 50 percent.",
            "Make the kitchen mushroom lamp blue.",
            "Pause the kitchen lights.",
            "Make movie mode blue.",
            "Set movie mode to 70 degrees.",
            "Activate the kitchen lights.",
            "Turn off movie mode.",
            "Dim movie mode.",
            "Set the thermostat.",
        ):
            self.assertIsNone(l0.parse_canonical(say), say)


class CorpusBenchmarkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with open(CORPUS_PATH, encoding="utf-8") as f:
            cls.cases = list(yaml.safe_load(f))

    def test_no_unsafe_parses(self) -> None:
        unsafe = [
            case["id"]
            for case in self.cases
            if l0.parse_canonical(case["say"]) is not None
            and case["expected_route"] != "execute"
        ]
        self.assertEqual(unsafe, [])

    def test_parsed_fields_match(self) -> None:
        mismatched = []
        for case in self.cases:
            command = l0.parse_canonical(case["say"])
            if command is None or case["expected_route"] != "execute":
                continue
            expected = case.get("expected", {})
            if not (
                command.target == expected.get("target")
                and command.action == expected.get("action")
                and command.color == expected.get("color", "none_or_unknown")
                and command.value == case.get("value")
            ):
                mismatched.append(case["id"])
        self.assertEqual(mismatched, [])

    def test_coverage_floor(self) -> None:
        covered = sum(
            1
            for case in self.cases
            if l0.parse_canonical(case["say"]) is not None
            and case["expected_route"] == "execute"
        )
        self.assertGreaterEqual(covered, MIN_CORPUS_COVERAGE)


class L1AdversarialSetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with open(CORPUS_PATH, encoding="utf-8") as f:
            cls.cases = {c["id"]: c for c in yaml.safe_load(f)}
        with open(ADVERSARIAL_PATH, encoding="utf-8") as f:
            cls.adversarial = yaml.safe_load(f)

    def test_ids_are_valid_and_disjoint(self) -> None:
        groups = [
            self.adversarial["stress_unsafe"],
            self.adversarial["model_errors_l0_covered"],
            self.adversarial["primary_safe"],
            self.adversarial["canaries"],
            self.adversarial["stress_synthetic"],
        ]
        self.assertEqual(len(self.adversarial["stress_unsafe"]), 12)
        self.assertEqual(len(self.adversarial["primary_safe"]), 63)
        self.assertEqual(len(self.adversarial["stress_synthetic"]), 201)
        for group in groups:
            for cid in group:
                self.assertIn(cid, self.cases, cid)
        for cid in (
            self.adversarial["primary_safe"]
            + self.adversarial["canaries"]
            + self.adversarial["model_errors_l0_covered"]
        ):
            self.assertEqual(self.cases[cid]["expected_route"], "execute", cid)

    def test_l0_misses_non_execute_stress_cases(self) -> None:
        pending = (
            self.adversarial["stress_unsafe"] + self.adversarial["stress_synthetic"]
        )
        for cid in pending:
            if self.cases[cid]["expected_route"] == "execute":
                continue
            self.assertIsNone(l0.parse_canonical(self.cases[cid]["say"]), cid)

    def test_synthetic_all_expect_clarify(self) -> None:
        for cid in self.adversarial["stress_synthetic"]:
            self.assertEqual(self.cases[cid]["expected_route"], "clarify", cid)

    def test_l0_parses_canaries_exactly(self) -> None:
        for cid in self.adversarial["canaries"]:
            case = self.cases[cid]
            command = l0.parse_canonical(case["say"])
            assert command is not None, cid
            expected = case.get("expected", {})
            self.assertEqual(command.target, expected.get("target"), cid)
            self.assertEqual(command.action, expected.get("action"), cid)
            self.assertEqual(
                command.color, expected.get("color", "none_or_unknown"), cid
            )
            self.assertEqual(command.value, case.get("value"), cid)


class RiskTierTest(unittest.TestCase):
    def test_all_emittable_actions_are_tiered(self) -> None:
        router_mod = sys.modules["jarvis_jev.router"]
        const_mod = sys.modules["jarvis_jev.const"]
        emittable = set()
        for target in router_mod.TARGETS.values():
            emittable |= set(target.actions)
        tiered = const_mod.STANDARD_ACTIONS | const_mod.RESTRICTED_ACTIONS
        self.assertEqual(emittable - tiered, set())
        self.assertEqual(
            const_mod.STANDARD_ACTIONS & const_mod.RESTRICTED_ACTIONS, set()
        )

    def test_restricted_action_clarifies(self) -> None:
        router_mod = sys.modules["jarvis_jev.router"]
        payload = {
            "model": router_mod.MODEL if hasattr(router_mod, "MODEL") else "jev-1.13.0",
            "answers": {
                "request_kind": {
                    "type": "choice",
                    "choice": "home_command",
                    "confidence": 0.99,
                },
                "target": {
                    "type": "choice",
                    "choice": "kitchen_lights",
                    "confidence": 0.99,
                },
                "action": {"type": "choice", "choice": "turn_off", "confidence": 0.99},
                "reference": {
                    "type": "choice",
                    "choice": "explicit_target",
                    "confidence": 0.99,
                },
                "color": {
                    "type": "choice",
                    "choice": "none_or_unknown",
                    "confidence": 0.99,
                },
            },
        }
        with unittest.mock.patch.object(
            router_mod, "RESTRICTED_ACTIONS", frozenset({"turn_off"})
        ):
            decision = router_mod.decide("Turn off the kitchen lights.", payload)
        self.assertEqual(decision.route, "clarify")
        self.assertIn("confirmation", decision.speech or "")

    def test_l0_refuses_restricted_action(self) -> None:
        l0_mod = sys.modules["jarvis_jev.l0"]
        with unittest.mock.patch.object(
            l0_mod, "RESTRICTED_ACTIONS", frozenset({"turn_off"})
        ):
            self.assertIsNone(l0_mod.parse_canonical("Turn off the kitchen lights."))


if __name__ == "__main__":
    unittest.main()
