"""Regression tests for the Fireworks show mode."""

import time
import unittest
from unittest.mock import patch

import numpy as np

from modes.base import HAState, PlaybackState, RenderContext
from modes.fireworks import (
    MAX_PARTICLES,
    PASSAGES,
    FireworksRenderer,
    FireworksShow,
    ShowDirector,
    color,
)

FPS = 60


def context() -> RenderContext:
    return RenderContext(
        tick=0.0,
        dt=1 / FPS,
        ha_state=HAState(mode="Fireworks"),
        spotify_state=PlaybackState(),
        spotify_art=None,
    )


def lit(frame: np.ndarray) -> float:
    """Share of pixels that are visibly lit."""
    return float((frame.max(axis=2) > 40).mean())


class ShellTests(unittest.TestCase):
    def setUp(self) -> None:
        self.show = FireworksShow(np.random.default_rng(1))

    def run_show(self, seconds: float) -> list[np.ndarray]:
        frames = []
        for _ in range(int(seconds * FPS)):
            self.show.step(1 / FPS)
            frames.append(np.asarray(self.show.render(1 / FPS)))
        return frames

    def test_every_shell_type_bursts_into_visible_stars(self) -> None:
        for kind in FireworksShow.SHELLS:
            show = self.show = FireworksShow(np.random.default_rng(2))
            show.launch(32, kind, apex=20)
            frames = self.run_show(2.5)
            self.assertGreaterEqual(show.bursts, 1, kind)
            self.assertGreater(max(lit(f) for f in frames), 0.01, kind)

    def test_shells_burst_near_their_apex(self) -> None:
        self.show.launch(32, "peony", apex=15)
        while len(self.show.shells):
            self.show.step(1 / FPS)
        heights = self.show.stars.pos[:, 1]
        self.assertAlmostEqual(float(np.median(heights)), 15, delta=3)

    def test_crossette_stars_split_into_four(self) -> None:
        self.show.crossette(np.array([32.0, 20.0]), [color("red")], 1.0)
        first = int(np.isfinite(self.show.stars.split_at).sum())
        self.assertEqual(first, 8)
        for _ in range(int(0.5 * FPS)):
            self.show.step(1 / FPS)
        kids = (self.show.stars.life == 0.9).sum()
        self.assertEqual(int(kids), 32)

    def test_bouquet_breaks_into_small_shells_that_burst(self) -> None:
        self.show.bouquet(np.array([32.0, 20.0]), [color("red")], 1.0)
        self.assertGreaterEqual(len(self.show.shells), 3)
        self.run_show(1.0)
        self.assertEqual(len(self.show.shells), 0)
        self.assertGreaterEqual(self.show.bursts, 4)

    def test_stars_die_out(self) -> None:
        self.show.launch(32, "willow", apex=12)
        self.run_show(7)
        self.assertEqual(len(self.show.stars), 0)
        self.assertEqual(len(self.show.shells), 0)

    def test_fountain_sprays_up_from_the_ground_then_stops(self) -> None:
        self.show.fountain(32, 2.0, color("gold"), crackle=False)
        frames = self.run_show(1.5)
        stars = self.show.stars
        self.assertGreater(len(stars), 20)
        # A narrow cone: stars rise from the base near column 32.
        self.assertLess(float(np.abs(stars.pos[:, 0] - 32).mean()), 8)
        self.assertLess(float(stars.pos[:, 1].min()), 50)
        self.assertGreater(lit(frames[-1]), 0.005)
        self.run_show(2.5)
        self.assertEqual(self.show.fountains, [])
        self.assertEqual(len(self.show.stars), 0)

    def test_overlapping_bursts_keep_their_colour(self) -> None:
        # Regression: additive blending turned dense finales into white mush.
        at = np.array([32.0, 20.0])
        for _ in range(6):
            self.show.peony(at, [color("blue")], 1.0)
        self.run_show(0.5)
        sky = np.asarray(self.show.render(1 / FPS)).astype(int)
        bright = sky[sky.max(axis=2) > 80]
        self.assertGreater(len(bright), 0)
        self.assertTrue((bright[:, 2] >= bright[:, 0]).all())

    def test_rendered_star_changes_from_blue_to_gold_before_fading(self) -> None:
        stars = self.show.stars
        stars.add(
            np.array([32.0, 20.0]),
            np.zeros((1, 2)),
            color("blue"),
            2.0,
            gravity=0,
            end_rgb=color("gold"),
        )
        pixels = []
        for age in (0.3, 1.4, 1.9):
            stars.age[:] = age
            self.show.sky[:] = 0
            pixels.append(np.asarray(self.show.render(1 / FPS))[20, 32].astype(int))
        self.assertGreater(pixels[0][2], 3 * pixels[0][0])
        self.assertGreater(pixels[1][0], 3 * pixels[1][2])
        self.assertLess(pixels[2].max(), pixels[1].max())

    def test_split_and_multibreak_stars_keep_the_color_transition(self) -> None:
        for kind in ("crossette", "bouquet"):
            with self.subTest(kind=kind):
                self.show = FireworksShow(np.random.default_rng(2))
                getattr(self.show, kind)(
                    np.array([32.0, 20.0]), [color("blue")], 1.0, color("gold")
                )
                self.run_show(0.65)
                stars = self.show.stars
                parents = (
                    stars.spark_rate > 0 if kind == "crossette" else stars.life > 1
                )
                self.assertGreater(int(parents.sum()), 0)
                np.testing.assert_allclose(
                    stars.end_color[parents],
                    np.tile(color("gold"), (int(parents.sum()), 1)),
                )
                self.assertTrue(
                    (stars.color[parents, 2] > stars.color[parents, 0]).all()
                )

    def test_shed_embers_keep_blue_hue_and_fade_without_new_emitters(self) -> None:
        self.show._emit_sparks(
            np.array([[32.0, 20.0]]), color("blue")[None, :], np.array([60.0]), 1 / FPS
        )
        stars = self.show.stars
        self.assertEqual(len(stars), 1)
        self.assertEqual(stars.spark_rate[0], 0)
        np.testing.assert_allclose(stars.color[0], color("blue") * 0.65)
        frame = np.asarray(self.show.render(1 / FPS))
        blue = frame[:, :, 2].astype(int)
        red = frame[:, :, 0].astype(int)
        self.assertGreater(int(blue.max()), 100)
        self.assertTrue((blue >= 3 * red).all())
        self.assertGreater(float(stars.life[0]), 0.3)
        self.run_show(1.0)
        self.assertEqual(len(stars), 0)


