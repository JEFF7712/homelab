"""Rendered-template regressions for the Jarvis music volume automation.

Templates are rendered with the real Jinja2 engine from the pinned nix
devShell (matching Home Assistant's stock filter semantics) plus the two
HA-provided pieces the automation relies on: the ``match`` test and
``parse_result`` variable chaining. ``state_attr`` and ``trigger`` are
focused fixtures standing in for the HA runtime.

Behavioral contract under test (B4):
- explicit numeric levels are always percentages: ``1`` means 1% (0.01);
- results clamp to [0.0, 1.0] via an iterable sort/median;
- a zero current volume is preserved (relative steps start from 0.0);
- unparseable levels render ``invalid`` so the automation rejects them
  instead of silently substituting 50%.
"""

from __future__ import annotations

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
AUTOMATION_FILE = (
    REPO_ROOT / "home-assistant" / "automations" / "jarvis_music_volume.yaml"
)
SATELLITE = "media_player.homelab_05_satellite_media_player_2"


def make_env() -> jinja2.Environment:
    """Real Jinja2 with HA's ``match`` test registered."""
    env = jinja2.Environment(undefined=jinja2.Undefined)
    env.tests["match"] = lambda value, pattern: (
        isinstance(value, str) and re.match(pattern, value) is not None
    )
    return env


def parse_result(text: str) -> object:
    """Emulate HA's template parse_result for chained variables: rendered
    numbers and booleans come back as native values for later expressions."""
    stripped = text.strip()
    lowered = stripped.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("none", "null"):
        return None
    try:
        return int(stripped)
    except ValueError:
        pass
    try:
        return float(stripped)
    except ValueError:
        pass
    return text


def automation() -> dict:
    return yaml.safe_load(AUTOMATION_FILE.read_text(encoding="utf-8"))


def volume_default_steps() -> list:
    branches = automation()["actions"][0]
    assert "default" in branches, "volume automation must have a default branch"
    return branches["default"]


def volume_variables() -> dict:
    for step in volume_default_steps():
        if isinstance(step, dict) and "variables" in step:
            return step["variables"]
    raise AssertionError("volume automation must compute variables")


def render_target(
    trigger_id: str, slots: dict | None = None, current: float | None = 0.5
) -> str:
    env = make_env()
    states = {SATELLITE: {"volume_level": current}}
    context: dict = {
        "state_attr": lambda entity_id, attr: states.get(entity_id, {}).get(attr),
        "trigger": {"id": trigger_id, "slots": dict(slots or {})},
    }
    rendered = ""
    for name, template in volume_variables().items():
        rendered = env.from_string(str(template)).render(context)
        context[name] = parse_result(rendered)
    return rendered.strip()


@unittest.skipUnless(HAS_JINJA2, "jinja2 not installed in host python env")
class VolumeTemplateTest(unittest.TestCase):
    def test_zero_volume_is_preserved_on_relative_steps(self) -> None:
        self.assertEqual(float(render_target("volume_up", current=0.0)), 0.1)
        self.assertEqual(float(render_target("volume_down", current=0.0)), 0.0)
        self.assertEqual(float(render_target("volume_down_lot", current=0.0)), 0.0)

    def test_one_percent_maps_to_one_percent(self) -> None:
        self.assertEqual(float(render_target("volume_level", {"level": "1"})), 0.01)
        self.assertEqual(float(render_target("volume_level", {"level": "1%"})), 0.01)

    def test_explicit_percentages(self) -> None:
        self.assertEqual(float(render_target("volume_level", {"level": "0"})), 0.0)
        self.assertEqual(float(render_target("volume_level", {"level": "50"})), 0.5)
        self.assertEqual(float(render_target("volume_level", {"level": "100"})), 1.0)
        self.assertEqual(float(render_target("volume_level", {"level": "12.5"})), 0.12)

    def test_out_of_range_levels_clamp(self) -> None:
        self.assertEqual(float(render_target("volume_level", {"level": "150"})), 1.0)
        self.assertEqual(float(render_target("volume_level", {"level": "-10"})), 0.0)
        self.assertEqual(float(render_target("volume_up_lot", current=0.9)), 1.0)

    def test_invalid_level_is_rejected_not_defaulted(self) -> None:
        for slots in ({"level": "loud"}, {"level": ""}, {}):
            with self.subTest(slots=slots):
                self.assertEqual(render_target("volume_level", slots), "invalid")

    def test_unknown_current_volume_falls_back_without_clobbering_zero(self) -> None:
        self.assertEqual(float(render_target("volume_up", current=None)), 0.6)

    def test_min_max_branches(self) -> None:
        self.assertEqual(float(render_target("volume_max")), 1.0)
        self.assertEqual(float(render_target("volume_min")), 0.0)

    def test_invalid_gate_stops_before_service_call(self) -> None:
        steps = volume_default_steps()
        gate = next(
            step for step in steps if isinstance(step, dict) and "choose" in step
        )
        gate_text = str(gate)
        self.assertIn("invalid", gate_text)
        self.assertIn("stop", gate_text)
        service_calls = [
            step for step in steps if isinstance(step, dict) and "action" in step
        ]
        self.assertTrue(service_calls)
        self.assertNotIn("invalid", render_target("volume_level", {"level": "50"}))

    def test_no_success_response_after_suppressed_service_error(self) -> None:
        text = AUTOMATION_FILE.read_text(encoding="utf-8")
        self.assertNotIn("continue_on_error", text)


@unittest.skipUnless(HAS_JINJA2, "jinja2 not installed in host python env")
class TemplateFidelityTest(unittest.TestCase):
    """The previous template shapes fail on the real engine (B4 evidence)."""

    def test_scalar_max_raises_like_live_ha(self) -> None:
        env = make_env()
        with self.assertRaises(TypeError):
            env.from_string("{{ (0.6 | max(0.0)) }}").render()

    def test_truthy_default_replaces_zero(self) -> None:
        env = make_env()
        self.assertEqual(
            env.from_string("{{ value | default(0.5, true) | float(0.5) }}").render(
                value=0.0
            ),
            "0.5",
        )
        self.assertEqual(
            env.from_string("{{ value | float(0.5) }}").render(value=0.0), "0.0"
        )


if __name__ == "__main__":
    unittest.main()
