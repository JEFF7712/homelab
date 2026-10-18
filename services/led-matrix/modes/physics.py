"""Gravity, collisions, and wave propagation on the matrix."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from modes.base import ModeRenderer, RenderContext


class PhysicsRenderer(ModeRenderer):
    PERIOD = 20.0

    def __init__(self) -> None:
        rng = np.random.default_rng()
        self.pos = np.zeros((36, 2), dtype=np.float64)
        self.vel = np.zeros((36, 2), dtype=np.float64)
        self.wave = np.zeros((64, 64), dtype=np.float32)
        self.previous = self.wave.copy()
        self.last_scene = -1
        self.rng = rng

    def _reset_particles(self, scene: int, size: int) -> None:
        if scene == 0:
            angles = np.linspace(0, 2 * np.pi, len(self.pos), endpoint=False)
            angles += self.rng.uniform(-0.08, 0.08, len(self.pos))
            radius = self.rng.uniform(11, size / 2 - 5, len(self.pos))
            direction = np.column_stack((np.cos(angles), np.sin(angles)))
            self.pos = np.array([size / 2, size / 2]) + direction * radius[:, None]
            tangent = np.column_stack((-direction[:, 1], direction[:, 0]))
            self.vel = tangent * self.rng.uniform(14.5, 15.2, (len(self.pos), 1))
        else:
            self.pos = self.rng.uniform(4, size - 5, self.pos.shape)
            self.vel = self.rng.normal(0, 9, self.vel.shape)

    def render(self, ctx: RenderContext, size: int = 64) -> Image.Image:
        scene = int(ctx.tick / self.PERIOD) % 3
        if scene != self.last_scene:
            self.last_scene = scene
            if scene == 2:
                self.wave.fill(0)
                self.previous.fill(0)
            else:
                self._reset_particles(scene, size)
        dt = min(max(ctx.dt, 0.0), 0.05)
        if scene == 2:
            return self._waves(size)
        return self._particles(scene, dt, size)

    def _particles(self, scene: int, dt: float, size: int) -> Image.Image:
        if scene == 0:
            delta = np.array([size / 2, size / 2]) - self.pos
            radius = np.maximum(np.linalg.norm(delta, axis=1), 5)
            self.vel += delta / radius[:, None] * (220 / radius[:, None]) * dt
        else:
            self.vel[:, 1] += 42 * dt
        self.pos += self.vel * dt
        if scene == 0:
            center = np.array([size / 2, size / 2])
            offset = self.pos - center
            distance = np.linalg.norm(offset, axis=1)
            inside = distance < 7
            if np.any(inside):
                normal = offset[inside] / np.maximum(distance[inside][:, None], 1)
                normal[distance[inside] < 1] = (1, 0)
                self.pos[inside] = center + normal * 7
                inward = np.sum(self.vel[inside] * normal, axis=1)
                self.vel[inside] -= np.minimum(inward, 0)[:, None] * 1.8 * normal
        for axis in (0, 1):
            low = self.pos[:, axis] < 2
            high = self.pos[:, axis] > size - 3
            self.pos[low, axis] = 2
            self.pos[high, axis] = size - 3
            self.vel[low | high, axis] *= -0.88
        if scene == 1:
            delta = self.pos[:, None, :] - self.pos[None, :, :]
            dist2 = np.sum(delta * delta, axis=2)
            collision = (dist2 > 0) & (dist2 < 16)
            push = np.sum(
                np.where(
                    collision[:, :, None], delta / np.maximum(dist2[:, :, None], 1), 0
                ),
                axis=1,
            )
            self.vel += push * 20 * dt
        if scene == 1:
            self.vel *= 0.998
        frame = Image.new("RGB", (size, size), (2, 5, 13))
        draw = ImageDraw.Draw(frame)
        if scene == 0:
            draw.ellipse((29, 29, 35, 35), fill=(255, 195, 70))
        for i, (x, y) in enumerate(self.pos):
            color = (80, 160 + i % 90, 255) if scene == 0 else (90 + i % 120, 190, 255)
            px, py = round(x), round(y)
            draw.rectangle((px, py, px + 1, py + 1), fill=color)
        return frame

    def _waves(self, size: int) -> Image.Image:
        next_wave = (
            np.roll(self.wave, 1, 0)
            + np.roll(self.wave, -1, 0)
            + np.roll(self.wave, 1, 1)
            + np.roll(self.wave, -1, 1)
        ) * 0.5 - self.previous
        next_wave *= 0.985
        next_wave[[0, -1], :] = 0
        next_wave[:, [0, -1]] = 0
        self.previous, self.wave = self.wave, next_wave
        if self.rng.random() < 0.12:
            x, y = self.rng.integers(8, 56, 2)
            self.wave[y, x] = 130
        value = np.clip(np.abs(self.wave) * 4, 0, 255).astype(np.uint8)
        rgb = np.zeros((64, 64, 3), dtype=np.uint8)
        rgb[:, :, 0] = value // 5
        rgb[:, :, 1] = value // 2
        rgb[:, :, 2] = value
        return Image.fromarray(rgb, mode="RGB").resize((size, size))
