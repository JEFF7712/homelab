from __future__ import annotations

import json
import re
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

    def test_trailing_device_reference_is_stripped_from_query(self) -> None:
        automation = yaml.safe_load(AUTOMATION.read_text(encoding="utf-8"))
        action = automation["actions"][0]
        media_content_id = action["data"]["media_content_id"]
        self.assertIn("on\\s+(the\\s+)?(speaker|soundbar)", media_content_id)

    def test_song_by_artist_phrasing_routes_to_track_search(self) -> None:
        automation = yaml.safe_load(AUTOMATION.read_text(encoding="utf-8"))
        action = automation["actions"][0]
        media_content_type = action["data"]["media_content_type"]
        self.assertIn("trigger.id == 'artist'", media_content_type)
        self.assertIn("' by ' in trigger.slots.query.lower()", media_content_type)

    def test_unknown_speaker_defaults_to_spotify_and_sam_to_youtube_music(self) -> None:
        automation = yaml.safe_load(AUTOMATION.read_text(encoding="utf-8"))
        action = automation["actions"][0]
        self.assertEqual(action.get("action"), "script.jarvis_play_media")
        platform = action["data"]["platform"]
        self.assertIn("speaker | lower == 'sam'", platform)
        self.assertIn("else 'spotify'", platform)

    def test_failure_hands_full_utterance_to_jev(self) -> None:
        automation = yaml.safe_load(AUTOMATION.read_text(encoding="utf-8"))
        branch = next(step for step in automation["actions"] if "if" in step)
        fallback = next(
            step
            for step in branch["then"]
            if step.get("action") == "conversation.process"
        )
        self.assertEqual(fallback["data"]["agent_id"], "01M3123N8QF2RJ5Z95X7M7K87K")
        self.assertIn("trigger.sentence", fallback["data"]["text"])
        reply = next(
            step
            for step in automation["actions"]
            if "set_conversation_response" in step
        )
        speech = reply["set_conversation_response"]
        self.assertIn("playback.status", speech)
        self.assertIn("jev_fallback", speech)
        self.assertIn("Could not start playback.", speech)

    def test_provider_prefixes_match_instance_qualified_uris(self) -> None:
        # MA returns spotify--<instance>://... URIs, so a '^spotify:'
        # filter never matches and silently disables platform
        # preference and radio continuation.
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("'^spotify:'", text)
        self.assertNotIn("'^ytmusic:'", text)

    def test_song_by_artist_search_prefers_title_and_artist_match(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("by_song", text)
        self.assertIn("search_name", text)
        self.assertIn("ns.pick", text)

    def test_numeric_ids_stay_strings_at_length_checks(self) -> None:
        # HA renders variables with native types, so an all-digit needle
        # such as library://track/6206 becomes int 6206 and `| length`
        # explodes. Every length/membership use must stringify first.
        text = SCRIPT.read_text(encoding="utf-8")
        for match in re.finditer(
            r"(confirm_needle|artist_uri|track_uri|yt_uri) \| length", text
        ):
            start = match.start()
            self.assertTrue(
                text[:start].rstrip().endswith("| string"),
                f"unguarded length check: {match.group(0)}",
            )
        for match in re.finditer(r"confirm_needle(\s*\n\s*)in \(", text):
            start = match.start()
            self.assertTrue(
                text[:start].rstrip().endswith("| string"),
                "unguarded needle membership check",
            )

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
