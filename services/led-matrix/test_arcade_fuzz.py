"""Seeded headless fuzzing for every multiplayer and attract-mode arcade game."""

from __future__ import annotations

import inspect
import math
import random
import unittest
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from PIL import Image, ImageDraw

from modes.arcade.arcade_renderer import RetroArcadeRenderer
from modes.multiplayer import GAME_REGISTRY

SEEDS = range(6)
FRAMES = 1500
ACTIONS = ["up", "down", "left", "right", "action_a", "action_b"]
DT_CHOICES = [0.0, 0.001, 0.016, 0.016, 0.033, 0.05, 0.15, 0.5]


@contextmanager
def seeded(seed: int) -> Iterator[random.Random]:
    """Seed the global RNG the games use and restore it afterwards."""
    state = random.getstate()
    random.seed(seed)
    try:
        yield random.Random(seed ^ 0x5EED)
    finally:
        random.setstate(state)


def non_finite_attrs(obj: object) -> list[str]:
    bad: list[str] = []
    for name, value in vars(obj).items():
        if isinstance(value, float) and not math.isfinite(value):
            bad.append(f"{name}={value}")
    return bad


def check_pong(t: unittest.TestCase, g: Any) -> None:
    for y in (g.p1_y, g.p2_y):
        t.assertGreaterEqual(y, 0)
        t.assertLessEqual(y, 64 - g.paddle_h)


def check_snake(t: unittest.TestCase, g: Any) -> None:
    for x, y in g.snake:
        t.assertTrue(0 <= x < g.grid_size and 0 <= y < g.grid_size, (x, y))


def check_breakout(t: unittest.TestCase, g: Any) -> None:
    half_w = g.paddle_w / 2.0
    t.assertGreaterEqual(g.paddle_x, half_w)
    t.assertLessEqual(g.paddle_x, 63 - half_w + 0.1)


def check_tetris(t: unittest.TestCase, g: Any) -> None:
    t.assertEqual(len(g.board), 17)
    t.assertTrue(all(len(row) == 10 for row in g.board))


def check_tron(t: unittest.TestCase, g: Any) -> None:
    for x, y in (g.p1_pos, g.p2_pos):
        t.assertTrue(0 <= x < 64 and 0 <= y < 64, (x, y))


def check_air_hockey(t: unittest.TestCase, g: Any) -> None:
    t.assertGreaterEqual(g.p1_x, g.RINK_L + g.MALLET_R - 0.1)
    t.assertLessEqual(g.p1_x, 29.0 - g.MALLET_R + 0.1)
    t.assertGreaterEqual(g.p2_x, 34.0 + g.MALLET_R - 0.1)
    t.assertLessEqual(g.p2_x, g.RINK_R - g.MALLET_R + 0.1)


def check_asteroids(t: unittest.TestCase, g: Any) -> None:
    t.assertTrue(0.0 <= g.ship_x < 64.0 and 0.0 <= g.ship_y < 64.0)


def check_flappy(t: unittest.TestCase, g: Any) -> None:
    t.assertEqual(g.bird_x, 16.0)
    t.assertGreaterEqual(g.bird_y, 0.0)
    t.assertLessEqual(g.bird_y, g.GROUND_Y)


def check_2048(t: unittest.TestCase, g: Any) -> None:
    t.assertEqual(len(g.grid), 4)
    t.assertTrue(all(len(row) == 4 for row in g.grid))


INVARIANTS: dict[str, Callable[[unittest.TestCase, Any], None]] = {
    "pong": check_pong,
    "snake": check_snake,
    "breakout": check_breakout,
    "tetris": check_tetris,
    "tron": check_tron,
    "air_hockey": check_air_hockey,
    "asteroids": check_asteroids,
    "flappy": check_flappy,
    "2048": check_2048,
}


def random_inputs(rng: random.Random) -> set[str]:
    return set(rng.sample(ACTIONS, k=rng.randint(0, 2)))


class MultiplayerGameFuzzTests(unittest.TestCase):
    def test_every_registered_game_has_invariants(self) -> None:
        ids = {spec.id for spec in GAME_REGISTRY.list_games()}
        self.assertEqual(ids, set(INVARIANTS))

    def test_random_play_keeps_invariants(self) -> None:
        img = Image.new("RGB", (64, 64))
        draw = ImageDraw.Draw(img)
        for spec in GAME_REGISTRY.list_games():
            for solo in {False, spec.supports_2p}:
                for seed in SEEDS:
                    with self.subTest(game=spec.id, solo=solo, seed=seed):
                        with seeded(seed) as rng:
                            self._fuzz(spec.id, solo, rng, draw)

    def _fuzz(
        self, game_id: str, solo: bool, rng: random.Random, draw: ImageDraw.ImageDraw
    ) -> None:
        game = GAME_REGISTRY.create_game(game_id, solo=solo)
        game.sfx_callback = lambda _name: None
        check = INVARIANTS[game_id]
        p1, p2 = random_inputs(rng), random_inputs(rng)
        for frame in range(FRAMES):
            if rng.random() < 0.15:
                p1 = random_inputs(rng)
            if rng.random() < 0.15:
                p2 = random_inputs(rng)
            p1_events = rng.sample(ACTIONS, k=rng.randint(0, 1))
            p2_events = rng.sample(ACTIONS, k=rng.randint(0, 1))
            game.update(rng.choice(DT_CHOICES), p1, p2, p1_events, p2_events)
            if frame % 5 == 0:
                game.render(draw)
            self.assertIsInstance(game.get_status_text(), str)
            score = game.get_final_score()
            self.assertIsInstance(score, int)
            self.assertGreaterEqual(score, 0)
            self.assertEqual(non_finite_attrs(game), [], f"frame {frame}")
            check(self, game)
            if game.is_game_over():
                game.reset()
                self.assertFalse(game.is_game_over(), "reset left game over")


class AttractGameFuzzTests(unittest.TestCase):
    def test_ai_play_survives_dt_stutter(self) -> None:
        classes: list[Any] = [
            type(g) for g in RetroArcadeRenderer(randomize=False).games
        ]
        for cls in classes:
            takes_rng = "rng" in inspect.signature(cls).parameters
            for seed in SEEDS:
                with self.subTest(game=cls.__name__, seed=seed):
                    with seeded(seed) as rng:
                        game = cls(random.Random(seed)) if takes_rng else cls()
                        self._fuzz(game, rng)

    def _fuzz(self, game: Any, rng: random.Random) -> None:
        frame_img = Image.new("RGB", (64, 64))
        draw = ImageDraw.Draw(frame_img)
        for frame in range(FRAMES):
            game.update(rng.choice(DT_CHOICES))
            if frame % 5 == 0:
                game.draw(frame_img, draw, size=64)
            self.assertEqual(non_finite_attrs(game), [], f"frame {frame}")


if __name__ == "__main__":
    unittest.main()
