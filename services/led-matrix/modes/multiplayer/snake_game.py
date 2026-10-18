"""Interactive Snake arcade game for 64x64 LED Matrix."""

from __future__ import annotations

import colorsys
import random
from collections import deque

from PIL import ImageDraw, ImageFont

from .base import MultiplayerGame

GRID_SIZE = 30  # 30x30 grid, 2x2 px cells with 2px margins = 64x64

DIR_MAP = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}

OPPOSITE = {
    "up": "down",
    "down": "up",
    "left": "right",
    "right": "left",
}


class InteractiveSnake(MultiplayerGame):
    """Playable Snake game controlled via mobile gamepad."""

    def __init__(self) -> None:
        super().__init__(solo_vs_ai=True)
        self.font = ImageFont.load_default(size=9)
        self.grid_size = GRID_SIZE
        self.snake: deque[tuple[int, int]] = deque()
        self.food: tuple[int, int] = (15, 15)
        self.current_dir = "right"
        self.next_dir = "right"
        self.score = 0
        self.high_score = 0
        self.step_interval = 0.085
        self.step_timer = 0.0
        self.particles: list[tuple[float, float, float, float, tuple[int, int, int], float]] = []
        self.reset()

    def reset(self) -> None:
        self.winner = 0
        cx, cy = self.grid_size // 2, self.grid_size // 2
        self.snake = deque([(cx - 2, cy), (cx - 1, cy), (cx, cy), (cx + 1, cy)])
        self.current_dir = "right"
        self.next_dir = "right"
        self.score = 0
        self.step_timer = 0.0
        self.particles.clear()
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

    def update(
        self,
        dt: float,
        p1_inputs: set[str],
        p2_inputs: set[str],
        p1_events: list[str] | None = None,
        p2_events: list[str] | None = None,
    ) -> None:
        # Update particles
        new_p = []
        for px, py, vx, vy, col, life in self.particles:
            life -= dt
            if life > 0:
                new_p.append((px + vx * dt, py + vy * dt, vx, vy, col, life))
        self.particles = new_p

        if self.is_game_over():
            return

        # 1. Check discrete events first
        if p1_events:
            for ev in p1_events:
                if ev in DIR_MAP and OPPOSITE[ev] != self.current_dir:
                    self.next_dir = ev
                    break
        else:
            # 2. Check held inputs
            for d in ("up", "down", "left", "right"):
                if d in p1_inputs and OPPOSITE[d] != self.current_dir and d != self.current_dir:
                    self.next_dir = d
                    break

        self.step_timer += dt
        if self.step_timer >= self.step_interval:
            self.step_timer -= self.step_interval
            self._step_snake()

    def get_final_score(self) -> int:
        return self.score

    def _step_snake(self) -> None:
        prev_dir = self.current_dir
        self.current_dir = self.next_dir
        if self.current_dir != prev_dir:
            self.play_sfx("turn")
        dx, dy = DIR_MAP[self.current_dir]
        head = self.snake[-1]
        new_head = (head[0] + dx, head[1] + dy)

        # Check wall or self collision
        if (
            not (0 <= new_head[0] < self.grid_size and 0 <= new_head[1] < self.grid_size)
            or new_head in self.snake
        ):
            # Game Over!
            self.winner = 1
            self.high_score = max(self.high_score, self.score)
            self.play_sfx("crash")
            # Spawn explosion particles
            hx = 2 + head[0] * 2
            hy = 2 + head[1] * 2
            for _ in range(16):
                speed = random.uniform(15, 45)
                self.particles.append((
                    float(hx),
                    float(hy),
                    speed * random.uniform(-1, 1),
                    speed * random.uniform(-1, 1),
                    (255, 60, 40),
                    random.uniform(0.3, 0.6),
                ))
            return

        self.snake.append(new_head)
        if new_head == self.food:
            self.score += 10
            self.play_sfx("eat")
            # Food sparkle
            fx = 2 + self.food[0] * 2
            fy = 2 + self.food[1] * 2
            for _ in range(8):
                self.particles.append((
                    float(fx),
                    float(fy),
                    random.uniform(-20, 20),
                    random.uniform(-20, 20),
                    (255, 220, 50),
                    random.uniform(0.2, 0.4),
                ))
            self._spawn_food()
        else:
            self.snake.popleft()


    def render(self, draw: ImageDraw.ImageDraw) -> None:
        # Background
        draw.rectangle([0, 0, 63, 63], fill=(5, 8, 14))

        # Arena border
        draw.rectangle([1, 1, 62, 62], outline=(25, 35, 55), width=1)

        offset_x, offset_y = 2, 2
        scale = 2

        # Draw Food (pulsing red/amber)
        fx = offset_x + self.food[0] * scale
        fy = offset_y + self.food[1] * scale
        draw.rectangle([fx, fy, fx + 1, fy + 1], fill=(255, 50, 40))

        # Draw Snake
        n_segments = len(self.snake)
        for idx, (sx, sy) in enumerate(self.snake):
            px = offset_x + sx * scale
            py = offset_y + sy * scale
            hue = (idx / max(1, n_segments)) * 0.35 + 0.25  # Green to cyan/lime
            rgb = colorsys.hsv_to_rgb(hue % 1.0, 0.9, 0.95)
            r, g, b = int(rgb[0] * 255), int(rgb[1] * 255), int(rgb[2] * 255)

            if idx == n_segments - 1:
                # Glowing head
                draw.rectangle([px, py, px + 1, py + 1], fill=(255, 255, 255))
            else:
                draw.rectangle([px, py, px + 1, py + 1], fill=(r, g, b))

        # Particles
        for px, py, _, _, col, _ in self.particles:
            if 0 <= px < 64 and 0 <= py < 64:
                draw.point((int(px), int(py)), fill=col)

        # Score in top right
        draw.text((45, 2), f"{self.score}", fill=(160, 200, 240), font=self.font)

    def get_status_text(self) -> str:
        return f"SCORE: {self.score} | LENGTH: {len(self.snake)}"
