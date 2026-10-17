"""Standalone Conway's Game of Life renderer with phosphor decay trails and rotating palettes."""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass

import numpy as np
from PIL import Image

from modes.base import ModeRenderer, RenderContext


@dataclass(frozen=True)
class LifePalette:
    name: str
    bg: tuple[int, int, int]
    decay1: tuple[int, int, int]
    decay2: tuple[int, int, int]
    decay3: tuple[int, int, int]
    alive: tuple[int, int, int]


DEFAULT_PALETTES: tuple[LifePalette, ...] = (
    # 1. Cyberpunk Emerald & Violet (original classic)
    LifePalette(
        name="Cyberpunk",
        bg=(3, 5, 10),
        decay1=(15, 10, 50),
        decay2=(40, 20, 110),
        decay3=(120, 30, 180),
        alive=(0, 240, 140),
    ),
    # 2. Amber CRT Phosphor (warm retro arcade)
    LifePalette(
        name="Amber CRT",
        bg=(8, 4, 1),
        decay1=(40, 16, 2),
        decay2=(110, 50, 5),
        decay3=(190, 100, 10),
        alive=(255, 190, 30),
    ),
    # 3. Matrix Terminal (classic green phosphor)
    LifePalette(
        name="Matrix Terminal",
        bg=(2, 6, 2),
        decay1=(5, 35, 10),
        decay2=(12, 95, 25),
        decay3=(25, 165, 45),
        alive=(60, 255, 90),
    ),
    # 4. Synthwave Sunset (hot magenta/purple to warm sun orange)
    LifePalette(
        name="Synthwave",
        bg=(8, 2, 10),
        decay1=(45, 10, 50),
        decay2=(110, 15, 75),
        decay3=(200, 30, 80),
        alive=(255, 140, 40),
    ),
    # 5. Bioluminescent Abyss (deep ocean blue to electric cyan)
    LifePalette(
        name="Bioluminescent Abyss",
        bg=(2, 6, 12),
        decay1=(5, 25, 60),
        decay2=(10, 70, 130),
        decay3=(20, 140, 200),
        alive=(60, 245, 255),
    ),
    # 6. Solar Flare (crimson plasma to vibrant gold)
    LifePalette(
        name="Solar Flare",
        bg=(8, 2, 4),
        decay1=(45, 8, 20),
        decay2=(110, 15, 30),
        decay3=(190, 35, 30),
        alive=(255, 215, 50),
    ),
    # 7. Arctic Aurora (twilight indigo through teal to mint)
    LifePalette(
        name="Arctic Aurora",
        bg=(4, 3, 10),
        decay1=(25, 12, 50),
        decay2=(25, 65, 85),
        decay3=(35, 145, 125),
        alive=(130, 255, 205),
    ),
    # 8. Neon Dream (midnight violet to pastel pink)
    LifePalette(
        name="Neon Dream",
        bg=(6, 2, 8),
        decay1=(25, 15, 60),
        decay2=(65, 30, 110),
        decay3=(140, 45, 140),
        alive=(255, 105, 180),
    ),
)


class LifeRenderer(ModeRenderer):
    """Hypnotic Conway's Game of Life with phosphor decay trails and rotating palettes on 64x64."""

    def __init__(
        self,
        size: int = 64,
        palette_interval: float = 120.0,
        palettes: list[LifePalette] | tuple[LifePalette, ...] | None = None,
    ) -> None:
        self.size = size
        self.grid = np.zeros((size, size), dtype=bool)
        self.decay = np.zeros((size, size), dtype=np.int8)  # 0..3 decay trail
        self.step_timer = 0.0
        self.step_interval = 0.09  # ~11 gen/sec
        self.palette_timer = 0.0
        self.palette_interval = palette_interval
        self.palettes = list(palettes if palettes is not None else DEFAULT_PALETTES)
        self.palette_idx = 0
        self.stagnant_history: deque[int] = deque(maxlen=24)
        self.reset()

    @property
    def current_palette(self) -> LifePalette:
        return self.palettes[self.palette_idx]

    def set_palette(self, index_or_name: int | str) -> None:
        """Manually select a palette by index or name and reset the timer."""
        if not self.palettes:
            return
        if isinstance(index_or_name, int):
            self.palette_idx = index_or_name % len(self.palettes)
            self.palette_timer = 0.0
        else:
            name_lower = index_or_name.lower()
            for idx, pal in enumerate(self.palettes):
                if pal.name.lower() == name_lower:
                    self.palette_idx = idx
                    self.palette_timer = 0.0
                    return
            raise ValueError(f"Unknown palette name: {index_or_name}")

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
        if self.palette_interval > 0 and len(self.palettes) > 1:
            self.palette_timer += dt
            if self.palette_timer >= self.palette_interval:
                steps = int(self.palette_timer // self.palette_interval)
                self.palette_timer %= self.palette_interval
                self.palette_idx = (self.palette_idx + steps) % len(self.palettes)

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
        palette = self.current_palette

        # Background
        img_array[:, :] = palette.bg

        # Phosphor decay trails:
        # Trail 1: oldest decay
        img_array[self.decay == 1] = palette.decay1
        # Trail 2: mid decay
        img_array[self.decay == 2] = palette.decay2
        # Trail 3: freshest decay
        img_array[self.decay == 3] = palette.decay3

        # Living cells
        img_array[self.grid] = palette.alive

        return Image.fromarray(img_array, mode="RGB")
