"""Multiplayer Lobby and QR Code renderer for 64x64 LED Matrix."""

import math
import random
from typing import List, Tuple
from PIL import ImageDraw

from web_controller.server import MultiplayerHub

# Fixed 25x25 QR Code matrix for "https://arcade.rupan.dev"
QR_ROWS_25x25 = [
    "1111111011100101101111111",
    "1000001000001110101000001",
    "1011101001100001001011101",
    "1011101011011111001011101",
    "1011101011111111101011101",
    "1000001010101110001000001",
    "1111111010101010101111111",
    "0000000011000010100000000",
    "1000101111010000111111001",
    "0011010001100011110011010",
    "0000001111101111110101100",
    "0011100100001000011000110",
    "1110011001110011011101111",
    "1111010101001011000010010",
    "0000101110111111010111100",
    "0000110010010001001110110",
    "1110101100101000111111100",
    "0000000010000110100010000",
    "1111111011000010101010000",
    "1000001001101000100011110",
    "1011101011111000111111100",
    "1011101001001110011100111",
    "1011101000110010111001010",
    "1000001001101011010111110",
    "1111111010100001011000111",
]


class LobbyRenderer:
    """Renders QR code, connection status, countdowns, and gameover states."""

    def __init__(self, hub: MultiplayerHub) -> None:
        self.hub = hub
        self.blink_timer = 0.0
        self.particles: List[Tuple[float, float, float, float, Tuple[int, int, int], float]] = []

    def update(self, dt: float) -> None:
        self.blink_timer = (self.blink_timer + dt) % 2.0

        # Update sparkles
        new_p = []
        for px, py, pvx, pvy, col, life in self.particles:
            life -= dt
            if life > 0:
                new_p.append((px + pvx * dt, py + pvy * dt, pvx, pvy, col, life))
        self.particles = new_p

    def render_lobby(self, draw: ImageDraw.ImageDraw) -> None:
        """Render high-contrast scannable QR code and player indicators."""
        # 1. White quiet zone rectangle in center
        draw.rectangle([4, 4, 59, 59], fill=(255, 255, 255))

        # 2. Draw 25x25 QR modules scaled 2x centered at (7, 7)
        for r, row in enumerate(QR_ROWS_25x25):
            for c, char in enumerate(row):
                if char == "1":
                    x = 7 + c * 2
                    y = 7 + r * 2
                    draw.rectangle([x, y, x + 1, y + 1], fill=(0, 0, 0))

        # 3. Top bar (y=0..2)
        # Player 1 indicator (Left)
        if self.hub.p1_connected:
            draw.rectangle([0, 0, 20, 2], fill=(0, 229, 255))
        else:
            # Pulse cyan
            pulse = int(120 + 100 * math.sin(self.blink_timer * 6.0))
            draw.rectangle([0, 0, 20, 2], fill=(0, pulse // 2, pulse))

        # Player 2 indicator (Right)
        if self.hub.p2_connected:
            draw.rectangle([43, 0, 63, 2], fill=(255, 145, 0))
        else:
            pulse = int(120 + 100 * math.sin(self.blink_timer * 6.0 + 3.14))
            draw.rectangle([43, 0, 63, 2], fill=(pulse, pulse // 2, 0))

        # Center top dot
        draw.point((31, 1), fill=(180, 180, 255))
        draw.point((32, 1), fill=(180, 180, 255))

        # 4. Bottom bar (y=61..63)
        draw.rectangle([0, 61, 31, 63], fill=(0, 180, 220) if self.hub.p1_connected else (20, 30, 45))
        draw.rectangle([32, 61, 63, 63], fill=(220, 110, 0) if self.hub.p2_connected else (45, 30, 20))

    def render_countdown(self, draw: ImageDraw.ImageDraw, count: int) -> None:
        """Render animated countdown before game launch."""
        # Dark vignette background
        draw.rectangle([0, 0, 63, 63], fill=(5, 5, 12))

        # Big countdown number
        color = (0, 229, 255) if count % 2 == 1 else (255, 145, 0)
        num_str = str(count) if count > 0 else "GO!"

        # Draw centered box
        draw.rectangle([12, 16, 51, 47], outline=color, width=2)
        # Center text representation
        if count == 3:
            self._draw_big_3(draw, 24, 22, color)
        elif count == 2:
            self._draw_big_2(draw, 24, 22, color)
        elif count == 1:
            self._draw_big_1(draw, 26, 22, color)
        else:
            # "GO"
            self._draw_go(draw, 20, 24, (255, 255, 255))

    def render_game_over(self, draw: ImageDraw.ImageDraw, winner: int) -> None:
        """Render victory screen with winner color banner and celebratory sparks."""
        draw.rectangle([0, 0, 63, 63], fill=(5, 5, 12))

        # Periodically spawn confetti
        if random.random() < 0.4:
            col = (0, 229, 255) if winner == 1 else (255, 145, 0)
            self.particles.append((
                random.uniform(10, 54),
                random.uniform(5, 55),
                random.uniform(-20, 20),
                random.uniform(-20, 20),
                col,
                random.uniform(0.3, 0.8),
            ))

        for px, py, _, _, col, _ in self.particles:
            if 0 <= px < 64 and 0 <= py < 64:
                draw.point((int(px), int(py)), fill=col)

        # Winner Banner
        col = (0, 229, 255) if winner == 1 else (255, 145, 0)
        draw.rectangle([6, 18, 57, 45], fill=(12, 12, 24), outline=col, width=2)

        # Draw "P1 WIN" or "P2 WIN"
        if winner == 1:
            self._draw_p1_win(draw, 14, 26, col)
        else:
            self._draw_p2_win(draw, 14, 26, col)

    def _draw_big_3(self, draw: ImageDraw.ImageDraw, x: int, y: int, col: Tuple[int, int, int]) -> None:
        bitmap = [
            "11111111",
            "00000011",
            "00000011",
            "11111111",
            "00000011",
            "00000011",
            "11111111",
        ]
        self._render_bitmap(draw, bitmap, x, y, col, scale=2)

    def _draw_big_2(self, draw: ImageDraw.ImageDraw, x: int, y: int, col: Tuple[int, int, int]) -> None:
        bitmap = [
            "11111111",
            "00000011",
            "00000011",
            "11111111",
            "11000000",
            "11000000",
            "11111111",
        ]
        self._render_bitmap(draw, bitmap, x, y, col, scale=2)

    def _draw_big_1(self, draw: ImageDraw.ImageDraw, x: int, y: int, col: Tuple[int, int, int]) -> None:
        bitmap = [
            "001100",
            "011100",
            "001100",
            "001100",
            "001100",
            "001100",
            "111111",
        ]
        self._render_bitmap(draw, bitmap, x, y, col, scale=2)

    def _draw_go(self, draw: ImageDraw.ImageDraw, x: int, y: int, col: Tuple[int, int, int]) -> None:
        # G O
        draw.text((x, y), "GO!", fill=col)

    def _draw_p1_win(self, draw: ImageDraw.ImageDraw, x: int, y: int, col: Tuple[int, int, int]) -> None:
        draw.text((x, y), "P1 WINS!", fill=col)

    def _draw_p2_win(self, draw: ImageDraw.ImageDraw, x: int, y: int, col: Tuple[int, int, int]) -> None:
        draw.text((x, y), "P2 WINS!", fill=col)

    def _render_bitmap(self, draw: ImageDraw.ImageDraw, bitmap: list[str], x: int, y: int, col: Tuple[int, int, int], scale: int = 1) -> None:
        for r, row in enumerate(bitmap):
            for c, val in enumerate(row):
                if val == "1":
                    px = x + c * scale
                    py = y + r * scale
                    if scale == 1:
                        draw.point((px, py), fill=col)
                    else:
                        draw.rectangle([px, py, px + scale - 1, py + scale - 1], fill=col)
