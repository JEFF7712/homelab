"""Autonomous AI Snake simulation on 64x64 matrix."""

from __future__ import annotations

import colorsys
import random
from collections import deque

from PIL import Image, ImageDraw, ImageFont

from .base import ArcadeGame

GRID_SIZE = 30  # 30x30 cells, 2x2 px each with 2px margins = 64x64


class SnakeGame(ArcadeGame):
    name = "SNAKE AI"

    def __init__(self) -> None:
        self.font = ImageFont.load_default(size=9)
        self.grid_size = GRID_SIZE
        self.snake: deque[tuple[int, int]] = deque()
        self.food: tuple[int, int] = (15, 15)
        self.direction: tuple[int, int] = (1, 0)
        self.score = 0
        self.step_interval = 0.08  # ~12 steps per second
        self.step_timer = 0.0
        self.game_over_timer = 0.0
        self.reset()

    def reset(self) -> None:
        cx, cy = self.grid_size // 2, self.grid_size // 2
        self.snake = deque([(cx - 2, cy), (cx - 1, cy), (cx, cy), (cx + 1, cy)])
        self.direction = (1, 0)
        self.score = 0
        self.game_over_timer = 0.0
        self.step_timer = 0.0
        self._spawn_food()

    def _spawn_food(self) -> None:
        occupied = set(self.snake)
        free_cells = [
            (x, y)
            for x in range(self.grid_size)
            for y in range(self.grid_size)
            if (x, y) not in occupied
        ]
        self.food = random.choice(free_cells) if free_cells else (0, 0)

    def _bfs_path(
        self,
        start: tuple[int, int],
        target: tuple[int, int],
        obstacles: set[tuple[int, int]],
    ) -> list[tuple[int, int]]:
        queue = deque([[start]])
        visited = {start}
        while queue:
            path = queue.popleft()
            curr = path[-1]
            if curr == target:
                return path
            for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                nx, ny = curr[0] + dx, curr[1] + dy
                if (
                    0 <= nx < self.grid_size
                    and 0 <= ny < self.grid_size
                    and (nx, ny) not in obstacles
                    and (nx, ny) not in visited
                ):
                    visited.add((nx, ny))
                    queue.append(path + [(nx, ny)])
        return []

    def _decide_move(self) -> tuple[int, int]:
        head = self.snake[-1]
        tail = self.snake[0]
        body = set(self.snake)

        # 1. Try to find path to food
        path_to_food = self._bfs_path(head, self.food, body)
        if len(path_to_food) > 1:
            next_step = path_to_food[1]
            # Safety check: simulate snake moving along path; can it still reach its tail?
            simulated_snake = list(self.snake)[1:] + [next_step]
            path_to_tail = self._bfs_path(
                next_step, tail, set(simulated_snake) - {tail}
            )
            if path_to_tail:
                return (next_step[0] - head[0], next_step[1] - head[1])

        # 2. Fallback: survive by moving towards tail
        path_to_tail = self._bfs_path(head, tail, body - {tail})
        if len(path_to_tail) > 1:
            next_step = path_to_tail[1]
            return (next_step[0] - head[0], next_step[1] - head[1])

        # 3. Emergency: any open neighbor
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nx, ny = head[0] + dx, head[1] + dy
            if (
                0 <= nx < self.grid_size
                and 0 <= ny < self.grid_size
                and (nx, ny) not in body
            ):
                return (dx, dy)

        return self.direction

    def update(self, dt: float) -> None:
        if self.game_over_timer > 0:
            self.game_over_timer -= dt
            if self.game_over_timer <= 0:
                self.reset()
            return

        self.step_timer += dt
        if self.step_timer < self.step_interval:
            return
        self.step_timer = 0.0

        # AI step
        dx, dy = self._decide_move()
        self.direction = (dx, dy)
        head = self.snake[-1]
        new_head = (head[0] + dx, head[1] + dy)

        # Collision with walls or self
        if (
            not (
                0 <= new_head[0] < self.grid_size and 0 <= new_head[1] < self.grid_size
            )
            or new_head in self.snake
        ):
            self.game_over_timer = 1.0
            return

        self.snake.append(new_head)
        if new_head == self.food:
            self.score += 1
            self._spawn_food()
        else:
            self.snake.popleft()

    def draw(
        self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64
    ) -> None:
        # Background
        draw.rectangle([(0, 0), (size - 1, size - 1)], fill=(5, 7, 12))

        # Border
        draw.rectangle([(1, 1), (size - 2, size - 2)], outline=(25, 35, 55), width=1)

        offset_x, offset_y = 2, 2
        scale = 2

        # Draw Food (pulsing red/amber)
        fx = offset_x + self.food[0] * scale
        fy = offset_y + self.food[1] * scale
        draw.rectangle([(fx, fy), (fx + 1, fy + 1)], fill=(255, 60, 40))

        # Draw Snake
        n_segments = len(self.snake)
        for idx, (sx, sy) in enumerate(self.snake):
            px = offset_x + sx * scale
            py = offset_y + sy * scale

            # Hue gradient from tail to head
            hue = (idx / max(1, n_segments)) * 0.35 + 0.25  # Green to cyan/yellow
            rgb = colorsys.hsv_to_rgb(hue % 1.0, 0.9, 0.95)
            r, g, b = int(rgb[0] * 255), int(rgb[1] * 255), int(rgb[2] * 255)

            if idx == n_segments - 1:
                # Head
                draw.rectangle([(px, py), (px + 1, py + 1)], fill=(255, 255, 255))
            else:
                draw.rectangle([(px, py), (px + 1, py + 1)], fill=(r, g, b))

        # Score display in top right
        score_str = f"{self.score}"
        draw.text((size - 14, 2), score_str, fill=(160, 200, 240), font=self.font)

        # Game Over Flash
        if self.game_over_timer > 0 and int(self.game_over_timer * 6) % 2 == 0:
            draw.rectangle(
                [(0, 0), (size - 1, size - 1)], outline=(255, 40, 40), width=2
            )
