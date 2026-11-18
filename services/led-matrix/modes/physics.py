"""Physics scenes: orbits, bouncing particles, ripples, pendulums, sand, fire,
volcano, and fireworks, rotated in randomized order."""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from typing import TypeVar

import numpy as np
from PIL import Image, ImageDraw

from modes.base import ModeRenderer, RenderContext

# Trail decay factors and cellular-automaton speeds are tuned at this rate;
# scenes scale them by elapsed time so the look does not depend on the FPS.
REFERENCE_HZ = 30.0
MAX_DT = 0.05


def fade(trail: np.ndarray, decay: float, dt: float) -> None:
    """Decay a trail buffer by `decay` per reference frame, in place."""
    trail *= decay ** (dt * REFERENCE_HZ)


def to_image(rgb: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), mode="RGB")


def plot(trail: np.ndarray, pos: np.ndarray, colors: np.ndarray) -> None:
    """Max-blend one pixel per position into a (size, size, 3) trail."""
    size = trail.shape[0]
    px = np.round(pos[:, 0]).astype(int)
    py = np.round(pos[:, 1]).astype(int)
    inside = (px >= 0) & (px < size) & (py >= 0) & (py < size)
    np.maximum.at(trail, (py[inside], px[inside]), colors[inside])


class FixedStep:
    """Counts fixed-rate simulation steps owed for elapsed time."""

    def __init__(self, hz: float = REFERENCE_HZ, max_steps: int = 4) -> None:
        self.interval = 1.0 / hz
        self.max_steps = max_steps
        self.pending = 0.0

    def reset(self) -> None:
        self.pending = 0.0

    def steps(self, dt: float) -> int:
        self.pending += dt
        count = int(self.pending / self.interval)
        self.pending -= count * self.interval
        return min(count, self.max_steps)


class PhysicsScene(ABC):
    """One scene. `elapsed` is the scene's own clock since its last reset,
    independent of the rotation timer, so scenes animate with cycling off."""

    name = ""

    def __init__(self, rng: np.random.Generator) -> None:
        self.rng = rng
        self.size = 0
        self.elapsed = 0.0

    def reset(self, size: int) -> None:
        self.size = size
        self.elapsed = 0.0
        self.setup()

    def render(self, dt: float) -> Image.Image:
        self.elapsed += dt
        return self.update(dt)

    @abstractmethod
    def setup(self) -> None:
        """Initialize state for a fresh run at self.size."""

    @abstractmethod
    def update(self, dt: float) -> Image.Image:
        """Advance by dt seconds and return the frame."""


class OrbitScene(PhysicsScene):
    name = "Orbit"
    COUNT = 36
    SUN_RADIUS = 7

    def setup(self) -> None:
        size = self.size
        angles = np.linspace(0, 2 * np.pi, self.COUNT, endpoint=False)
        angles += self.rng.uniform(-0.08, 0.08, self.COUNT)
        radius = self.rng.uniform(11, size / 2 - 5, self.COUNT)
        direction = np.column_stack((np.cos(angles), np.sin(angles)))
        self.center = np.array([size / 2, size / 2])
        self.pos = self.center + direction * radius[:, None]
        tangent = np.column_stack((-direction[:, 1], direction[:, 0]))
        self.vel = tangent * self.rng.uniform(14.5, 15.2, (self.COUNT, 1))
        self.colors = [(80, 160 + i % 90, 255) for i in range(self.COUNT)]

    def update(self, dt: float) -> Image.Image:
        size = self.size
        delta = self.center - self.pos
        radius = np.maximum(np.linalg.norm(delta, axis=1), 5)
        self.vel += delta / radius[:, None] * (220 / radius[:, None]) * dt
        self.pos += self.vel * dt

        # Bounce off the sun's surface.
        offset = self.pos - self.center
        distance = np.linalg.norm(offset, axis=1)
        inside = distance < self.SUN_RADIUS
        if np.any(inside):
            normal = offset[inside] / np.maximum(distance[inside][:, None], 1)
            normal[distance[inside] < 1] = (1, 0)
            self.pos[inside] = self.center + normal * self.SUN_RADIUS
            inward = np.sum(self.vel[inside] * normal, axis=1)
            self.vel[inside] -= np.minimum(inward, 0)[:, None] * 1.8 * normal

        low = self.pos < 2
        high = self.pos > size - 3
        self.pos = np.clip(self.pos, 2, size - 3)
        self.vel[low | high] *= -0.88

        frame = Image.new("RGB", (size, size), (0, 0, 0))
        draw = ImageDraw.Draw(frame)
        c = size // 2
        draw.ellipse((c - 3, c - 3, c + 3, c + 3), fill=(255, 195, 70))
        for (x, y), color in zip(self.pos, self.colors, strict=True):
            px, py = round(x), round(y)
            draw.rectangle((px, py, px + 1, py + 1), fill=color)
        return frame


