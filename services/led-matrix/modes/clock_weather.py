"""Clock and Weather mode renderer."""

from __future__ import annotations

import time
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from modes.base import ModeRenderer, RenderContext


class ClockWeatherRenderer(ModeRenderer):
    def __init__(self) -> None:
        self.font_time = ImageFont.load_default(size=18)
        self.font_small = ImageFont.load_default(size=9)
        self.font_temp = ImageFont.load_default(size=13)

    def render(self, ctx: RenderContext, size: int = 64) -> Image.Image:
        frame = Image.new("RGB", (size, size), (6, 8, 14))
        draw = ImageDraw.Draw(frame)

        now = datetime.now()

        # 1. Digital Time (12-hour format with blinking colon)
        hour = now.strftime("%I").lstrip("0") or "12"
        minute = now.strftime("%M")
        colon = ":" if (int(time.monotonic() * 2) % 2 == 0) else " "
        time_str = f"{hour}{colon}{minute}"

        bbox_time = draw.textbbox((0, 0), time_str, font=self.font_time)
        w_time = bbox_time[2] - bbox_time[0]
        draw.text(
            ((size - w_time) // 2, 3),
            time_str,
            fill=(255, 255, 255),
            font=self.font_time,
        )

        # 2. Date (e.g. FRI OCT 16)
        date_str = now.strftime("%a %b %d").upper()
        bbox_date = draw.textbbox((0, 0), date_str, font=self.font_small)
        w_date = bbox_date[2] - bbox_date[0]
        draw.text(
            ((size - w_date) // 2, 23),
            date_str,
            fill=(110, 190, 255),
            font=self.font_small,
        )

        # 3. Divider
        draw.line([(6, 35), (size - 7, 35)], fill=(30, 42, 60), width=1)

        # 4. Weather Icon and Temperature
        temp_val = ctx.ha_state.weather_temp
        temp_str = f"{round(temp_val)}°" if temp_val is not None else "--°"

        # Draw Weather Icon on left, Temperature on right
        self._draw_weather_icon(draw, ctx.ha_state.weather_condition, x=10, y=41)

        # Temperature text
        draw.text((32, 42), temp_str, fill=(255, 195, 60), font=self.font_temp)

        return frame

    def _draw_weather_icon(
        self, draw: ImageDraw.ImageDraw, condition: str, x: int, y: int
    ) -> None:
        """Draw a pixel-art weather icon within ~18x18 bounding box at (x, y)."""
        cond = (condition or "").lower().replace("-", "").replace("_", "")

        if "rain" in cond or "pour" in cond or "drizzle" in cond:
            # Cloud with rain drops
            draw.ellipse((x + 2, y + 2, x + 14, y + 10), fill=(140, 160, 180))
            draw.line([(x + 4, y + 12), (x + 3, y + 15)], fill=(80, 160, 255), width=1)
            draw.line([(x + 8, y + 12), (x + 7, y + 15)], fill=(80, 160, 255), width=1)
            draw.line(
                [(x + 12, y + 12), (x + 11, y + 15)], fill=(80, 160, 255), width=1
            )
        elif "snow" in cond or "hail" in cond:
            # Cloud with snowflakes
            draw.ellipse((x + 2, y + 2, x + 14, y + 10), fill=(160, 180, 200))
            draw.point(
                [(x + 5, y + 13), (x + 9, y + 14), (x + 13, y + 12)],
                fill=(255, 255, 255),
            )
        elif "thunder" in cond or "lightning" in cond:
            # Dark cloud with yellow lightning bolt
            draw.ellipse((x + 2, y + 1, x + 14, y + 9), fill=(100, 110, 130))
            draw.polygon(
                [(x + 8, y + 9), (x + 5, y + 13), (x + 8, y + 13), (x + 6, y + 17)],
                fill=(255, 220, 0),
            )
        elif "cloud" in cond:
            # Puffy clouds
            draw.ellipse((x + 1, y + 5, x + 11, y + 13), fill=(140, 155, 175))
            draw.ellipse((x + 6, y + 3, x + 16, y + 13), fill=(170, 185, 205))
        elif "night" in cond or "moon" in cond:
            # Crescent moon with tiny stars
            draw.ellipse((x + 3, y + 2, x + 13, y + 12), fill=(255, 230, 130))
            draw.ellipse(
                (x + 6, y + 1, x + 15, y + 10), fill=(6, 8, 14)
            )  # Cut out crescent
            draw.point([(x + 14, y + 4), (x + 3, y + 14)], fill=(255, 255, 255))
        else:
            # Sunny / Clear: Bright sun with orange rays
            draw.ellipse((x + 4, y + 4, x + 12, y + 12), fill=(255, 170, 0))
            # Ray points
            draw.point(
                [(x + 8, y + 1), (x + 8, y + 15), (x + 1, y + 8), (x + 15, y + 8)],
                fill=(255, 210, 50),
            )
            draw.point(
                [(x + 3, y + 3), (x + 13, y + 13), (x + 3, y + 13), (x + 13, y + 3)],
                fill=(255, 210, 50),
            )
