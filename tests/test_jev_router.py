from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "home-assistant" / "custom_components" / "jarvis_jev"


def load_router():
    package = types.ModuleType("jarvis_jev")
    package.__path__ = [str(PACKAGE_DIR)]
    sys.modules["jarvis_jev"] = package
    for name in ("const", "router"):
        spec = importlib.util.spec_from_file_location(
            f"jarvis_jev.{name}", PACKAGE_DIR / f"{name}.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"jarvis_jev.{name}"] = module
        spec.loader.exec_module(module)
    return sys.modules["jarvis_jev.router"]


router = load_router()


def choice(value: str, confidence: float = 0.99) -> dict:
    return {"type": "choice", "choice": value, "confidence": confidence}


def response(
    *,
    kind: str = "home_command",
    target: str = "kitchen_lights",
    action: str = "turn_off",
    reference: str = "explicit_target",
    color: str = "none_or_unknown",
    confidence: float = 0.99,
) -> dict:
    return {
        "model": "jev-1.13.0",
        "answers": {
            "request_kind": choice(kind, confidence),
            "target": choice(target, confidence),
            "action": choice(action, confidence),
            "reference": choice(reference, confidence),
            "color": choice(color, confidence),
        },
    }


class JevRouterTest(unittest.TestCase):
    def test_build_request_strips_speaker_and_pins_model(self) -> None:
        payload = router.build_request("speaker Rupan turn off the kitchen lights")
        self.assertEqual(payload["state"], "turn off the kitchen lights")
        self.assertEqual(payload["model"], "jev-1.13.0")
        self.assertLessEqual(len(payload["questions"]["target"]["criteria"]), 255)

    def test_high_confidence_allowlisted_command_executes(self) -> None:
        decision = router.decide("turn off the kitchen lights", response())
        self.assertEqual(decision.route, "execute")
        self.assertEqual(decision.command.target, "kitchen_lights")
        self.assertEqual(decision.command.action, "turn_off")

    def test_general_conversation_falls_back(self) -> None:
        decision = router.decide(
            "how many ounces are in a cup",
            response(kind="general_or_conversation"),
        )
        self.assertEqual(decision.route, "fallback")

    def test_low_confidence_home_command_never_falls_back(self) -> None:
        decision = router.decide("maybe turn something off", response(confidence=0.60))
        self.assertEqual(decision.route, "clarify")

    def test_compound_command_never_partially_executes(self) -> None:
        decision = router.decide(
            "turn off the kitchen and set the thermostat", response(kind="compound")
        )
        self.assertEqual(decision.route, "clarify")
        self.assertIsNone(decision.command)

    def test_ambiguous_reference_does_not_execute(self) -> None:
        decision = router.decide(
            "turn them off", response(reference="missing_or_ambiguous")
        )
        self.assertEqual(decision.route, "clarify")

    def test_unknown_and_incompatible_targets_do_not_execute(self) -> None:
        unknown = router.decide(
            "turn off the garage disco ball", response(target="none_or_unknown")
        )
        incompatible = router.decide(
            "make the thermostat blue",
            response(target="living_room_thermostat", action="set_color", color="blue"),
        )
        self.assertEqual(unknown.route, "clarify")
        self.assertEqual(incompatible.route, "clarify")

    def test_exact_brightness_requires_one_in_range_number(self) -> None:
        valid = router.decide(
            "set the kitchen lights to 30%",
            response(action="set_brightness"),
        )
        missing = router.decide(
            "set the kitchen brightness", response(action="set_brightness")
        )
        multiple = router.decide(
            "change it from 20 to 30 percent", response(action="set_brightness")
        )
        invalid = router.decide(
            "set the kitchen lights to 150 percent",
            response(action="set_brightness"),
        )
        self.assertEqual(valid.route, "execute")
        self.assertEqual(valid.command.value, 30)
        self.assertEqual(missing.route, "clarify")
        self.assertEqual(multiple.route, "clarify")
        self.assertEqual(invalid.route, "clarify")

    def test_color_confidence_is_part_of_weakest_field_gate(self) -> None:
        payload = response(action="set_color", color="blue")
        payload["answers"]["color"] = choice("blue", 0.80)
        decision = router.decide("make the kitchen lights blue", payload)
        self.assertEqual(decision.route, "clarify")

    def test_model_cannot_invent_command_fields(self) -> None:
        cases = (
            (
                "Turn on the kitchenette lights.",
                response(target="kitchen_lights", action="turn_on"),
            ),
            (
                "Activate the kitchen lights.",
                response(target="kitchen_lights", action="turn_on"),
            ),
            (
                "Make the kitchen lights chartreuse.",
                response(action="set_color", color="green"),
            ),
            (
                "Put the kitchen lights back.",
                response(target="kitchen_lights", action="turn_on"),
            ),
        )
        for say, payload in cases:
            with self.subTest(say=say):
                self.assertEqual(router.decide(say, payload).route, "clarify")

    def test_play_music_executes_without_explicit_device(self) -> None:
        artist = router.decide(
            "play some kanye west",
            response(
                target="satellite_media_player",
                action="play_music",
                reference="missing_or_ambiguous",
            ),
        )
        self.assertEqual(artist.route, "execute")
        self.assertEqual(artist.command.target, "satellite_media_player")
        self.assertEqual(artist.command.action, "play_music")
        track = router.decide(
            "play kanye west on the speaker",
            response(target="satellite_media_player", action="play_music"),
        )
        self.assertEqual(track.route, "execute")

    def test_play_music_strips_device_suffix_and_detects_type(self) -> None:
        self.assertEqual(
            router.extract_music_request("play some kanye west on the speaker"),
            ("artist", "kanye west", ""),
        )
        self.assertEqual(
            router.extract_music_request("speaker Sam play Abbey Road"),
            ("music", "Abbey Road", "Sam"),
        )
        self.assertEqual(
            router.extract_music_request("put some To Pimp a Butterfly on"),
            ("artist", "To Pimp a Butterfly", ""),
        )
        self.assertEqual(
            router.extract_music_request("put on Stargazing by Travis Scott"),
            ("music", "Stargazing by Travis Scott", ""),
        )
        self.assertEqual(
            router.extract_music_request("play some Travis Scott"),
            ("artist", "Travis Scott", ""),
        )
        self.assertIsNone(router.extract_music_request("play"))
        self.assertIsNone(router.extract_music_request("play and sing"))
        self.assertIsNone(router.extract_music_request("turn off the speaker"))

    def test_play_music_wrong_target_or_empty_query_clarifies(self) -> None:
        wrong_target = router.decide(
            "play some jazz",
            response(target="kitchen_lights", action="play_music"),
        )
        self.assertEqual(wrong_target.route, "clarify")
        empty = router.decide(
            "play",
            response(target="satellite_media_player", action="play_music"),
        )
        self.assertEqual(empty.route, "clarify")
        self.assertEqual(empty.speech, "Please say what to play.")

    def test_play_music_low_confidence_clarifies(self) -> None:
        decision = router.decide(
            "maybe play something",
            response(
                target="satellite_media_player",
                action="play_music",
                confidence=0.80,
            ),
        )
        self.assertEqual(decision.route, "clarify")
        self.assertIsNone(decision.command)

    def test_build_request_offers_play_music(self) -> None:
        payload = router.build_request("play some kanye west")
        self.assertIn("play_music", payload["questions"]["action"]["criteria"])
        self.assertIn(
            "play_music",
            router.TARGETS["satellite_media_player"].actions,
        )

    def test_malformed_or_wrong_model_response_fails_closed(self) -> None:
        self.assertEqual(router.decide("turn it off", {}).route, "reject")
        payload = response()
        payload["model"] = "jev-latest"
        self.assertEqual(router.decide("turn it off", payload).route, "reject")


if __name__ == "__main__":
    unittest.main()
