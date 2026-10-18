"""Carousel Game Menu renderer for 64x64 LED Matrix."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from PIL import ImageDraw, ImageFont

if TYPE_CHECKING:
    from web_controller.server import MultiplayerHub

GAME_ITEMS = [
    {
        "id": "pong",
        "title": "PONG 2P",
        "short_title": "PONG",
        "badge": "1P / 2P",
        "color": (0, 229, 255),
    },
    {
        "id": "tron",
        "title": "TRON CYCLES",
        "short_title": "TRON",
        "badge": "1P / 2P",
        "color": (0, 229, 255),
    },
    {
        "id": "snake",
        "title": "SNAKE",
        "short_title": "SNAKE",
        "badge": "1P SOLO",
        "color": (40, 220, 90),
    },
    {
        "id": "breakout",
        "title": "BREAKOUT",
        "short_title": "BREAK",
        "badge": "1P SOLO",
        "color": (255, 130, 20),
    },
    {
        "id": "tetris",
        "title": "TETRIS",
        "short_title": "TETRIS",
        "badge": "1P SOLO",
        "color": (180, 90, 230),
    },
]

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
        target_x = float(self.hub.selected_game_index * CARD_SPACING)
        # Smooth spring/lerp transition
        self.scroll_x += (target_x - self.scroll_x) * min(1.0, dt * 14.0)

    def render(self, draw: ImageDraw.ImageDraw) -> None:
        # 1. Dark arcade cabinet background
        draw.rectangle([0, 0, 63, 63], fill=(6, 8, 16))

        # 2. Top Header Bar (y=0..9)
        draw.rectangle([0, 0, 63, 9], fill=(12, 16, 28))
        draw.line([(0, 9), (63, 9)], fill=(40, 55, 85))

        # Title: "SELECT GAME"
        pulse = int(190 + 55 * math.sin(self.anim_timer * 4.0))
        draw.text((6, 0), "SELECT GAME", fill=(pulse, pulse, 255), font=self.font)

        # 3. Side Navigation Arrows
        arrow_pulse = int(140 + 90 * math.sin(self.anim_timer * 6.0))
        arrow_col = (0, arrow_pulse, arrow_pulse)

        # Left arrow ◀ (x=2..4, y=26..30)
        draw.polygon([(4, 26), (1, 28), (4, 30)], fill=arrow_col)

        # Right arrow ▶ (x=59..62, y=26..30)
        draw.polygon([(59, 26), (62, 28), (59, 30)], fill=arrow_col)

        # 4. Draw Carousel Cards
        center_screen_x = 32
        for idx, item in enumerate(GAME_ITEMS):
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

            # Draw Game Icon centered at (card_cx, 24)
            icon_y = 23
            if item["id"] == "pong":
                self._draw_pong_icon(draw, card_cx, icon_y)
            elif item["id"] == "tron":
                self._draw_tron_icon(draw, card_cx, icon_y)
            elif item["id"] == "snake":
                self._draw_snake_icon(draw, card_cx, icon_y)
            elif item["id"] == "breakout":
                self._draw_breakout_icon(draw, card_cx, icon_y)
            elif item["id"] == "tetris":
                self._draw_tetris_icon(draw, card_cx, icon_y)

            # Short title text below icon
            title = item["short_title"]
            text_x = card_cx - len(title) * 2 - 2
            text_col = (255, 255, 255) if is_selected else (100, 115, 140)
            draw.text((text_x, 35), title, fill=text_col, font=self.font)

        # 5. Carousel Position Dots at y=48
        dot_start_x = 32 - (len(GAME_ITEMS) * 4) // 2
        for i in range(len(GAME_ITEMS)):
            dx = dot_start_x + i * 4
            if i == self.hub.selected_game_index:
                draw.rectangle([dx, 48, dx + 1, 49], fill=(0, 229, 255))
            else:
                draw.point((dx, 48), fill=(40, 50, 75))

        # 6. Mode Badge at y=53
        curr_item = GAME_ITEMS[self.hub.selected_game_index]
        badge_text = curr_item["badge"]
        badge_x = 32 - len(badge_text) * 2 - 2
        draw.text((badge_x, 52), badge_text, fill=curr_item["color"], font=self.font)

        # 7. Bottom connection indicators (y=62..63)
        draw.rectangle(
            [0, 62, 30, 63],
            fill=(0, 229, 255) if self.hub.p1_connected else (20, 30, 45),
        )
        draw.rectangle(
            [33, 62, 63, 63],
            fill=(255, 145, 0) if self.hub.p2_connected else (45, 30, 20),
        )

    def _draw_pong_icon(self, draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        # Dotted center line
        for dy in (-6, -3, 0, 3, 6):
            draw.point((cx, cy + dy), fill=(35, 45, 65))
        # Left paddle (Cyan)
        draw.rectangle([cx - 8, cy - 5, cx - 7, cy + 5], fill=(0, 229, 255))
        # Right paddle (Orange)
        draw.rectangle([cx + 6, cy - 3, cx + 7, cy + 5], fill=(255, 145, 0))
        # Ball (White)
        draw.rectangle([cx - 1, cy - 2, cx, cy - 1], fill=(255, 255, 255))

    def _draw_tron_icon(self, draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        # Cyan Light Cycle
        draw.line([(cx - 7, cy + 5), (cx - 7, cy - 3), (cx + 1, cy - 3)], fill=(0, 190, 230))
        draw.point((cx + 1, cy - 3), fill=(255, 255, 255))
        # Orange Light Cycle
        draw.line([(cx + 7, cy - 5), (cx + 7, cy + 3), (cx - 2, cy + 3)], fill=(220, 110, 0))
        draw.point((cx - 2, cy + 3), fill=(255, 180, 50))

    def _draw_snake_icon(self, draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        # Winding green snake
        segments = [
            (cx - 6, cy + 4), (cx - 4, cy + 4), (cx - 2, cy + 4),
            (cx - 2, cy + 2), (cx - 2, cy), (cx, cy), (cx + 2, cy),
            (cx + 2, cy - 2), (cx + 4, cy - 2),
        ]
        for sx, sy in segments:
            draw.rectangle([sx, sy, sx + 1, sy + 1], fill=(40, 220, 90))
        # Glowing head
        draw.rectangle([cx + 4, cy - 2, cx + 5, cy - 1], fill=(255, 255, 255))
        # Red apple
        draw.rectangle([cx - 6, cy - 2, cx - 5, cy - 1], fill=(255, 50, 40))
        draw.point((cx - 6, cy - 3), fill=(60, 200, 60))

    def _draw_breakout_icon(self, draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        # Mini bricks
        draw.rectangle([cx - 8, cy - 6, cx - 3, cy - 5], fill=(255, 40, 50))
        draw.rectangle([cx - 2, cy - 6, cx + 3, cy - 5], fill=(255, 130, 20))
        draw.rectangle([cx + 4, cy - 6, cx + 8, cy - 5], fill=(255, 215, 30))

        draw.rectangle([cx - 8, cy - 3, cx - 3, cy - 2], fill=(40, 220, 90))
        draw.rectangle([cx - 2, cy - 3, cx + 3, cy - 2], fill=(30, 190, 255))
        draw.rectangle([cx + 4, cy - 3, cx + 8, cy - 2], fill=(220, 50, 200))

        # Ball
        draw.point((cx + 1, cy + 1), fill=(255, 255, 255))
        # Paddle
        draw.rectangle([cx - 5, cy + 5, cx + 3, cy + 6], fill=(0, 229, 255))

    def _draw_tetris_icon(self, draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        # Magenta T-tetromino
        draw.rectangle([cx - 5, cy - 4, cx - 3, cy - 2], fill=(180, 90, 230))
        draw.rectangle([cx - 2, cy - 4, cx, cy - 2], fill=(180, 90, 230))
        draw.rectangle([cx + 1, cy - 4, cx + 3, cy - 2], fill=(180, 90, 230))
        draw.rectangle([cx - 2, cy - 1, cx, cy + 1], fill=(180, 90, 230))

        # Cyan I-tetromino (horizontal at bottom)
        draw.rectangle([cx - 7, cy + 3, cx - 5, cy + 5], fill=(60, 220, 230))
        draw.rectangle([cx - 4, cy + 3, cx - 2, cy + 5], fill=(60, 220, 230))
        draw.rectangle([cx - 1, cy + 3, cx + 1, cy + 5], fill=(60, 220, 230))
        draw.rectangle([cx + 2, cy + 3, cx + 4, cy + 5], fill=(60, 220, 230))
