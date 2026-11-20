"""Regression tests for the Physics mode scenes and rotation."""

import unittest

import numpy as np

from modes.base import HAState, PlaybackState, RenderContext
from modes.physics import (
    SCENES,
    BouncingScene,
    FireScene,
    PendulumWaveScene,
    PhysicsRenderer,
    RippleScene,
    SandScene,
    VolcanoScene,
)


def context(dt: float = 1 / 60, cycle: bool = False) -> RenderContext:
    return RenderContext(
        tick=0.0,
        dt=dt,
        ha_state=HAState(mode="Physics", cycle=cycle),
        spotify_state=PlaybackState(),
        spotify_art=None,
    )


# Every scene draws from the renderer's RNG; a fixed seed keeps the
# statistical checks (fire coverage, sand peaks) deterministic in CI.
SEED = 7


def run(
    scene_type: type, seconds: float, fps: float = 60
) -> tuple[PhysicsRenderer, list[np.ndarray]]:
    """Hold one scene for `seconds` at `fps`, returning every frame."""
    physics = PhysicsRenderer(randomize=False, seed=SEED)
    physics.set_scene(SCENES.index(scene_type))
    ctx = context(1 / fps)
    frames = [np.asarray(physics.render(ctx)) for _ in range(int(seconds * fps))]
    return physics, frames


def lit(frame: np.ndarray) -> np.ndarray:
    return frame.max(axis=2) > 40


class PhysicsRotationTests(unittest.TestCase):
    def test_scene_list(self) -> None:
        self.assertEqual(
            [scene.name for scene in SCENES],
            [
                "Orbit",
                "Bouncing balls",
                "Ripples",
                "Pendulum wave",
                "Sand",
                "Fire",
                "Volcano",
            ],
        )
        self.assertEqual(PhysicsRenderer.NUM_SCENES, len(SCENES))

    def test_every_scene_renders_a_lit_frame(self) -> None:
        physics = PhysicsRenderer(randomize=False, seed=SEED)
        for index in range(physics.NUM_SCENES):
            physics.set_scene(index)
            frame = physics.render(context(), size=64)
            for _ in range(30):
                frame = physics.render(context(), size=64)
            self.assertEqual((frame.size, frame.mode), ((64, 64), "RGB"))
            self.assertGreater(int(np.asarray(frame).sum()), 0, SCENES[index].name)

    def test_rotation_advances_in_order_and_wraps(self) -> None:
        physics = PhysicsRenderer(randomize=False, seed=SEED)
        advance = context(dt=physics.PERIOD + 0.5, cycle=True)
        visited = []
        for _ in range(physics.NUM_SCENES):
            physics.render(advance)
            visited.append(physics.current_scene)
        self.assertEqual(visited, [*range(1, physics.NUM_SCENES), 0])

    def test_reentering_the_mode_restarts_the_scene(self) -> None:
        physics = PhysicsRenderer(randomize=False, seed=SEED)
        physics.set_scene(SCENES.index(SandScene))
        for _ in range(300):
            physics.render(context())
        self.assertGreater(int((physics.scene(SandScene).grid > 0).sum()), 50)
        physics.reset()
        physics.set_scene(SCENES.index(SandScene))
        physics.render(context())
        self.assertLess(int((physics.scene(SandScene).grid > 0).sum()), 10)


class PhysicsSceneTests(unittest.TestCase):
    def test_fire_forms_flames_instead_of_filling_the_screen(self) -> None:
        _, frames = run(FireScene, 10)
        settled = frames[120:]
        coverage = np.mean([lit(f).mean() for f in settled])
        rows = np.mean([lit(f).mean(axis=1) for f in settled], axis=0)
        # Regression: fire used to light every pixel as a solid orange sheet.
        self.assertLess(coverage, 0.45)
        self.assertGreater(coverage, 0.08)
        self.assertLess(rows[:16].mean(), 0.05)  # top quarter stays dark
        self.assertGreater(rows[48:].mean(), 0.3)  # flames along the bottom
        # Separate tongues: the flame band is broken up across the width.
        band = lit(settled[-1])[40:56].mean(axis=0)
        self.assertGreater(float(band.std()), 0.1)

    def test_fire_is_frame_rate_independent(self) -> None:
        def coverage(fps: int) -> float:
            _, frames = run(FireScene, 6, fps)
            return float(np.mean([lit(f).mean() for f in frames[fps * 2 :]]))

        self.assertAlmostEqual(coverage(30), coverage(60), delta=0.06)

    def test_scenes_keep_animating_with_cycle_off(self) -> None:
        for scene_type in (PendulumWaveScene, SandScene, RippleScene, FireScene):
            physics, frames = run(scene_type, 25)
            self.assertEqual(physics.scene_timer, 0.0)
            self.assertFalse(np.array_equal(frames[-60], frames[-1]), scene_type.name)

    def test_sand_drains_completely_every_loop(self) -> None:
        physics = PhysicsRenderer(randomize=False, seed=SEED)
        physics.set_scene(SCENES.index(SandScene))
        sand = physics.scene(SandScene)
        ctx = context()
        peaks, leftovers = [], []
        for _ in range(3):
            for frame in range(int(SandScene.LOOP_SECONDS * 60)):
                physics.render(ctx)
                if frame == int(SandScene.FILL_SECONDS * 60) - 1:
                    peaks.append(int((sand.grid > 0).sum()))
            leftovers.append(int((sand.grid > 0).sum()))
        self.assertTrue(all(peak > 300 for peak in peaks), peaks)
        self.assertTrue(all(left < 20 for left in leftovers), leftovers)

    def test_ripples_have_no_checkerboard_noise(self) -> None:
        physics, _ = run(RippleScene, 12)
        wave = physics.scene(RippleScene).wave
        # Pixel-scale alternation shows up as a large mixed second difference.
        checker = np.abs(
            wave[:-1, :-1] - wave[1:, :-1] - wave[:-1, 1:] + wave[1:, 1:]
        ).mean()
        self.assertLess(float(checker), 0.5 * float(np.abs(wave).mean()) + 1e-3)

    def test_pendulums_swing_deep_and_stay_on_screen(self) -> None:
        physics, _ = run(PendulumWaveScene, 10)
        self.assertGreater(int(physics.scene(PendulumWaveScene).trail[50:].sum()), 0)

    def test_bouncing_balls_keep_moving(self) -> None:
        physics, _ = run(BouncingScene, 12)
        scene = physics.scene(BouncingScene)
        self.assertGreater(float(np.linalg.norm(scene.vel, axis=1).mean()), 10.0)
        self.assertLess(float(scene.pos[:, 1].min()), 55.0)

    def test_volcano_erupts_into_the_upper_half(self) -> None:
        physics = PhysicsRenderer(randomize=False, seed=SEED)
        physics.set_scene(SCENES.index(VolcanoScene))
        frame = np.asarray(physics.render(context()))
        volcano = physics.scene(VolcanoScene)
        highest = 64.0
        for _ in range(120):
            frame = np.asarray(physics.render(context()))
            highest = min(highest, float(volcano.pos[:, 1].min()))
        self.assertLess(highest, 20.0)
        self.assertGreater(int(frame[:32].sum()), 0)


if __name__ == "__main__":
    unittest.main()
