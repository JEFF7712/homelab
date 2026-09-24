from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
AUTOMATION = ROOT / "home-assistant/automations/jarvis_voice_music_playback.yaml"
SCRIPT = ROOT / "home-assistant/scripts/jarvis_play_media.yaml"


class JarvisMusicRoutingTest(unittest.TestCase):
    def test_short_play_phrasing_and_speaker_prefix_are_supported(self) -> None:
        automation = yaml.safe_load(AUTOMATION.read_text(encoding="utf-8"))
        track = next(
            trigger
            for trigger in automation["triggers"]
            if trigger.get("id") == "track"
        )
        self.assertIn("play {query}", track["command"])
        self.assertIn("[speaker {speaker}] play {query}", track["command"])

    def test_unknown_speaker_defaults_to_spotify_and_sam_to_youtube_music(self) -> None:
        automation = yaml.safe_load(AUTOMATION.read_text(encoding="utf-8"))
        action = automation["actions"][0]
        self.assertEqual(action.get("action"), "script.jarvis_play_media")
        platform = action["data"]["platform"]
        self.assertIn("speaker | lower == 'sam'", platform)
        self.assertIn("else 'spotify'", platform)

    def test_youtube_track_search_stops_without_cross_provider_fallback(self) -> None:
        script = yaml.safe_load(SCRIPT.read_text(encoding="utf-8"))
        branches = next(
            step["choose"] for step in script["sequence"] if "choose" in step
        )
        track = next(
            branch
            for branch in branches
            if "media_content_type in ['music', 'track']"
            in json.dumps(branch.get("conditions", []))
        )
        sequence = track["sequence"]
        values = next(
            step["variables"]
            for step in sequence
            if "track_uri" in step.get("variables", {})
        )
        self.assertIn("default('', true)", values["track_uri"])
        missing_result = next(
            index
            for index, step in enumerate(sequence)
            if "No track result was found on" in json.dumps(step)
        )
        play = next(
            index
            for index, step in enumerate(sequence)
            if step.get("action") == "music_assistant.play_media"
        )
        self.assertLess(missing_result, play)


if __name__ == "__main__":
    unittest.main()
