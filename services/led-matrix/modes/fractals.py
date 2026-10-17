"""Continuous zoom through three escape-time fractals."""

from __future__ import annotations

import numpy as np
from PIL import Image

from modes.base import ModeRenderer, RenderContext


class FractalRenderer(ModeRenderer):
    PERIOD = 24.0
    SCENES = (
        ("Mandelbrot", -0.743643887, 0.131825904),
        ("Julia", 0.0, 0.0),
        ("Burning Ship", -1.744, -0.017),
    )

    def render(self, ctx: RenderContext, size: int = 64) -> Image.Image:
        scene = int(ctx.tick / self.PERIOD) % len(self.SCENES)
        phase = (ctx.tick % self.PERIOD) / self.PERIOD
        _, cx, cy = self.SCENES[scene]
        scale = 2.7 * 0.025**phase
        axis = np.linspace(-1, 1, size, dtype=np.float64)
        x, y = np.meshgrid(axis * scale + cx, axis * scale + cy)
        point = x + 1j * y
        z = np.zeros_like(point) if scene != 1 else point.copy()
        escaped = np.zeros((size, size), dtype=bool)
        iterations = np.zeros((size, size), dtype=np.float32)
        for i in range(52):
            active = ~escaped
            if not active.any():
                break
            with np.errstate(over="ignore", invalid="ignore"):
                if scene == 2:
                    z[active] = (
                        np.abs(z[active].real) + 1j * np.abs(z[active].imag)
                    ) ** 2 + point[active]
                elif scene == 1:
                    z[active] = z[active] ** 2 + complex(-0.8, 0.156)
                else:
                    z[active] = z[active] ** 2 + point[active]
            newly = active & (np.abs(z) > 16)
            iterations[newly] = i + 1
            escaped |= newly
        hue = (iterations * 0.07 + phase * 0.3) % 1.0
        rgb = np.zeros((size, size, 3), dtype=np.uint8)
        rgb[:, :, 0] = ((np.sin(hue * 6.28) + 1) * 115).astype(np.uint8)
        rgb[:, :, 1] = ((np.sin(hue * 6.28 + 2.1) + 1) * 115).astype(np.uint8)
        rgb[:, :, 2] = ((np.sin(hue * 6.28 + 4.2) + 1) * 115).astype(np.uint8)
        rgb[~escaped] = (1, 2, 8)
        return Image.fromarray(rgb, mode="RGB")