class ShowTests(unittest.TestCase):
    def test_passages_hold_their_palette_and_change_without_repeating(self) -> None:
        director = ShowDirector(FireworksShow(np.random.default_rng(7)))
        director._plan_next()
        self.assertEqual(director.passage, 0)
        while director.planned_until < director.next_passage:
            director._plan_next()
            self.assertEqual(director.passage, 0)
        director._plan_next()
        self.assertNotEqual(director.passage, 0)

    def test_scheduled_shell_retains_palette_after_passage_changes(self) -> None:
        show = FireworksShow(np.random.default_rng(7))
        director = ShowDirector(show)
        director._launch(1, kind="peony")
        director.passage = 2
        director.planned_until = 10
        with patch.object(show, "launch") as launch:
            director.advance(1)
        rgb = launch.call_args.kwargs["rgb"][0]
        self.assertTrue(any(np.array_equal(rgb, color(n)) for n in PASSAGES[0][:2]))
        np.testing.assert_array_equal(launch.call_args.kwargs["end_rgb"], color("gold"))

    def test_finale_closes_with_one_gold_burst_then_embers_and_darkness(self) -> None:
        renderer = FireworksRenderer(seed=7)
        director = renderer.director
        duration = director._finale(0)
        director.planned_until = duration
        coverage = []
        with patch.object(
            renderer.show, "launch", wraps=renderer.show.launch
        ) as launch:
            for _ in range(int(duration * FPS)):
                coverage.append(lit(np.asarray(renderer.render(context()))))
        closing = launch.call_args.kwargs
        self.assertEqual(closing["kind"], "chrysanthemum")
        self.assertGreater(closing["scale"], 1)
        np.testing.assert_array_equal(closing["rgb"][0], color("gold"))
        self.assertGreater(max(coverage[24 * FPS : 26 * FPS]), 0.01)
        self.assertEqual(max(coverage[27 * FPS : 28 * FPS]), 0)

    def play(
        self, seconds: float, finale_at: float = 40.0
    ) -> tuple[FireworksRenderer, list[float], list[float]]:
        renderer = FireworksRenderer(seed=7)
        renderer.director.next_finale = finale_at
        ctx = context()
        coverage, timings = [], []
        for _ in range(int(seconds * FPS)):
            start = time.perf_counter()
            frame = renderer.render(ctx)
            timings.append(time.perf_counter() - start)
            coverage.append(lit(np.asarray(frame)))
        return renderer, coverage, timings

    def test_show_is_varied_endless_and_has_finales(self) -> None:
        renderer, coverage, timings = self.play(240)
        segments = renderer.director.segments
        self.assertIn("finale", segments)
        self.assertIn("fountains", segments)
        self.assertGreaterEqual(len(set(segments)), 5)
        for a, b in zip(segments, segments[1:], strict=False):
            self.assertFalse(a == b != "finale", segments)
        # Never dark for long: pauses between segments and after finales.
        dark = longest = 0
        for c in coverage:
            dark = dark + 1 if c < 0.002 else 0
            longest = max(longest, dark)
        self.assertLess(longest / FPS, 7.0)
        # Busy finales stay readable rather than whiting out the sky.
        self.assertLess(max(coverage), 0.6)
        # Generous for throttled CI runners; locally the mean is ~0.4 ms.
        self.assertLess(float(np.mean(timings)), 0.02)
        self.assertLess(len(renderer.show.stars), MAX_PARTICLES * 1.3)

    def test_finale_is_the_busiest_part_of_the_show(self) -> None:
        renderer, coverage, _ = self.play(80, finale_at=30.0)
        director = renderer.director
        finale_start = 30.0
        before = np.mean(coverage[: int(finale_start * FPS)])
        during = np.mean(
            coverage[int((finale_start + 10) * FPS) : int((finale_start + 19) * FPS)]
        )
        self.assertIn("finale", director.segments)
        self.assertGreater(during, 2 * before)

    def test_reset_starts_a_fresh_show(self) -> None:
        renderer = FireworksRenderer(seed=3)
        ctx = context()
        for _ in range(10 * FPS):
            renderer.render(ctx)
        self.assertGreater(renderer.director.time, 9)
        renderer.reset()
        self.assertEqual(renderer.director.time, 0.0)
        self.assertEqual(len(renderer.show.stars), 0)

    def test_director_plans_ahead_without_running_dry(self) -> None:
        director = ShowDirector(FireworksShow(np.random.default_rng(5)))
        for _ in range(120 * FPS):
            director.advance(1 / FPS)
            director.show.step(1 / FPS)
            self.assertGreater(director.planned_until, director.time)


if __name__ == "__main__":
    unittest.main()