class BouncingScene(PhysicsScene):
    name = "Bouncing balls"
    COUNT = 36
    GRAVITY = 36.0

    def setup(self) -> None:
        self.pos = self.rng.uniform(4, self.size - 5, (self.COUNT, 2))
        self.vel = self.rng.normal(0, 9, (self.COUNT, 2))
        self.colors = [(90 + i % 120, 190, 255) for i in range(self.COUNT)]

    def update(self, dt: float) -> Image.Image:
        size = self.size
        self.vel[:, 1] += self.GRAVITY * dt
        self.pos += self.vel * dt

        # Side walls reflect with loss.
        low_x = self.pos[:, 0] < 2
        high_x = self.pos[:, 0] > size - 3
        self.vel[low_x | high_x, 0] *= -0.88

        # The floor rebounds, kicking slow balls back up so motion never dies.
        floor = self.pos[:, 1] > size - 3
        if np.any(floor):
            rebound = np.abs(self.vel[floor, 1]) * 0.92
            too_slow = rebound < 18.0
            rebound[too_slow] = self.rng.uniform(28.0, 48.0, int(too_slow.sum()))
            self.vel[floor, 1] = -rebound
            self.vel[floor, 0] += self.rng.normal(0, 3.0, int(floor.sum()))
        ceiling = self.pos[:, 1] < 2
        self.vel[ceiling, 1] = np.abs(self.vel[ceiling, 1]) * 0.90
        self.pos = np.clip(self.pos, 2, size - 3)

        # Soft pairwise repulsion between overlapping balls.
        delta = self.pos[:, None, :] - self.pos[None, :, :]
        dist2 = np.sum(delta * delta, axis=2)
        collision = (dist2 > 0) & (dist2 < 16)
        push = np.sum(
            np.where(
                collision[:, :, None], delta / np.maximum(dist2[:, :, None], 1), 0
            ),
            axis=1,
        )
        self.vel += push * 25 * dt
        self.vel *= 0.999

        frame = Image.new("RGB", (size, size), (0, 0, 0))
        draw = ImageDraw.Draw(frame)
        for (x, y), color in zip(self.pos, self.colors, strict=True):
            px, py = round(x), round(y)
            draw.rectangle((px, py, px + 1, py + 1), fill=color)
        return frame


