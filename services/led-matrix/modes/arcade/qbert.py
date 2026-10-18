"""Autonomous cube hopping on an isometric pyramid with a chasing snake."""

from __future__ import annotations

import math
import random
from collections import deque

from PIL import Image, ImageDraw

from modes.text_utils import draw_boxed_message, draw_scoreboard

from .base import ArcadeGame

Cell = tuple[int, int]


class QbertGame(ArcadeGame):
    name = "Q*BERT"
    ROWS = 6
    HOP_TIME = 0.22

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng if rng is not None else random.Random()
        self.reset()

    def reset(self) -> None:
        self.level = 1
        self.score = 0
        self.lives = 3
        self.tick = 0.0
        self._reset_pyramid()

    def _reset_pyramid(self) -> None:
        self.position: Cell = (0, 0)
        self.coily: Cell = (self.ROWS - 1, self.ROWS - 1)
        self.painted: set[Cell] = {self.position}
        self.hop_to: Cell | None = None
        self.hop_elapsed = 0.0
        self.pause = 0.1
        self.snake_timer = 1.8
        self.transition_timer = 0.0
        self.cleared = False

    @classmethod
    def neighbors(cls, cell: Cell) -> list[Cell]:
        row, col = cell
        return [
            (r, c)
            for r, c in (
                (row - 1, col - 1),
                (row - 1, col),
                (row + 1, col),
                (row + 1, col + 1),
            )
            if 0 <= r < cls.ROWS and 0 <= c <= r
        ]

    def _choose_hop(self) -> Cell | None:
        queue: deque[tuple[Cell, Cell | None]] = deque([(self.position, None)])
        blocked = {self.coily}
        if self.snake_timer <= self.HOP_TIME + 0.05:
            blocked.add(self._snake_step())
        visited = {self.position} | blocked
        while queue:
            cell, first = queue.popleft()
            if cell not in self.painted:
                return first
            choices = self.neighbors(cell)
            self.rng.shuffle(choices)
            for neighbor in choices:
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, neighbor if first is None else first))
        safe = [n for n in self.neighbors(self.position) if n not in blocked]
        return self.rng.choice(safe) if safe else None

    def _snake_step(self) -> Cell:
        tx, ty = self._screen(self.position)
        return min(
            self.neighbors(self.coily),
            key=lambda c: (
                (self._screen(c)[0] - tx) ** 2 + (self._screen(c)[1] - ty) ** 2
            ),
        )

    def _hit(self) -> None:
        self.lives -= 1
        self.transition_timer = 1.0
        self.hop_to = None

    def update(self, dt: float) -> None:
        dt = max(0.0, min(dt, 0.05))
        self.tick += dt
        if self.transition_timer > 0:
            self.transition_timer -= dt
            if self.transition_timer <= 0:
                if self.cleared:
                    self.level += 1
                    self._reset_pyramid()
                elif self.lives <= 0:
                    self.reset()
                else:
                    self.position = (0, 0)
                    self.coily = (self.ROWS - 1, self.ROWS - 1)
                    self.snake_timer = 1.8
            return
        self.snake_timer -= dt
        if self.snake_timer <= 0:
            self.coily = self._snake_step()
            self.snake_timer = max(0.65, 1.1 - self.level * 0.04)
            if self.hop_to is None and self.position == self.coily:
                self._hit()
                return
        if self.hop_to is not None:
            self.hop_elapsed += dt
            if self.hop_elapsed >= self.HOP_TIME:
                self.position = self.hop_to
                self.hop_to = None
                if self.position == self.coily:
                    self._hit()
                    return
                if self.position not in self.painted:
                    self.painted.add(self.position)
                    self.score += 25
                if len(self.painted) == self.ROWS * (self.ROWS + 1) // 2:
                    self.score += 200
                    self.cleared = True
                    self.transition_timer = 1.0
                self.pause = 0.1
            return
        self.pause -= dt
        if self.pause <= 0:
            self.hop_to = self._choose_hop()
            self.hop_elapsed = 0.0

    @staticmethod
    def _screen(cell: Cell) -> tuple[float, float]:
        row, col = cell
        return 31.5 + (2 * col - row) * 4.5, 17 + row * 7.0

    def draw(
        self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64
    ) -> None:
        draw.rectangle((0, 0, size - 1, size - 1), fill=(6, 6, 20))
        target_color = (255, 185, 45) if self.level % 2 else (65, 215, 185)
        for row in range(self.ROWS):
            for col in range(row + 1):
                cell = (row, col)
                x, y = self._screen(cell)
                color = target_color if cell in self.painted else (45, 85, 170)
                draw.polygon(
                    [(x - 4, y), (x, y + 2), (x, y + 6), (x - 4, y + 3)],
                    fill=(24, 37, 82),
                )
                draw.polygon(
                    [(x, y + 2), (x + 4, y), (x + 4, y + 3), (x, y + 6)],
                    fill=(35, 48, 108),
                )
                draw.polygon(
                    [(x, y - 3), (x + 4, y - 1), (x, y + 2), (x - 4, y - 1)], fill=color
                )
        sx, sy = self._screen(self.coily)
        sx, sy = round(sx), round(sy)
        draw.ellipse((sx - 2, sy - 4, sx + 2, sy), fill=(175, 70, 225))
        draw.rectangle((sx - 1, sy - 7, sx + 2, sy - 3), fill=(195, 95, 245))
        draw.point((sx, sy - 6), fill=(255, 255, 255))
        draw.point((sx + 2, sy - 6), fill=(255, 255, 255))
        x, y = self._screen(self.position)
        if self.hop_to is not None:
            t = min(1.0, self.hop_elapsed / self.HOP_TIME)
            tx, ty = self._screen(self.hop_to)
            x += (tx - x) * t
            y += (ty - y) * t - math.sin(t * math.pi) * 5
        px, py = round(x), round(y)
        draw.ellipse((px - 2, py - 6, px + 2, py - 2), fill=(255, 125, 35))
        draw.rectangle((px + 2, py - 5, px + 4, py - 4), fill=(255, 155, 65))
        draw.point((px, py - 5), fill=(255, 255, 255))
        draw.point((px, py - 1), fill=(255, 205, 135))
        draw_scoreboard(
            draw, str(self.score), "Q*BERT", size=size, right_color=target_color
        )
        if self.transition_timer > 0:
            if self.cleared:
                draw_boxed_message(
                    draw,
                    "PYRAMID CLEAR",
                    f"LEVEL {self.level}",
                    border_color=target_color,
                    title_color=target_color,
                )
            elif self.lives <= 0:
                draw_boxed_message(draw, "GAME OVER", f"SCORE {self.score}")
