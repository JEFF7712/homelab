"""Autonomous vertical platform hopping with a scrolling camera."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from PIL import Image, ImageDraw

from modes.text_utils import draw_boxed_message, draw_scoreboard

from .base import ArcadeGame


@dataclass
class Platform:
    x: float
    y: float
    phase: float = 0.0
    moving: bool = False
    spring: bool = False

    def center(self, tick: float) -> float:
        return self.x + (4 * math.sin(tick * 1.8 + self.phase) if self.moving else 0)


class DoodleJumpGame(ArcadeGame):
    name = "DOODLE JUMP"
    GRAVITY = 90.0
    JUMP_SPEED = 52.0

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng if rng is not None else random.Random()
        self.reset()

    def reset(self) -> None:
        self.tick = 0.0
        self.camera = 0.0
        self.x, self.y = 32.0, 8.0
        self.vy = self.JUMP_SPEED
        self.score = 0
        self.highest = self.y
        self.landings = 0
        self.game_over_timer = 0.0
        self.platform_number = 0
        self.platforms = [Platform(32.0, 8.0)]
        self._extend_platforms(100)
        self.target = self.platforms[1]

    def _extend_platforms(self, height: float) -> None:
        while self.platforms[-1].y < height:
            previous = self.platforms[-1]
            self.platform_number += 1
            self.platforms.append(
                Platform(
                    x=max(12.0, min(51.0, previous.x + self.rng.uniform(-15, 15))),
                    y=previous.y + self.rng.uniform(10, 12),
                    phase=self.rng.uniform(0, math.tau),
                    moving=self.platform_number % 4 == 0,
                    spring=self.platform_number % 7 == 0,
                )
            )

    def _launch(self, platform: Platform) -> None:
        self.vy = 65.0 if platform.spring else self.JUMP_SPEED
        apex = platform.y + self.vy * self.vy / (2 * self.GRAVITY) - 2
        candidates = [p for p in self.platforms if platform.y < p.y <= apex]
        self.target = min(candidates, key=lambda p: p.y) if candidates else platform

    def update(self, dt: float) -> None:
        dt = max(0.0, min(dt, 0.05))
        self.tick += dt
        if self.game_over_timer > 0:
            self.game_over_timer -= dt
            if self.game_over_timer <= 0:
                self.reset()
            return
        target_x = self.target.center(self.tick)
        self.x += max(-32 * dt, min(32 * dt, target_x - self.x))
        old_y = self.y
        self.y += self.vy * dt - self.GRAVITY * dt * dt / 2
        self.vy -= self.GRAVITY * dt
        if self.vy <= 0:
            for platform in reversed(self.platforms):
                if (
                    self.y <= platform.y <= old_y
                    and abs(self.x - platform.center(self.tick)) <= 7
                ):
                    self.y = platform.y
                    self.landings += 1
                    self._launch(platform)
                    break
        self.highest = max(self.highest, self.y)
        self.score = int(self.highest - 8) * 10
        self.camera = max(self.camera, self.y - 37)
        self.platforms = [p for p in self.platforms if p.y >= self.camera - 12]
        self._extend_platforms(self.camera + 90)
        if self.y < self.camera - 5:
            self.game_over_timer = 1.2

    def draw(
        self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64
    ) -> None:
        draw.rectangle((0, 0, size - 1, size - 1), fill=(4, 14, 20))
        for x in range(5, size, 12):
            for y in range(12, size, 12):
                draw.point((x, y), fill=(13, 34, 40))
        for platform in self.platforms:
            y = round(size - 1 - (platform.y - self.camera))
            if not 12 <= y < size:
                continue
            x = round(platform.center(self.tick))
            color = (70, 180, 255) if platform.moving else (110, 215, 70)
            draw.rounded_rectangle((x - 7, y, x + 6, y + 2), radius=1, fill=color)
            if platform.spring:
                draw.line(
                    (x - 1, y - 4, x + 1, y - 4, x - 1, y - 2, x + 1, y - 2),
                    fill=(255, 220, 60),
                )
        px, py = round(self.x), round(size - 1 - (self.y - self.camera))
        if py >= 14:
            facing = 1 if self.target.center(self.tick) >= self.x else -1
            draw.rounded_rectangle(
                (px - 3, py - 7, px + 3, py - 2), radius=2, fill=(175, 235, 65)
            )
            draw.rectangle(
                (px + facing * 4, py - 5, px + facing * 4 + 1, py - 3),
                fill=(175, 235, 65),
            )
            draw.point((px + facing, py - 6), fill=(8, 25, 20))
            draw.line((px - 2, py - 1, px - 2, py), fill=(235, 245, 200))
            draw.line((px + 2, py - 1, px + 2, py), fill=(235, 245, 200))
        draw.rectangle((0, 0, size - 1, 9), fill=(4, 14, 20))
        draw_scoreboard(
            draw, str(self.score), "DOODLE", size=size, right_color=(175, 235, 65)
        )
        if self.game_over_timer > 0:
            draw_boxed_message(draw, "GAME OVER", f"SCORE {self.score}")
