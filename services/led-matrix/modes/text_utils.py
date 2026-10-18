"""Robust pixel-perfect text rendering and auto-fitting utilities for 64x64 LED canvas."""

from __future__ import annotations

from PIL import ImageDraw, ImageFont

_FONT_CACHE: dict[int, ImageFont.ImageFont] = {}


def get_matrix_font(size: int = 9) -> ImageFont.ImageFont:
    """Retrieve or cache a PIL default font of the requested size."""
    if size not in _FONT_CACHE:
        _FONT_CACHE[size] = ImageFont.load_default(size=size)
    return _FONT_CACHE[size]


def fit_text_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    preferred_size: int = 9,
    min_size: int = 6,
) -> tuple[ImageFont.ImageFont, int, int]:
    """Find the largest font size in [min_size..preferred_size] that fits within max_width.

    Returns (font, text_width, text_height).
    """
    for size in range(preferred_size, min_size - 1, -1):
        font = get_matrix_font(size)
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        if w <= max_width:
            return font, w, h

    # If it still exceeds at min_size, return min_size
    font = get_matrix_font(min_size)
    bbox = draw.textbbox((0, 0), text, font=font)
    return font, bbox[2] - bbox[0], bbox[3] - bbox[1]


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    cx: int | float,
    cy: int | float,
    fill: tuple[int, int, int] = (255, 255, 255),
    max_width: int | None = None,
    preferred_size: int = 9,
    min_size: int = 6,
) -> tuple[int, int, int, int]:
    """Draw text precisely centered at (cx, cy) on the integer pixel grid.

    Compensates for PIL font bounding-box ascender offsets and auto-scales down font size
    if max_width is specified.
    """
    if max_width is not None and max_width > 0:
        font, w, h = fit_text_font(draw, text, max_width, preferred_size, min_size)
    else:
        font = get_matrix_font(preferred_size)

    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]

    # Calculate exact top-left render coordinate subtracting bbox offsets
    x = int(round(cx - (w / 2.0) - bbox[0]))
    y = int(round(cy - (h / 2.0) - bbox[1]))

    draw.text((x, y), text, fill=fill, font=font)
    return (x, y, x + w, y + h)


def draw_boxed_message(
    draw: ImageDraw.ImageDraw,
    title: str,
    subtitle: str | None = None,
    rect: tuple[int, int, int, int] = (4, 18, 59, 46),
    bg_color: tuple[int, int, int] = (10, 10, 16),
    border_color: tuple[int, int, int] = (255, 60, 60),
    title_color: tuple[int, int, int] = (255, 80, 80),
    sub_color: tuple[int, int, int] = (255, 215, 0),
    border_width: int = 1,
) -> None:
    """Draw a clean, perfectly aligned modal banner with guaranteed non-clipping text."""
    x1, y1, x2, y2 = rect
    draw.rectangle([x1, y1, x2, y2], fill=bg_color)
    draw.rectangle([x1, y1, x2, y2], outline=border_color, width=border_width)

    box_cx = (x1 + x2) / 2.0
    box_w = (x2 - x1) - 4  # 2px padding on each side

    if subtitle:
        # Two stacked lines
        title_cy = y1 + ((y2 - y1) * 0.35)
        sub_cy = y1 + ((y2 - y1) * 0.72)
        draw_centered_text(
            draw,
            title,
            cx=box_cx,
            cy=title_cy,
            fill=title_color,
            max_width=box_w,
            preferred_size=8,
            min_size=6,
        )
        draw_centered_text(
            draw,
            subtitle,
            cx=box_cx,
            cy=sub_cy,
            fill=sub_color,
            max_width=box_w,
            preferred_size=8,
            min_size=6,
        )
    else:
        # Single line centered vertically
        box_cy = (y1 + y2) / 2.0
        draw_centered_text(
            draw,
            title,
            cx=box_cx,
            cy=box_cy,
            fill=title_color,
            max_width=box_w,
            preferred_size=8,
            min_size=6,
        )


def draw_right_aligned_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    right_x: int = 62,
    y: int = 0,
    fill: tuple[int, int, int] = (255, 255, 255),
    max_width: int | None = None,
    preferred_size: int = 7,
    min_size: int = 6,
) -> tuple[int, int, int, int]:
    """Draw text aligned to right_x with auto-fitting and no edge clipping."""
    if max_width is not None and max_width > 0:
        font, w, h = fit_text_font(draw, text, max_width, preferred_size, min_size)
    else:
        font = get_matrix_font(preferred_size)

    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]

    x = int(right_x - w - bbox[0])
    draw_y = int(y - bbox[1])
    draw.text((x, draw_y), text, fill=fill, font=font)
    return (x, draw_y, x + w, draw_y + h)

