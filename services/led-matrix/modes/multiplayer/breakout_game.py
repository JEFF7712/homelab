"""Interactive Breakout arcade game for 64x64 LED Matrix."""

from __future__ import annotations

import random
from dataclasses import dataclass

from PIL import ImageDraw, ImageFont

from .base import MultiplayerGame


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


class InteractiveBreakout(MultiplayerGame):
    """Playable Breakout brick-breaker controlled via mobile gamepad."""

    def __init__(self) -> None:
        super().__init__(solo_vs_ai=True)
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
        self.lives = 3
        self.bricks: list[Brick] = []
        self.sparks: list[Spark] = []
        self.reset()

    def reset(self) -> None:
        self.winner = 0
        self.score = 0
        self.lives = 3
        self.paddle_x = 32.0
        self._reset_ball()
        self.sparks.clear()
        self._init_bricks()

    def _reset_ball(self) -> None:
        self.paddle_x = 32.0
        self.ball_x = 32.0
        self.ball_y = 41.0
        self.ball_vx = random.choice([-32.0, 32.0])
        self.ball_vy = -36.0

    def _init_bricks(self) -> None:
        self.bricks.clear()
        row_colors = [
            (255, 40, 50),   # Red
            (255, 130, 20),  # Orange
            (255, 215, 30),  # Yellow
            (40, 220, 90),   # Green
            (30, 190, 255),  # Cyan
            (220, 50, 200),  # Magenta
        ]
        bw, bh = 7, 2
        for r_idx, color in enumerate(row_colors):
            y = 13 + r_idx * 3
            for c_idx in range(8):
                x = 2 + c_idx * (bw + 1)
                self.bricks.append(Brick(x=x, y=y, w=bw, h=bh, color=color))

    def update(
        self,
        dt: float,
        p1_inputs: set[str],
        p2_inputs: set[str],
        p1_events: list[str] | None = None,
        p2_events: list[str] | None = None,
    ) -> None:
        if self.is_game_over():
            return

        dt = min(dt, 0.05)

        # Update sparks
        surviving = []
        for s in self.sparks:
            s.x += s.vx * dt
            s.y += s.vy * dt
            s.vy += 60.0 * dt
            s.life -= dt
            if s.life > 0:
                surviving.append(s)
        self.sparks = surviving

        # Player paddle input (held or discrete taps)
        move_left = "left" in p1_inputs or (p1_events and "left" in p1_events)
        move_right = "right" in p1_inputs or (p1_events and "right" in p1_events)

        if move_left:
            self.paddle_x -= self.paddle_speed * dt
        if move_right:
            self.paddle_x += self.paddle_speed * dt

        half_w = self.paddle_w / 2.0
        self.paddle_x = max(half_w + 1, min(63 - half_w, self.paddle_x))

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
            # Ball lost!
            self.lives -= 1
            if self.lives <= 0:
                self.winner = 1  # Game over
            else:
                self._reset_ball()
            return

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
            if (
                b.x - 1 <= self.ball_x <= b.x + b.w + 1
                and b.y - 1 <= self.ball_y <= b.y + b.h + 1
            ):
                b.alive = False
                self.ball_vy = -self.ball_vy
                self.score += 15

                # Sparks
                for _ in range(6):
                    self.sparks.append(
                        Spark(
                            x=self.ball_x,
                            y=self.ball_y,
                            vx=random.uniform(-30.0, 30.0),
                            vy=random.uniform(-25.0, 10.0),
                            color=b.color,
                        )
                    )
                break

        if all_cleared:
            # Victory! Respawn bricks
            self.score += 100
            self._init_bricks()

    def render(self, draw: ImageDraw.ImageDraw) -> None:
        # Background
        draw.rectangle([0, 0, 63, 63], fill=(8, 10, 18))

        # Top border
        draw.line([(0, 0), (63, 0)], fill=(30, 40, 60))
        draw.line([(0, 0), (0, 63)], fill=(30, 40, 60))
        draw.line([(63, 0), (63, 63)], fill=(30, 40, 60))

        # Score HUD
        draw.text((2, 2), f"{self.score:04d}", fill=(180, 210, 255), font=self.font)

        # Lives HUD (pips)
        for i in range(self.lives):
            draw.rectangle([54 - i * 5, 3, 56 - i * 5, 5], fill=(255, 60, 60))

        # Bricks
        for b in self.bricks:
            if b.alive:
                draw.rectangle([b.x, b.y, b.x + b.w - 1, b.y + b.h - 1], fill=b.color)

        # Sparks
        for s in self.sparks:
            px, py = int(s.x), int(s.y)
            if 0 <= px < 64 and 0 <= py < 64:
                draw.point((px, py), fill=s.color)

        # Paddle
        half_w = self.paddle_w / 2.0
        px0 = int(self.paddle_x - half_w)
        px1 = int(self.paddle_x + half_w)
        py0 = int(self.paddle_y)
        py1 = int(self.paddle_y + self.paddle_h - 1)
        draw.rectangle([px0, py0, px1, py1], fill=(0, 229, 255))

        # Ball
        bx = int(self.ball_x)
        by = int(self.ball_y)
        draw.rectangle([bx, by, bx + 1, by + 1], fill=(255, 255, 255))

    def get_status_text(self) -> str:
        return f"SCORE: {self.score} | LIVES: {'♥ ' * self.lives}"
