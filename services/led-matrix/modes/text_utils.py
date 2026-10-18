"""Fit and align text inside explicit pixel bounds on the matrix."""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

Color = tuple[int, int, int]
Bounds = tuple[int, int, int, int]
Font = ImageFont.ImageFont | ImageFont.FreeTypeFont


@lru_cache(maxsize=16)
def get_matrix_font(size: int = 9) -> Font:
    return ImageFont.load_default(size=size)


def _font_bounds(font: Font, text: str) -> Bounds:
    left, top, right, bottom = font.getbbox(text)
    return math.floor(left), math.floor(top), math.ceil(right), math.ceil(bottom)


@lru_cache(maxsize=512)
def _fit_text(
    text: str, width: int, height: int, preferred_size: int, min_size: int
) -> tuple[str, Font, Bounds]:
    text = " ".join(text.split())
    for size in range(preferred_size, min_size - 1, -1):
        font = get_matrix_font(size)
        bounds = _font_bounds(font, text)
        if bounds[2] - bounds[0] <= width and bounds[3] - bounds[1] <= height:
            return text, font, bounds
    font = get_matrix_font(min_size)
    marker = "..."
    while marker and font.getlength(marker) > width:
        marker = marker[:-1]
    while text:
        candidate = text.rstrip() + marker
        bounds = _font_bounds(font, candidate)
        if bounds[2] - bounds[0] <= width and bounds[3] - bounds[1] <= height:
            return candidate, font, bounds
        text = text[:-1]
    bounds = _font_bounds(font, marker)
    if bounds[3] - bounds[1] <= height:
        return marker, font, bounds
    return "", font, (0, 0, 0, 0)


def draw_fitted_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    rect: Bounds,
    fill: Color = (255, 255, 255),
    *,
    align: Literal["left", "center", "right"] = "center",
    preferred_size: int = 9,
    min_size: int = 6,
) -> Bounds:
    """Draw within a half-open rectangle, returning the actual visible ink bounds."""
    left, top, right, bottom = rect
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0 or not text:
        return left, top, left, top
    if not 1 <= min_size <= preferred_size:
        raise ValueError("Font sizes must satisfy 1 <= min_size <= preferred_size")
    if align not in {"left", "center", "right"}:
        raise ValueError("Unknown text alignment")
    text, font, bounds = _fit_text(text, width, height, preferred_size, min_size)
    ink_width, ink_height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    glyph = Image.new("L", (max(1, ink_width), max(1, ink_height)))
    ImageDraw.Draw(glyph).text((-bounds[0], -bounds[1]), text, font=font, fill=255)
    ink = glyph.getbbox()
    if ink is None:
        return left, top, left, top
    glyph = glyph.crop(ink)
    ink_width, ink_height = glyph.size
    x = {"left": 0, "center": (width - ink_width) // 2, "right": width - ink_width}[
        align
    ]
    y = (height - ink_height) // 2
    mask = Image.new("L", (width, height))
    mask.paste(glyph, (x, y))
    visible = mask.getbbox()
    draw.bitmap((left, top), mask, fill=fill)
    if visible is None:
        return left, top, left, top
    return left + visible[0], top + visible[1], left + visible[2], top + visible[3]


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    cx: int | float,
    cy: int | float,
    fill: Color = (255, 255, 255),
    max_width: int | None = None,
    preferred_size: int = 9,
    min_size: int = 6,
    max_height: int = 12,
) -> Bounds:
    width = max_width if max_width is not None else 64
    left, top = round(cx - width / 2), round(cy - max_height / 2)
    return draw_fitted_text(
        draw,
        text,
        (left, top, left + width, top + max_height),
        fill,
        preferred_size=preferred_size,
        min_size=min_size,
    )


def draw_right_aligned_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    right_x: int = 62,
    y: int = 0,
    fill: Color = (255, 255, 255),
    max_width: int | None = None,
    preferred_size: int = 7,
    min_size: int = 6,
    max_height: int = 8,
) -> Bounds:
    width = max_width if max_width is not None else right_x
    return draw_fitted_text(
        draw,
        text,
        (right_x - width, y, right_x, y + max_height),
        fill,
        align="right",
        preferred_size=preferred_size,
        min_size=min_size,
    )


def draw_scoreboard(
    draw: ImageDraw.ImageDraw,
    left: str,
    right: str = "",
    *,
    size: int = 64,
    left_color: Color = (210, 225, 245),
    right_color: Color = (255, 205, 90),
) -> None:
    right_width = min(
        size // 2 + 6, max(16, round(get_matrix_font(7).getlength(right)) + 1)
    )
    split = size - 2 - right_width
    draw_fitted_text(
        draw,
        left,
        (2, 1, split - 4 if right else size - 2, 9),
        left_color,
        align="left",
        preferred_size=8,
    )
    if right:
        draw_fitted_text(
            draw,
            right,
            (split, 1, size - 2, 9),
            right_color,
            align="right",
            preferred_size=8,
        )


def draw_boxed_message(
    draw: ImageDraw.ImageDraw,
    title: str,
    subtitle: str | None = None,
    rect: Bounds = (4, 18, 59, 46),
    bg_color: Color = (10, 10, 16),
    border_color: Color = (255, 60, 60),
    title_color: Color = (255, 80, 80),
    sub_color: Color = (255, 215, 0),
    border_width: int = 1,
) -> None:
    """Draw an inclusive border and reserve separate bounded text rows inside it."""
    x1, y1, x2, y2 = rect
    draw.rectangle(rect, fill=bg_color, outline=border_color, width=border_width)
    inset = border_width + 2
    inner = (x1 + inset, y1 + inset, x2 - inset + 1, y2 - inset + 1)
    if subtitle:
        middle = (inner[1] + inner[3]) // 2
        title_rect = (inner[0], inner[1], inner[2], middle - 1)
        sub_rect = (inner[0], middle + 1, inner[2], inner[3])
        draw_fitted_text(draw, title, title_rect, title_color, preferred_size=8)
        draw_fitted_text(draw, subtitle, sub_rect, sub_color, preferred_size=8)
    else:
        draw_fitted_text(draw, title, inner, title_color, preferred_size=8)
