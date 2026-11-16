import ast
import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from jinja2 import StrictUndefined
from jinja2.nativetypes import NativeEnvironment

ROOT = Path(__file__).resolve().parents[1]


class BedroomWakeFadeTests(unittest.TestCase):
    def setUp(self) -> None:
        source = yaml.safe_load(
            (
                ROOT / "home-assistant/automations/bedroom_lights_morning_wake_up.yaml"
            ).read_text()
        )
        # Exercise the key sorting performed when deployment serializes resources.
        self.config = json.loads(json.dumps(source, sort_keys=True))
        self.wake = datetime(2026, 11, 11, 8, tzinfo=UTC)
        self.env = NativeEnvironment(undefined=StrictUndefined)
        self.env.globals.update(
            states=lambda _: "08:00:00",
            today_at=lambda _: self.wake,
            as_timestamp=lambda value, default=None: value.timestamp(),
        )

    def render(self, template: str, variables: dict):
        # HA strips whitespace before converting template results to native values.
        value = self.env.from_string(template).render(**variables)
        return ast.literal_eval(value.strip()) if isinstance(value, str) else value

    def variables_at(self, moment: datetime) -> dict:
        self.env.globals["now"] = lambda: moment
        variables = {}
        for action in self.config["actions"]:
            if "variables" not in action:
                break
            for name, template in action["variables"].items():
                variables[name] = self.render(template, variables)
        return variables

    def test_serialized_variables_cover_window_boundaries_and_restart(self) -> None:
        for minutes, expected in [
            (-21, False),
            (-20, True),
            (-10, True),
            (0, True),
            (1, False),
        ]:
            with self.subTest(minutes=minutes):
                variables = self.variables_at(self.wake + timedelta(minutes=minutes))
                self.assertEqual(variables["target_ts"], self.wake.timestamp())
                self.assertIs(variables["in_window"], expected)

    def test_unknown_wake_time_skips_fade(self) -> None:
        for state in ("unknown", "unavailable", ""):
            with self.subTest(state=state):
                self.env.globals["states"] = lambda _, value=state: value
                variables = self.variables_at(self.wake - timedelta(minutes=10))
                self.assertEqual(variables["target_ts"], 0)
                self.assertIs(variables["in_window"], False)

    def test_brightness_renders_at_start_midpoint_and_end(self) -> None:
        fade = self.config["actions"][-1]["choose"][-1]["sequence"]
        template = next(
            action["data"]["brightness_pct"]
            for action in fade
            if action["action"] == "light.turn_on" and "data" in action
        )
        for minutes, expected in [(-20, 1), (-19, 1), (-10, 15), (-1, 28), (0, 30)]:
            with self.subTest(minutes=minutes):
                variables = self.variables_at(self.wake + timedelta(minutes=minutes))
                self.assertEqual(self.render(template, variables), expected)


if __name__ == "__main__":
    unittest.main()
