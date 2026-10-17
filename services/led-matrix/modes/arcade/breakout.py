"""Autonomous AI Breakout simulation on 64x64 matrix."""

from __future__ import annotations

import random
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from .base import ArcadeGame


@dataclass
class Brick:
    x: int
    y: int
    w: int
    h: int
    color: tuple[int, int, int]
    alive: bool = True


@dataclass
class Spark:
    x: float
    y: float
    vx: float
    vy: float
    color: tuple[int, int, int]
    life: float = 0.4


class BreakoutGame(ArcadeGame):
    name = "BREAKOUT"

    def __init__(self) -> None:
        self.font = ImageFont.load_default(size=9)
        self.paddle_w = 14.0
        self.paddle_h = 2.0
        self.paddle_y = 59.0
        self.paddle_x = 32.0
        self.paddle_speed = 52.0

        self.ball_x = 32.0
        self.ball_y = 35.0
        self.ball_vx = 32.0
        self.ball_vy = 36.0
        self.score = 0
        self.bricks: list[Brick] = []
        self.sparks: list[Spark] = []
        self.reset()

    def reset(self) -> None:
        self.paddle_x = 32.0
        self.ball_x = 32.0
        self.ball_y = 41.0
        self.ball_vx = random.choice([-32.0, 32.0])
        self.ball_vy = -36.0
        self.sparks.clear()
        self._init_bricks()

    def _init_bricks(self) -> None:
        self.bricks.clear()
        row_colors = [
            (255, 40, 50),  # Red
            (255, 130, 20),  # Orange
            (255, 215, 30),  # Yellow
            (40, 220, 90),  # Green
            (30, 190, 255),  # Cyan
            (220, 50, 200),  # Magenta
        ]
        bw, bh = 7, 2
        for r_idx, color in enumerate(row_colors):
            # Start below the top HUD band that holds the score
            y = 13 + r_idx * 3
            for c_idx in range(8):
                x = 2 + c_idx * (bw + 1)
                self.bricks.append(Brick(x=x, y=y, w=bw, h=bh, color=color))

    def update(self, dt: float) -> None:
        dt = min(dt, 0.05)

        # Update sparks
        surviving_sparks = []
        for s in self.sparks:
            s.x += s.vx * dt
            s.y += s.vy * dt
            s.vy += 60.0 * dt  # Gravity
            s.life -= dt
            if s.life > 0:
                surviving_sparks.append(s)
        self.sparks = surviving_sparks

        # Ball movement
        self.ball_x += self.ball_vx * dt
        self.ball_y += self.ball_vy * dt

        # Wall collisions
        if self.ball_x <= 1:
            self.ball_x = 1
            self.ball_vx = abs(self.ball_vx)
        elif self.ball_x >= 62:
            self.ball_x = 62
            self.ball_vx = -abs(self.ball_vx)

        if self.ball_y <= 1:
            self.ball_y = 1
            self.ball_vy = abs(self.ball_vy)
        elif self.ball_y >= 63:
            # Ball lost: reset ball & paddle
            self.ball_x = 32.0
            self.ball_y = 41.0
            self.ball_vx = random.choice([-32.0, 32.0])
            self.ball_vy = -36.0

        # AI Paddle Movement
        target_x = self.ball_x if self.ball_vy > 0 else 32.0
        if self.paddle_x < target_x - 1.0:
            self.paddle_x += self.paddle_speed * dt
        elif self.paddle_x > target_x + 1.0:
            self.paddle_x -= self.paddle_speed * dt
        half_w = self.paddle_w / 2.0
        self.paddle_x = max(half_w + 1, min(63 - half_w, self.paddle_x))

        # Paddle Collision
        if (
            self.paddle_y - 1.5 <= self.ball_y <= self.paddle_y + 1.5
            and self.ball_vy > 0
            and abs(self.ball_x - self.paddle_x) <= half_w + 1.0
        ):
            self.ball_y = self.paddle_y - 1.5
            offset = (self.ball_x - self.paddle_x) / half_w
            self.ball_vx = offset * 45.0
            self.ball_vy = -max(28.0, 48.0 - abs(offset) * 15.0)

        # Brick Collisions
        all_cleared = True
        for b in self.bricks:
            if not b.alive:
                continue
            all_cleared = False
            # AABB check
            if (
                b.x - 1 <= self.ball_x <= b.x + b.w + 1
                and b.y - 1 <= self.ball_y <= b.y + b.h + 1
            ):
                b.alive = False
                self.score += 10
                self.ball_vy = -self.ball_vy

                # Spawn spark burst
                for _ in range(4):
                    self.sparks.append(
                        Spark(
                            x=self.ball_x,
                            y=self.ball_y,
                            vx=random.uniform(-30, 30),
                            vy=random.uniform(-40, -10),
                            color=b.color,
                        )
                    )
                break

        if all_cleared:
            self._init_bricks()
            self.ball_y = 41.0
            self.ball_vy = -36.0

    def draw(
        self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64
    ) -> None:
        draw.rectangle([(0, 0), (size - 1, size - 1)], fill=(4, 6, 12))

        # Draw Bricks
        for b in self.bricks:
            if b.alive:
                draw.rectangle(
                    [(b.x, b.y), (b.x + b.w - 1, b.y + b.h - 1)], fill=b.color
                )

        # Draw Sparks
        for s in self.sparks:
            alpha = max(0.2, s.life / 0.4)
            color = (
                int(s.color[0] * alpha),
                int(s.color[1] * alpha),
                int(s.color[2] * alpha),
            )
            draw.point((int(s.x), int(s.y)), fill=color)

        # Draw Paddle (Neon Cyan)
        px1 = int(self.paddle_x - self.paddle_w / 2)
        px2 = int(self.paddle_x + self.paddle_w / 2)
        py = int(self.paddle_y)
        draw.rectangle(
            [(px1, py), (px2, py + int(self.paddle_h) - 1)], fill=(40, 220, 255)
        )

        # Draw Ball
        bx = int(self.ball_x)
        by = int(self.ball_y)
        draw.rectangle([(bx, by), (bx + 1, by + 1)], fill=(255, 255, 240))

        # Score HUD drawn last so bricks, sparks, and the ball never cover it
        self._draw_score(draw, size)

    def _draw_score(self, draw: ImageDraw.ImageDraw, size: int) -> None:
        score_str = f"{self.score}"
        left, top, right, bottom = draw.textbbox((0, 0), score_str, font=self.font)
        text_w = right - left
        text_h = bottom - top
        x = size - text_w - 2
        y = 1
        # Opaque backing keeps the score legible over the playfield
        draw.rectangle([(x - 2, 0), (size - 1, y + text_h)], fill=(4, 6, 12))
        draw.text((x - left, y - top), score_str, fill=(255, 230, 120), font=self.font)
