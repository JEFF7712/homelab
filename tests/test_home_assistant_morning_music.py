import ast
import json
import unittest
from pathlib import Path

import yaml
from jinja2 import StrictUndefined
from jinja2.nativetypes import NativeEnvironment

ROOT = Path(__file__).resolve().parents[1]
PLAYER = "media_player.bedroom_speaker"
QUEUE = "Music Assistant Queue"


class BedroomMorningMusicTests(unittest.TestCase):
    def setUp(self) -> None:
        source = yaml.safe_load(
            (ROOT / "home-assistant/automations/bedroom_morning_music.yaml").read_text()
        )
        # Exercise the key sorting performed when deployment serializes resources.
        self.config = json.loads(json.dumps(source, sort_keys=True))
        self.variables = self.config["actions"][0]["variables"]
        branches = self.config["actions"][1]["choose"]
        self.start = next(
            b for b in branches if b["conditions"][0].get("id") == "start"
        )
        self.stop = next(b for b in branches if b["conditions"][0].get("id") == "stop")

    def render_stop_condition(self, template: str, state: str, attrs: dict) -> bool:
        env = NativeEnvironment(undefined=StrictUndefined)
        env.globals.update(
            is_state=lambda entity, value: entity == PLAYER and state == value,
            state_attr=lambda entity, name: (
                attrs.get(name) if entity == PLAYER else None
            ),
        )
        value = env.from_string(template).render(
            player=PLAYER,
            queue_source=self.variables["queue_source"],
            morning_volume=self.variables["morning_volume"],
        )
        return ast.literal_eval(value.strip()) if isinstance(value, str) else value

    def test_starts_at_wake_time_only_when_home(self) -> None:
        start_trigger = next(t for t in self.config["triggers"] if t["id"] == "start")
        self.assertEqual(start_trigger["at"], "input_datetime.bedroom_wake_time")
        guards = {
            c["entity_id"]: c["state"]
            for c in self.start["conditions"]
            if c["condition"] == "state"
        }
        self.assertEqual(
            guards,
            {
                "person.rupan_sunderapandyan": "home",
                "input_boolean.vacation_mode": "off",
            },
        )

    def test_shuffle_is_settled_before_the_playlist_replaces_the_queue(self) -> None:
        # Shuffle and clear are no-ops on a Spotify Connect source, and clearing
        # drops a dynamic source that would reset shuffle on replace; only this
        # order makes the first track random.
        order = [step.get("action") for step in self.start["sequence"]]
        self.assertLess(
            order.index("media_player.select_source"),
            order.index("media_player.clear_playlist"),
        )
        self.assertLess(
            order.index("media_player.clear_playlist"),
            order.index("media_player.shuffle_set"),
        )
        self.assertLess(
            order.index("media_player.shuffle_set"),
            order.index("music_assistant.play_media"),
        )
        play = self.start["sequence"][order.index("music_assistant.play_media")]
        self.assertEqual(
            play["data"]["media_id"], "spotify:playlist:4OqFps0bUsOrPUNVGi6Mqx"
        )
        self.assertEqual(play["data"]["enqueue"], "replace")
        self.assertEqual(self.variables["queue_source"], QUEUE)

    def test_stops_after_twenty_minutes_and_restores_volume(self) -> None:
        delay = next(step for step in self.start["sequence"] if "delay" in step)
        self.assertEqual(delay["delay"], {"minutes": 20})
        stop_step = self.start["sequence"][-1]
        actions = [step["action"] for step in stop_step["then"]]
        self.assertEqual(
            actions, ["media_player.media_stop", "media_player.volume_set"]
        )
        self.assertEqual(
            stop_step["then"][1]["data"]["volume_level"], "{{ prior_volume }}"
        )

    def test_stop_only_targets_the_morning_session(self) -> None:
        morning = float(self.variables["morning_volume"])
        cases = [
            ("playing", {"source": QUEUE, "volume_level": morning}, True),
            ("paused", {"source": QUEUE, "volume_level": morning}, False),
            (
                "playing",
                {
                    "source": "Spotify Connect (Bedroom Speaker)",
                    "volume_level": morning,
                },
                False,
            ),
            ("playing", {"source": QUEUE, "volume_level": 0.45}, False),
        ]
        templates = [
            self.start["sequence"][-1]["if"][0]["value_template"],
            self.stop["conditions"][1]["value_template"],
        ]
        for template in templates:
            for state, attrs, expected in cases:
                with self.subTest(state=state, attrs=attrs):
                    self.assertIs(
                        self.render_stop_condition(template, state, attrs), expected
                    )


if __name__ == "__main__":
    unittest.main()
