"""Analog tilt input from the phone controller to the Breakout paddle."""

from __future__ import annotations

import time
import unittest

import modes  # noqa: F401  # import order: modes before web_controller avoids the hub import cycle
from modes.base import HAState, PlaybackState, RenderContext
from modes.multiplayer import InteractiveBreakout, MultiplayerArcadeRenderer
from web_controller import MultiplayerHub


class HubAnalogTests(unittest.TestCase):
    def test_clamps_and_rejects_invalid_values(self) -> None:
        hub = MultiplayerHub()
        cases: list[tuple[object, float | None]] = [
            (0.25, 0.25),
            (-3, -1.0),
            (9.5, 1.0),
            (float("nan"), None),
            (float("inf"), None),
            (True, None),
            ("0.5", None),
            (None, None),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                hub.handle_analog(1, 0.5)
                hub.handle_analog(1, raw)
                self.assertEqual(hub.p1_analog, expected)
        hub.handle_analog(2, -0.5)
        self.assertEqual(hub.p2_analog, -0.5)

    def test_clear_inputs_drops_analog(self) -> None:
        hub = MultiplayerHub()
        hub.handle_analog(1, 0.4)
        hub.handle_analog(2, -0.4)
        hub.clear_inputs()
        self.assertIsNone(hub.p1_analog)
        self.assertIsNone(hub.p2_analog)


class BreakoutAnalogTests(unittest.TestCase):
    def _settle(self, game: InteractiveBreakout, axis: float | None) -> None:
        game.p1_analog = axis
        for _ in range(40):
            game.ball_y = 40.0
            game.update(0.05, set(), set())

    def test_axis_maps_to_absolute_paddle_position(self) -> None:
        game = InteractiveBreakout()
        lo = game.paddle_w / 2.0 + 1
        hi = 63 - game.paddle_w / 2.0
        for axis, expected in ((-1.0, lo), (0.0, (lo + hi) / 2.0), (1.0, hi)):
            with self.subTest(axis=axis):
                self._settle(game, axis)
                self.assertAlmostEqual(game.paddle_x, expected, places=3)

    def test_paddle_speed_is_bounded(self) -> None:
        game = InteractiveBreakout()
        game.p1_analog = 1.0
        start = game.paddle_x
        game.update(0.05, set(), set())
        self.assertAlmostEqual(game.paddle_x - start, game.analog_speed * 0.05)

    def test_digital_input_still_works_without_analog(self) -> None:
        game = InteractiveBreakout()
        start = game.paddle_x
        game.update(0.05, {"right"}, set())
        self.assertGreater(game.paddle_x, start)

    def test_analog_overrides_held_buttons(self) -> None:
        game = InteractiveBreakout()
        self._settle(game, 0.0)
        centre = game.paddle_x
        game.update(0.05, {"right"}, set())
        self.assertAlmostEqual(game.paddle_x, centre)


class RendererAnalogWiringTests(unittest.TestCase):
    def test_hub_axis_reaches_active_game(self) -> None:
        hub = MultiplayerHub()
        renderer = MultiplayerArcadeRenderer(hub)
        hub.trigger_game_start("breakout", solo=True)
        ctx = RenderContext(
            tick=time.monotonic(),
            dt=0.05,
            ha_state=HAState(connected=True, power=True, mode="2-Player Arcade"),
            spotify_state=PlaybackState(),
            spotify_art=None,
        )
        hub.countdown_remaining = 0.001
        renderer.render(ctx)
        self.assertEqual(hub.phase, "playing")
        hub.handle_analog(1, 1.0)
        game = renderer.active_game
        assert isinstance(game, InteractiveBreakout)
        start = game.paddle_x
        renderer.render(ctx)
        self.assertEqual(game.p1_analog, 1.0)
        self.assertGreater(game.paddle_x, start)


if __name__ == "__main__":
    unittest.main()
