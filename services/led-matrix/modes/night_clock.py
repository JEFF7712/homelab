"""Night Clock mode renderer (minimal, low-glow red/amber clock for sleeping)."""

from __future__ import annotations

import time
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from modes.base import ModeRenderer, RenderContext


class NightClockRenderer(ModeRenderer):
    def __init__(self) -> None:
        self.font_time = ImageFont.load_default(size=20)
        self.font_small = ImageFont.load_default(size=9)

    def render(self, ctx: RenderContext, size: int = 64) -> Image.Image:
        # Pure black canvas
        frame = Image.new("RGB", (size, size), (0, 0, 0))
        draw = ImageDraw.Draw(frame)

        now = datetime.now()

        # 12-hour format with dim colon
        hour = now.strftime("%I").lstrip("0") or "12"
        minute = now.strftime("%M")
        colon = ":" if (int(time.monotonic() * 2) % 2 == 0) else " "
        time_str = f"{hour}{colon}{minute}"

        # Deep warm red/crimson - zero blue light for sleep
        glow_color = (165, 24, 24)
        dim_color = (100, 16, 16)

        bbox_time = draw.textbbox((0, 0), time_str, font=self.font_time)
        w_time = bbox_time[2] - bbox_time[0]
        draw.text(
            ((size - w_time) // 2, 16), time_str, fill=glow_color, font=self.font_time
        )

        # Subtle date
        date_str = now.strftime("%a %b %d").upper()
        bbox_date = draw.textbbox((0, 0), date_str, font=self.font_small)
        w_date = bbox_date[2] - bbox_date[0]
        draw.text(
            ((size - w_date) // 2, 42), date_str, fill=dim_color, font=self.font_small
        )

        return frame
