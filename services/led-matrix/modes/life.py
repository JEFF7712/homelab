"""Standalone Conway's Game of Life renderer with phosphor decay trails."""

from __future__ import annotations

import random
from collections import deque

import numpy as np
from PIL import Image

from modes.base import ModeRenderer, RenderContext


class LifeRenderer(ModeRenderer):
    """Hypnotic Conway's Game of Life with phosphor decay trails on 64x64."""

    def __init__(self, size: int = 64) -> None:
        self.size = size
        self.grid = np.zeros((size, size), dtype=bool)
        self.decay = np.zeros((size, size), dtype=np.int8)  # 0..3 decay trail
        self.step_timer = 0.0
        self.step_interval = 0.09  # ~11 gen/sec
        self.stagnant_history: deque[int] = deque(maxlen=24)
        self.reset()

    def reset(self) -> None:
        self.grid = np.zeros((self.size, self.size), dtype=bool)
        self.decay = np.zeros((self.size, self.size), dtype=np.int8)
        # Seed a rich random 36x36 core
        core = np.random.rand(36, 36) < 0.28
        self.grid[14:50, 14:50] = core
        self.stagnant_history.clear()

    def _inject_glider(self, r: int, c: int) -> None:
        glider = np.array([[0, 1, 0], [0, 0, 1], [1, 1, 1]], dtype=bool)
        r = r % (self.size - 4)
        c = c % (self.size - 4)
        self.grid[r : r + 3, c : c + 3] ^= glider

    def update(self, dt: float) -> None:
        self.step_timer += dt
        if self.step_timer < self.step_interval:
            return
        self.step_timer = 0.0

        # Vectorized Conway's Game of Life
        g = self.grid.astype(int)
        nbrs = (
            np.roll(np.roll(g, 1, 0), 1, 1)
            + np.roll(g, 1, 0)
            + np.roll(np.roll(g, 1, 0), -1, 1)
            + np.roll(g, 1, 1)
            + np.roll(g, -1, 1)
            + np.roll(np.roll(g, -1, 0), 1, 1)
            + np.roll(g, -1, 0)
            + np.roll(np.roll(g, -1, 0), -1, 1)
        )
        new_grid = (nbrs == 3) | (self.grid & (nbrs == 2))

        # Update decay trail: cells that died get max trail (3), living get 0, decaying drop by 1
        died = self.grid & (~new_grid)
        self.decay[died] = 3
        self.decay[new_grid] = 0
        decaying = (~new_grid) & (self.decay > 0) & (~died)
        self.decay[decaying] -= 1

        self.grid = new_grid
        alive_count = int(np.count_nonzero(self.grid))
        self.stagnant_history.append(alive_count)

        # Check stagnation or extinction
        if alive_count < 15:
            self.reset()
        elif len(self.stagnant_history) == 24 and len(set(self.stagnant_history)) <= 2:
            # If all recent alive counts are identical, add gliders to break symmetry
            for _ in range(3):
                self._inject_glider(random.randint(4, 55), random.randint(4, 55))
            self.stagnant_history.clear()

    def render(self, ctx: RenderContext, size: int = 64) -> Image.Image:
        self.update(ctx.dt)

        img_array = np.zeros((self.size, self.size, 3), dtype=np.uint8)

        # Dark background
        img_array[:, :] = [3, 5, 10]

        # Phosphor decay trails:
        # Trail 1: Dark Indigo
        img_array[self.decay == 1] = [15, 10, 50]
        # Trail 2: Deep Blue/Violet
        img_array[self.decay == 2] = [40, 20, 110]
        # Trail 3: Vibrant Violet
        img_array[self.decay == 3] = [120, 30, 180]

        # Living cells: Bright Neon Emerald / Cyan
        img_array[self.grid] = [0, 240, 140]

        return Image.fromarray(img_array, mode="RGB")