class RippleScene(PhysicsScene):
    """Damped 2D wave equation with raindrops. A wave speed well inside the
    stability limit and soft-edged drops keep the surface free of the
    pixel-scale checkerboard a single-pixel impulse excites."""

    name = "Ripples"
    DROP_CHANCE = 0.12  # per step
    WAVE_SPEED2 = 0.25  # (cells per substep)^2; stable below 0.5
    SUBSTEPS = 2
    DAMPING = 0.984  # per substep
    DROP = np.exp(-(np.add.outer(np.arange(-2, 3) ** 2, np.arange(-2, 3) ** 2)) / 1.5)

    def setup(self) -> None:
        size = self.size
        self.wave = np.zeros((size, size), dtype=np.float32)
        self.previous = self.wave.copy()
        self._drop(size // 2, size // 2, 60.0)
        self.clock = FixedStep()

    def _drop(self, x: int, y: int, height: float) -> None:
        self.wave[y - 2 : y + 3, x - 2 : x + 3] += height * self.DROP

    def _substep(self) -> None:
        w = self.wave
        laplacian = np.zeros_like(w)
        laplacian[1:-1, 1:-1] = (
            w[:-2, 1:-1] + w[2:, 1:-1] + w[1:-1, :-2] + w[1:-1, 2:] - 4 * w[1:-1, 1:-1]
        )
        next_wave = (
            2 * w - self.previous + self.WAVE_SPEED2 * laplacian
        ) * self.DAMPING
        next_wave[[0, -1], :] = 0
        next_wave[:, [0, -1]] = 0
        self.previous, self.wave = w, next_wave

    def update(self, dt: float) -> Image.Image:
        for _ in range(self.clock.steps(dt)):
            for _ in range(self.SUBSTEPS):
                self._substep()
            if self.rng.random() < self.DROP_CHANCE:
                x, y = self.rng.integers(8, self.size - 8, 2)
                self._drop(int(x), int(y), 45.0)
        value = np.clip(np.abs(self.wave) * 7, 0, 255)
        return to_image(np.stack((value / 5, value / 2, value), axis=2))


class PendulumWaveScene(PhysicsScene):
    """16 pendulums whose periods realign every PATTERN_SECONDS."""

    name = "Pendulum wave"
    COUNT = 16
    PATTERN_SECONDS = 20.0
    BASE_CYCLES = 10
    AMPLITUDE = 0.20
    ANCHOR_Y = 2

    def setup(self) -> None:
        size = self.size
        self.trail = np.zeros((size, size, 3), dtype=np.float32)
        self.anchors_x = np.linspace(12, size - 13, self.COUNT)
        self.lengths = np.linspace(26.0, 56.0, self.COUNT)
        hues = np.arange(self.COUNT) / self.COUNT * 2 * np.pi
        shifts = (0.0, 2 * np.pi / 3, 4 * np.pi / 3)
        self.colors = [
            tuple(int(127.5 * (1 + np.sin(hue + shift))) for shift in shifts)
            for hue in hues
        ]

    def update(self, dt: float) -> Image.Image:
        size = self.size
        fade(self.trail, 0.86, dt)
        frame = to_image(self.trail)
        draw = ImageDraw.Draw(frame)
        y0 = self.ANCHOR_Y
        draw.line(
            (int(self.anchors_x[0]) - 3, y0, int(self.anchors_x[-1]) + 3, y0),
            fill=(24, 32, 48),
        )

        t = self.elapsed % self.PATTERN_SECONDS
        cycles = self.BASE_CYCLES + np.arange(self.COUNT)
        theta = self.AMPLITUDE * np.sin(2 * np.pi * cycles / self.PATTERN_SECONDS * t)
        bob_x = np.round(self.anchors_x + self.lengths * np.sin(theta)).astype(int)
        bob_y = np.round(y0 + self.lengths * np.cos(theta)).astype(int)
        for ax, px, py, color in zip(
            self.anchors_x, bob_x, bob_y, self.colors, strict=True
        ):
            draw.line((int(round(ax)), y0, px, py), fill=(20, 28, 44))
            if 0 <= px < size - 1 and 0 <= py < size - 1:
                self.trail[py : py + 2, px : px + 2] = color
            draw.rectangle((px, py, px + 1, py + 1), fill=color)
        return frame


class SandScene(PhysicsScene):
    """Sand pours from three spouts, then drains through a floor opening that
    widens to the full floor so the pile empties. The fill/drain loop runs on
    the scene clock, so it repeats while the scene is held."""

    name = "Sand"
    FILL_SECONDS = 16.5
    LOOP_SECONDS = 20.0
    SPOUTS = (16, 32, 48)
    PALETTE = np.array(
        [
            [0, 0, 0],
            [255, 195, 60],  # amber
            [235, 120, 40],  # copper
            [240, 75, 65],  # coral
            [50, 215, 185],  # cyan
        ],
        dtype=np.uint8,
    )

    def setup(self) -> None:
        self.grid = np.zeros((self.size, self.size), dtype=np.uint8)
        self.grid[2, self.SPOUTS] = (1, 2, 4)
        self.clock = FixedStep()

    def _step(self, drain: float | None) -> None:
        """drain is None while filling, else drain-phase progress in 0-1."""
        grid, size, rng = self.grid, self.size, self.rng
        if drain is None:
            if rng.random() < 0.85:
                for x in self.SPOUTS:
                    if rng.random() < 0.65 and grid[2, x] == 0:
                        grid[2, x] = rng.integers(1, len(self.PALETTE))
        else:
            mid = size // 2
            half = int(8 + drain * 1.5 * mid)
            grid[size - 1, max(0, mid - half) : mid + half] = 0

        # Bottom-up so each grain moves at most one cell per step.
        for y in range(size - 2, -1, -1):
            below = grid[y + 1]
            for x in rng.permutation(np.flatnonzero(grid[y])):
                if below[x] == 0:
                    target = x
                else:
                    left = x > 0 and below[x - 1] == 0
                    right = x < size - 1 and below[x + 1] == 0
                    if left and right:
                        target = x + (1 if rng.random() < 0.5 else -1)
                    elif left:
                        target = x - 1
                    elif right:
                        target = x + 1
                    else:
                        continue
                below[target] = grid[y, x]
                grid[y, x] = 0

    def update(self, dt: float) -> Image.Image:
        t = self.elapsed % self.LOOP_SECONDS
        drain = None
        if t >= self.FILL_SECONDS:
            drain = (t - self.FILL_SECONDS) / (self.LOOP_SECONDS - self.FILL_SECONDS)
        for _ in range(self.clock.steps(dt)):
            self._step(drain)
        return Image.fromarray(self.PALETTE[self.grid], mode="RGB")


def _fire_palette() -> np.ndarray:
    """Heat 0-255 to colour: black below a threshold so flames have edges,
    then ember red, orange, yellow, and a white-hot core."""
    heat = (0, 40, 80, 140, 200, 255)
    rgb = np.array(
        [
            (0, 0, 0),
            (0, 0, 0),
            (130, 12, 0),
            (240, 80, 0),
            (255, 190, 20),
            (255, 250, 210),
        ]
    )
    levels = np.arange(256)
    return np.stack(
        [np.interp(levels, heat, rgb[:, c]) for c in range(3)], axis=1
    ).astype(np.uint8)


def _smooth_noise(
    rng: np.random.Generator, shape: tuple[int, int], passes: int = 4
) -> np.ndarray:
    """Tileable noise in 0-1, box-blurred so neighbouring cells correlate."""
    noise = rng.random(shape)
    for _ in range(passes):
        noise = (
            noise
            + np.roll(noise, 1, 0)
            + np.roll(noise, -1, 0)
            + np.roll(noise, 1, 1)
            + np.roll(noise, -1, 1)
        ) / 5
    noise -= noise.min()
    return noise / max(float(noise.max()), 1e-6)


class FireScene(PhysicsScene):
    """Heat field fed by a wandering fuel bed. Cooling comes from a smooth
    noise map that scrolls up with the heat, so each parcel cools steadily
    and the flames form separate tongues that die out below the top edge."""

    name = "Fire"
    PALETTE = _fire_palette()
    COOLING = 5.5  # mean heat lost per row risen
    FUEL_WAVES = 3

    def setup(self) -> None:
        size = self.size
        self.heat = np.zeros((size, size), dtype=np.float32)
        self.columns = np.arange(size)
        # Both maps scroll up one row per step, moving with the heat, so a
        # rising parcel keeps a consistent cooling rate and sway.
        self.cooling_map = (
            _smooth_noise(self.rng, (size * 4, size)) * 2 * self.COOLING
        ).astype(np.float32)
        self.sway_map = np.round(
            (_smooth_noise(self.rng, (size * 4, size)) - 0.5) * 2.2
        ).astype(int)
        self.map_offset = 0
        self.fuel_freq = self.rng.uniform(0.12, 0.35, self.FUEL_WAVES)
        self.fuel_speed = self.rng.uniform(0.6, 1.6, self.FUEL_WAVES) * self.rng.choice(
            (-1, 1), self.FUEL_WAVES
        )
        self.fuel_phase = self.rng.uniform(0, 2 * np.pi, self.FUEL_WAVES)
        self.clock = FixedStep()
        for _ in range(size):
            self._step()

    def _fuel(self) -> np.ndarray:
        """Fuel 0-1 per column: a few drifting sine waves, so hot spots wander."""
        t = self.elapsed
        waves = np.sin(
            self.columns[:, None] * self.fuel_freq
            + self.fuel_speed * t
            + self.fuel_phase
        )
        return np.clip(waves.mean(axis=1) * 1.4 + 0.45, 0.0, 1.0)

    def _step(self) -> None:
        size, heat, rng = self.size, self.heat, self.rng

        # Rise one row with sideways sway, then blur slightly and cool.
        rows = (np.arange(size - 1) + self.map_offset) % len(self.cooling_map)
        self.map_offset += 1
        cols = np.clip(self.columns + self.sway_map[rows], 0, size - 1)
        risen = heat[np.arange(1, size)[:, None], cols]
        padded = np.pad(risen, ((0, 0), (1, 1)), mode="edge")
        risen = risen * 0.8 + (padded[:, :-2] + padded[:, 2:]) * 0.1
        heat[:-1] = np.maximum(risen - self.cooling_map[rows], 0)

        # Feed the bed: hot where fuel is high, flickering.
        fuel = self._fuel() * rng.uniform(0.75, 1.0, size)
        heat[-1] = 255 * fuel
        heat[-2] = np.maximum(heat[-2], 230 * fuel * (rng.random(size) < 0.5))

    def update(self, dt: float) -> Image.Image:
        for _ in range(self.clock.steps(dt)):
            self._step()
        return Image.fromarray(
            self.PALETTE[np.clip(self.heat, 0, 255).astype(np.uint8)], mode="RGB"
        )


class VolcanoScene(PhysicsScene):
    name = "Volcano"
    COUNT = 70
    GRAVITY = 55.0
    TEMP_COLORS = np.array(
        [[85, 14, 6], [215, 40, 15], [255, 135, 25], [255, 245, 180]], dtype=np.float32
    )
    TEMP_STEPS = np.array([0.18, 0.45, 0.75])

    def setup(self) -> None:
        size = self.size
        self.nozzle_y = size - 2.0
        self.pos = np.zeros((self.COUNT, 2))
        self.vel = np.zeros((self.COUNT, 2))
        self.temp = np.zeros(self.COUNT)
        self.trail = np.zeros((size, size, 3), dtype=np.float32)
        self._erupt(np.ones(self.COUNT, dtype=bool))
        self.pos[:, 0] = size / 2.0
        # Stagger lifetimes so sparks don't all erupt together.
        self.temp = np.linspace(0.05, 1.0, self.COUNT)

    def _erupt(self, which: np.ndarray) -> None:
        n = int(which.sum())
        self.pos[which, 0] = self.size / 2.0 + self.rng.normal(0, 1.8, n)
        self.pos[which, 1] = self.nozzle_y
        self.vel[which, 0] = self.rng.normal(0, 12.0, n)
        self.vel[which, 1] = self.rng.uniform(-78.0, -52.0, n)
        self.temp[which] = self.rng.uniform(0.85, 1.0, n)

    def update(self, dt: float) -> Image.Image:
        size = self.size
        self.vel[:, 1] += self.GRAVITY * dt
        self.vel[:, 0] *= 0.994 ** (dt * REFERENCE_HZ)
        self.pos += self.vel * dt
        self.temp -= 0.28 * dt

        # Hot sparks splash off the ground.
        bounce = (
            (self.pos[:, 1] >= self.nozzle_y)
            & (self.vel[:, 1] > 0)
            & (self.temp > 0.15)
        )
        if np.any(bounce):
            self.vel[bounce, 1] *= -0.38
            self.vel[bounce, 0] += self.rng.normal(0, 9.0, int(bounce.sum()))
            self.pos[bounce, 1] = self.nozzle_y

        dead = (
            (self.temp <= 0)
            | (self.pos[:, 0] < 0)
            | (self.pos[:, 0] >= size)
            | (self.pos[:, 1] > size)
        )
        if np.any(dead):
            self._erupt(dead)

        fade(self.trail, 0.82, dt)
        colors = self.TEMP_COLORS[np.searchsorted(self.TEMP_STEPS, self.temp)]
        plot(self.trail, self.pos, colors)

        frame = to_image(self.trail)
        cx = size // 2
        ImageDraw.Draw(frame).polygon(
            [(cx - 7, size - 1), (cx, size - 3), (cx + 7, size - 1)], fill=(45, 18, 12)
        )
        return frame


class FireworksScene(PhysicsScene):
    """Shells launch from the ground, burst near their apex into peony, ring,
    or two-tone stars that fall, slow, fade, and sometimes crackle."""

    name = "Fireworks"
    MAX_SHELLS = 4
    MAX_STARS = 480
    SHELL_GRAVITY = 36.0
    STAR_GRAVITY = 14.0
    STAR_DRAG = 1.3  # 1/s
    COLORS = np.array(
        [
            [255, 60, 60],
            [255, 150, 30],
            [255, 230, 60],
            [80, 255, 90],
            [60, 200, 255],
            [90, 110, 255],
            [200, 80, 255],
            [255, 90, 200],
            [255, 255, 255],
        ],
        dtype=np.float32,
    )

    def setup(self) -> None:
        size = self.size
        self.trail = np.zeros((size, size, 3), dtype=np.float32)
        self.shell_pos = np.zeros((0, 2))
        self.shell_vel = np.zeros((0, 2))
        self.star_pos = np.zeros((0, 2))
        self.star_vel = np.zeros((0, 2))
        self.star_color = np.zeros((0, 3), dtype=np.float32)
        self.star_age = np.zeros(0)
        self.star_life = np.zeros(0)
        self.star_crackle = np.zeros(0, dtype=bool)
        self.next_launch = 0.0

    def _launch(self) -> None:
        count = 1 if self.rng.random() < 0.8 else int(self.rng.integers(2, 4))
        count = min(count, self.MAX_SHELLS - len(self.shell_pos))
        if count <= 0:
            return
        size = self.size
        pos = np.column_stack(
            (self.rng.uniform(10, size - 10, count), np.full(count, size - 1.0))
        )
        # Apex height v^2 / 2g lands bursts in the upper half.
        vel = np.column_stack(
            (self.rng.normal(0, 4, count), -self.rng.uniform(52, 62, count))
        )
        self.shell_pos = np.vstack((self.shell_pos, pos))
        self.shell_vel = np.vstack((self.shell_vel, vel))

    def _burst(self, at: np.ndarray) -> None:
        rng = self.rng
        count = int(rng.integers(60, 91))
        count = min(count, self.MAX_STARS - len(self.star_pos))
        if count <= 0:
            return
        style = rng.choice(("peony", "ring", "two-tone"), p=(0.5, 0.25, 0.25))
        angles = rng.uniform(0, 2 * np.pi, count)
        if style == "ring":
            speed = rng.normal(26, 1.0, count)
        else:
            # sqrt spreads stars through the sphere instead of bunching at the center.
            speed = 32 * np.sqrt(rng.uniform(0.05, 1.0, count))
        vel = np.column_stack((np.cos(angles), np.sin(angles))) * speed[:, None]
        primary, secondary = rng.choice(len(self.COLORS), 2, replace=False)
        color = np.repeat(self.COLORS[primary][None, :], count, axis=0)
        if style == "two-tone":
            color[count // 2 :] = self.COLORS[secondary]

        self.star_pos = np.vstack((self.star_pos, np.repeat(at[None, :], count, 0)))
        self.star_vel = np.vstack((self.star_vel, vel))
        self.star_color = np.vstack((self.star_color, color))
        self.star_age = np.concatenate((self.star_age, np.zeros(count)))
        self.star_life = np.concatenate((self.star_life, rng.uniform(1.5, 2.3, count)))
        crackle = rng.random() < 0.3
        self.star_crackle = np.concatenate((self.star_crackle, np.full(count, crackle)))

    def _update_shells(self, dt: float) -> None:
        if not len(self.shell_pos):
            return
        self.shell_vel[:, 1] += self.SHELL_GRAVITY * dt
        self.shell_pos += self.shell_vel * dt
        plot(
            self.trail,
            self.shell_pos,
            np.tile([255.0, 200.0, 120.0], (len(self.shell_pos), 1)),
        )
        burst = self.shell_vel[:, 1] > -6
        for at in self.shell_pos[burst]:
            self._burst(at)
        self.shell_pos = self.shell_pos[~burst]
        self.shell_vel = self.shell_vel[~burst]

    def _update_stars(self, dt: float) -> None:
        if not len(self.star_pos):
            return
        self.star_age += dt
        self.star_vel *= np.exp(-self.STAR_DRAG * dt)
        self.star_vel[:, 1] += self.STAR_GRAVITY * dt
        self.star_pos += self.star_vel * dt

        alive = (self.star_age < self.star_life) & (self.star_pos[:, 1] < self.size)
        for name in (
            "star_pos",
            "star_vel",
            "star_color",
            "star_age",
            "star_life",
            "star_crackle",
        ):
            setattr(self, name, getattr(self, name)[alive])

        # A white flash that settles into the star colour, dimming with age.
        progress = self.star_age / self.star_life
        flash = np.clip(1 - self.star_age / 0.15, 0, 1)[:, None]
        color = self.star_color * (1 - flash) + 255 * flash
        brightness = (1 - progress) ** 0.8
        twinkle = self.star_crackle & (progress > 0.6)
        brightness[twinkle] *= self.rng.random(int(twinkle.sum())) < 0.5
        plot(self.trail, self.star_pos, color * brightness[:, None])

    def update(self, dt: float) -> Image.Image:
        self.next_launch -= dt
        if self.next_launch <= 0:
            self._launch()
            self.next_launch = self.rng.uniform(0.5, 1.3)
        fade(self.trail, 0.8, dt)
        self._update_shells(dt)
        self._update_stars(dt)
        return to_image(self.trail)


SCENES: tuple[type[PhysicsScene], ...] = (
    OrbitScene,
    BouncingScene,
    RippleScene,
    PendulumWaveScene,
    SandScene,
    FireScene,
    VolcanoScene,
    FireworksScene,
)


SceneT = TypeVar("SceneT", bound=PhysicsScene)


class PhysicsRenderer(ModeRenderer):
    PERIOD = 20.0
    NUM_SCENES = len(SCENES)

    def __init__(self, randomize: bool = True) -> None:
        self.rng = np.random.default_rng()
        self.scenes = [scene(self.rng) for scene in SCENES]
        self.randomize = randomize
        self.scene_timer = 0.0
        self.last_scene = -1
        self.playlist = list(range(self.NUM_SCENES))
        self.playlist_pos = 0
        if randomize:
            random.shuffle(self.playlist)
        self.current_scene = self.playlist[0]

    def scene(self, scene_type: type[SceneT]) -> SceneT:
        scene = self.scenes[SCENES.index(scene_type)]
        assert isinstance(scene, scene_type)
        return scene

    def reset(self) -> None:
        """Start a fresh rotation (called when switching into Physics mode)."""
        self.scene_timer = 0.0
        self.last_scene = -1
        self.playlist_pos = 0
        if self.randomize:
            random.shuffle(self.playlist)
        self.current_scene = self.playlist[0]

    def set_scene(self, scene: int) -> None:
        """Manually select a scene by index and reset the timer."""
        self.current_scene = scene % self.NUM_SCENES
        self.scene_timer = 0.0
        if self.randomize:
            self.playlist_pos = self.playlist.index(self.current_scene)

    def _next_scene(self) -> None:
        if not self.randomize:
            self.current_scene = (self.current_scene + 1) % self.NUM_SCENES
            return
        self.playlist_pos += 1
        if self.playlist_pos >= len(self.playlist):
            last = self.playlist[-1]
            random.shuffle(self.playlist)
            # Never repeat a scene across the playlist boundary.
            if len(self.playlist) > 1 and self.playlist[0] == last:
                self.playlist[0], self.playlist[1] = self.playlist[1], self.playlist[0]
            self.playlist_pos = 0
        self.current_scene = self.playlist[self.playlist_pos]

    def render(self, ctx: RenderContext, size: int = 64) -> Image.Image:
        dt = min(max(ctx.dt, 0.0), MAX_DT)
        if ctx.ha_state is None or ctx.ha_state.cycle:
            self.scene_timer += ctx.dt
            while self.scene_timer >= self.PERIOD:
                self.scene_timer -= self.PERIOD
                self._next_scene()
        else:
            self.scene_timer = 0.0

        scene = self.scenes[self.current_scene]
        if self.current_scene != self.last_scene or scene.size != size:
            self.last_scene = self.current_scene
            scene.reset(size)
        return scene.render(dt)
