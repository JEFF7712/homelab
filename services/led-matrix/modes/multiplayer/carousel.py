"""Carousel Game Menu renderer for 64x64 LED Matrix."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from PIL import ImageDraw, ImageFont

from .registry import GAME_REGISTRY

if TYPE_CHECKING:
    from web_controller.server import MultiplayerHub

# Fallback module-level alias for backward compatibility
GAME_ITEMS = GAME_REGISTRY.get_carousel_items()

CARD_SPACING = 38.0


class CarouselRenderer:
    """Renders retro-arcade carousel game selection wheel on 64x64 LED matrix."""

    def __init__(self, hub: MultiplayerHub) -> None:
        self.hub = hub
        self.font = ImageFont.load_default(size=9)
        self.scroll_x = float(self.hub.selected_game_index * CARD_SPACING)
        self.anim_timer = 0.0

    def update(self, dt: float) -> None:
        self.anim_timer += dt
        items = GAME_REGISTRY.get_carousel_items()
        safe_idx = self.hub.selected_game_index % len(items) if items else 0
        target_x = float(safe_idx * CARD_SPACING)
        # Smooth spring/lerp transition
        self.scroll_x += (target_x - self.scroll_x) * min(1.0, dt * 14.0)

    def render(self, draw: ImageDraw.ImageDraw) -> None:
        # 1. Dark arcade cabinet background
        draw.rectangle([0, 0, 63, 63], fill=(6, 8, 16))

        # 2. Side Navigation Arrows
        arrow_pulse = int(140 + 90 * math.sin(self.anim_timer * 6.0))
        arrow_col = (0, arrow_pulse, arrow_pulse)

        # Left arrow ◀ (x=2..4, y=26..30)
        draw.polygon([(4, 26), (1, 28), (4, 30)], fill=arrow_col)

        # Right arrow ▶ (x=59..62, y=26..30)
        draw.polygon([(59, 26), (62, 28), (59, 30)], fill=arrow_col)

        # 4. Draw Carousel Cards
        items = GAME_REGISTRY.get_carousel_items()
        center_screen_x = 32
        for idx, item in enumerate(items):
            card_cx = center_screen_x + int(idx * CARD_SPACING - self.scroll_x)

            # Skip if offscreen
            if card_cx < -20 or card_cx > 84:
                continue

            is_selected = idx == self.hub.selected_game_index

            # Card box: 28px wide, 34px tall (y=12..45)
            w = 14
            x0 = card_cx - w
            x1 = card_cx + w
            y0 = 12
            y1 = 45

            if is_selected:
                glow = int(180 + 75 * math.sin(self.anim_timer * 5.0))
                border_col = (0, glow, 255)
                bg_col = (16, 20, 36)
            else:
                border_col = (30, 38, 56)
                bg_col = (8, 10, 18)

            draw.rectangle([x0, y0, x1, y1], fill=bg_col, outline=border_col)

            # Draw Game Icon centered at (card_cx, 23) via game's registered icon_fn
            icon_y = 23
            icon_fn = item.get("icon_fn")
            if icon_fn:
                icon_fn(draw, card_cx, icon_y)

            # Short title text below icon
            title = item.get("short_title", "")
            text_x = card_cx - len(title) * 2 - 2
            text_col = (255, 255, 255) if is_selected else (100, 115, 140)
            draw.text((text_x, 35), title, fill=text_col, font=self.font)

        # 5. Carousel Position Dots at y=48
        safe_idx = self.hub.selected_game_index % len(items) if items else 0
        dot_start_x = 32 - (len(items) * 4) // 2
        for i in range(len(items)):
            dx = dot_start_x + i * 4
            if i == safe_idx:
                draw.rectangle([dx, 48, dx + 1, 49], fill=(0, 229, 255))
            else:
                draw.point((dx, 48), fill=(40, 50, 75))

        # 6. Mode Badge or High Score at y=52 (Alternating every ~2s)
        if items:
            curr_item = items[safe_idx]
            hi_score = self.hub.get_high_score(curr_item["id"])
            show_hi = hi_score > 0 and (int(self.anim_timer * 0.6) % 2 == 1)

            if show_hi:
                display_text = f"HI: {hi_score}"
                display_col = (255, 215, 0)
            else:
                display_text = curr_item["badge"]
                display_col = curr_item["color"]

            badge_x = 32 - len(display_text) * 2 - 2
            draw.text((badge_x, 52), display_text, fill=display_col, font=self.font)

        # 7. Bottom connection indicators (y=62..63)
        draw.rectangle(
            [0, 62, 30, 63],
            fill=(0, 229, 255) if self.hub.p1_connected else (20, 30, 45),
        )
        draw.rectangle(
            [33, 62, 63, 63],
            fill=(255, 145, 0) if self.hub.p2_connected else (45, 30, 20),
        )
