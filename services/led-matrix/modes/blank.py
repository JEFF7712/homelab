"""Blank mode renderer (display turned off / sleep)."""

from __future__ import annotations

from PIL import Image

from modes.base import ModeRenderer, RenderContext


class BlankRenderer(ModeRenderer):
    def __init__(self) -> None:
        self._blank_cache: dict[int, Image.Image] = {}

    def render(self, ctx: RenderContext, size: int = 64) -> Image.Image:
        if size not in self._blank_cache:
            self._blank_cache[size] = Image.new("RGB", (size, size), (0, 0, 0))
        return self._blank_cache[size]
