"""Autonomous AI-vs-AI Pong simulation on 64x64 RGB matrix."""

from __future__ import annotations

import random
from collections import deque

from PIL import Image, ImageDraw, ImageFont

from modes.text_utils import draw_fitted_text

from .base import ArcadeGame


class PongGame(ArcadeGame):
    name = "PONG AI"

    def __init__(self) -> None:
        self.font = ImageFont.load_default(size=9)
        self.score_left = 0
        self.score_right = 0
        self.p1_y = 32.0
        self.p2_y = 32.0
        self.paddle_h = 11.0
        self.paddle_w = 2.0
        self.paddle_speed = 46.0

        self.ball_x = 32.0
        self.ball_y = 36.0
        self.ball_vx = 0.0
        self.ball_vy = 0.0
        self.ball_speed = 44.0
        self.trail: deque[tuple[float, float]] = deque(maxlen=4)
        self.serve_timer = 0.5
        self.reset()

    def reset(self) -> None:
        self.score_left = 0
        self.score_right = 0
        self.p1_y = 32.0
        self.p2_y = 32.0
        self._serve(random.choice([-1, 1]))

    def _serve(self, direction: int) -> None:
        self.ball_x = 32.0
        self.ball_y = random.uniform(20.0, 50.0)
        angle = random.uniform(-0.6, 0.6)
        self.ball_vx = direction * self.ball_speed * 0.95
        self.ball_vy = self.ball_speed * angle
        self.trail.clear()
        self.serve_timer = 0.6

    def update(self, dt: float) -> None:
        dt = min(dt, 0.05)  # Cap large delta spikes
        if self.serve_timer > 0:
            self.serve_timer -= dt
            return

        # Record trail
        self.trail.append((self.ball_x, self.ball_y))

        # Ball movement
        self.ball_x += self.ball_vx * dt
        self.ball_y += self.ball_vy * dt

        # Top & bottom bounce (play area y: 12 to 62)
        top_y, bot_y = 12.0, 62.0
        if self.ball_y <= top_y:
            self.ball_y = top_y
            self.ball_vy = abs(self.ball_vy)
        elif self.ball_y >= bot_y:
            self.ball_y = bot_y
            self.ball_vy = -abs(self.ball_vy)

        # AI tracking: Left paddle (x=3)
        p1_target = self.ball_y if self.ball_vx < 0 else 36.0
        if self.p1_y < p1_target - 1.5:
            self.p1_y += self.paddle_speed * dt
        elif self.p1_y > p1_target + 1.5:
            self.p1_y -= self.paddle_speed * dt

        # AI tracking: Right paddle (x=60)
        p2_target = self.ball_y if self.ball_vx > 0 else 36.0
        if self.p2_y < p2_target - 1.5:
            self.p2_y += self.paddle_speed * dt
        elif self.p2_y > p2_target + 1.5:
            self.p2_y -= self.paddle_speed * dt

        # Clamp paddles inside bounds
        half_h = self.paddle_h / 2.0
        self.p1_y = max(top_y + half_h, min(bot_y - half_h, self.p1_y))
        self.p2_y = max(top_y + half_h, min(bot_y - half_h, self.p2_y))

        # Left paddle collision (x=3..4)
        if (
            self.ball_x <= 5.0
            and self.ball_vx < 0
            and abs(self.ball_y - self.p1_y) <= half_h + 1.0
        ):
            self.ball_x = 5.0
            offset = (self.ball_y - self.p1_y) / half_h
            self.ball_vx = abs(self.ball_vx) * 1.05  # slight speedup
            self.ball_vy = offset * self.ball_speed * 1.1

        # Right paddle collision (x=59..60)
        if (
            self.ball_x >= 58.0
            and self.ball_vx > 0
            and abs(self.ball_y - self.p2_y) <= half_h + 1.0
        ):
            self.ball_x = 58.0
            offset = (self.ball_y - self.p2_y) / half_h
            self.ball_vx = -abs(self.ball_vx) * 1.05
            self.ball_vy = offset * self.ball_speed * 1.1

        # Scoring
        if self.ball_x < 0:
            self.score_right += 1
            if self.score_right >= 9:
                self.score_left = self.score_right = 0
            self._serve(1)
        elif self.ball_x > 64:
            self.score_left += 1
            if self.score_left >= 9:
                self.score_left = self.score_right = 0
            self._serve(-1)

    def draw(
        self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64
    ) -> None:
        # Background
        draw.rectangle([(0, 0), (size - 1, size - 1)], fill=(4, 6, 12))

        # Score bar at top
        score_text = f"{self.score_left}   {self.score_right}"
        draw_fitted_text(draw, score_text, (2, 1, size - 2, 9), fill=(200, 220, 240))

        # Upper divider line
        draw.line([(0, 10), (size - 1, 10)], fill=(25, 35, 55), width=1)

        # Center dashed line
        center_x = size // 2
        for y in range(12, size - 2, 4):
            draw.point((center_x, y), fill=(30, 45, 70))

        # Left Paddle (Cyan)
        p1_top = int(self.p1_y - self.paddle_h / 2)
        p1_bot = int(self.p1_y + self.paddle_h / 2)
        draw.rectangle([(2, p1_top), (3, p1_bot)], fill=(40, 200, 255))

        # Right Paddle (Amber/Orange)
        p2_top = int(self.p2_y - self.paddle_h / 2)
        p2_bot = int(self.p2_y + self.paddle_h / 2)
        draw.rectangle([(size - 4, p2_top), (size - 3, p2_bot)], fill=(255, 140, 40))

        # Ball Motion Trail
        trail_colors = [(20, 60, 90), (40, 110, 160), (100, 180, 230)]
        for idx, (tx, ty) in enumerate(self.trail):
            color = trail_colors[min(idx, len(trail_colors) - 1)]
            draw.point((int(tx), int(ty)), fill=color)

        # Ball (Bright White/Yellow)
        bx = int(self.ball_x)
        by = int(self.ball_y)
        draw.rectangle([(bx, by), (bx + 1, by + 1)], fill=(255, 255, 240))
