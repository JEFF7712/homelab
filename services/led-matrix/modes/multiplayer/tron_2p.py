"""Two-Player Tron Light Cycles for 64x64 LED Matrix."""

import random

import numpy as np
from PIL import ImageDraw

from .base import MultiplayerGame

DIR_VECTORS = {
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


class Tron2P(MultiplayerGame):
    """2-Player real-time Tron Light Cycles."""

    def __init__(self, solo_vs_ai: bool = False, winning_rounds: int = 2) -> None:
        super().__init__(solo_vs_ai=solo_vs_ai)
        self.winning_rounds = winning_rounds

        self.p1_rounds = 0
        self.p2_rounds = 0
        self.current_round = 1

        self.grid = np.zeros((64, 64), dtype=np.int8)

        self.p1_pos = (16, 32)
        self.p2_pos = (47, 32)
        self.p1_dir = "right"
        self.p2_dir = "left"

        self.p1_crashed = False
        self.p2_crashed = False

        self.step_timer = 0.0
        self.step_interval = 0.075  # seconds per step (~13 moves/sec)
        self.round_delay = 0.0

        self.particles: list[tuple[float, float, float, float, tuple[int, int, int], float]] = []

        self.reset()

    def reset(self) -> None:
        self.winner = 0
        self.p1_rounds = 0
        self.p2_rounds = 0
        self.current_round = 1
        self.reset_round()

    def reset_round(self) -> None:
        self.grid.fill(0)
        # Build arena walls
        self.grid[0, :] = 3
        self.grid[63, :] = 3
        self.grid[:, 0] = 3
        self.grid[:, 63] = 3

        self.p1_pos = (16, 32)
        self.p2_pos = (47, 32)
        self.p1_dir = "right"
        self.p2_dir = "left"
        self.p1_next_dir = "right"
        self.p2_next_dir = "left"

        self.grid[self.p1_pos[1], self.p1_pos[0]] = 1
        self.grid[self.p2_pos[1], self.p2_pos[0]] = 2

        self.p1_crashed = False
        self.p2_crashed = False
        self.step_timer = 0.0
        self.round_delay = 1.0  # 1s ready pause

    def update(
        self,
        dt: float,
        p1_inputs: set[str],
        p2_inputs: set[str],
        p1_events: list[str] | None = None,
        p2_events: list[str] | None = None,
    ) -> None:
        # Update particles
        new_particles = []
        for px, py, pvx, pvy, color, life in self.particles:
            life -= dt
            if life > 0:
                new_particles.append((px + pvx * dt, py + pvy * dt, pvx, pvy, color, life))
        self.particles = new_particles

        if self.is_game_over():
            return

        if self.round_delay > 0:
            self.round_delay -= dt
            if self.round_delay <= 0 and (self.p1_crashed or self.p2_crashed):
                # Check match winner
                if self.p1_rounds >= self.winning_rounds:
                    self.winner = 1
                elif self.p2_rounds >= self.winning_rounds:
                    self.winner = 2
                else:
                    self.current_round += 1
                    self.reset_round()
            return

        # P1 direction changes: check discrete buffered events first, then held inputs
        if p1_events:
            for ev in p1_events:
                if ev in DIR_VECTORS and OPPOSITE[ev] != self.p1_dir:
                    self.p1_next_dir = ev
                    break
        else:
            for direction in ("up", "down", "left", "right"):
                if direction in p1_inputs and OPPOSITE[direction] != self.p1_dir and direction != self.p1_dir:
                    self.p1_next_dir = direction
                    break

        if self.solo_vs_ai:
            self._update_ai_dir()
        else:
            if p2_events:
                for ev in p2_events:
                    if ev in DIR_VECTORS and OPPOSITE[ev] != self.p2_dir:
                        self.p2_next_dir = ev
                        break
            else:
                for direction in ("up", "down", "left", "right"):
                    if direction in p2_inputs and OPPOSITE[direction] != self.p2_dir and direction != self.p2_dir:
                        self.p2_next_dir = direction
                        break

        self.step_timer += dt
        if self.step_timer >= self.step_interval:
            self.step_timer -= self.step_interval
            self._step_cycles()

    def _update_ai_dir(self) -> None:
        """AI lookahead: avoid walls and trails, maximize open area."""
        cur_x, cur_y = self.p2_pos
        dx, dy = DIR_VECTORS[self.p2_dir]
        front_x, front_y = cur_x + dx, cur_y + dy

        # If front is safe, usually continue unless a turn is safer
        safe_dirs = []
        for d, (vx, vy) in DIR_VECTORS.items():
            if OPPOSITE[d] == self.p2_dir:
                continue
            nx, ny = cur_x + vx, cur_y + vy
            if 0 < nx < 63 and 0 < ny < 63 and self.grid[ny, nx] == 0:
                safe_dirs.append(d)

        if not safe_dirs:
            return  # Trapped

        if (
            self.p2_dir in safe_dirs
            and (0 < front_x < 63 and 0 < front_y < 63 and self.grid[front_y, front_x] == 0)
            and random.random() < 0.85
        ):
            self.p2_next_dir = self.p2_dir
            return

        # Pick random safe direction
        self.p2_next_dir = random.choice(safe_dirs)

    def _step_cycles(self) -> None:
        self.p1_dir = self.p1_next_dir
        self.p2_dir = self.p2_next_dir

        p1_dx, p1_dy = DIR_VECTORS[self.p1_dir]
        p2_dx, p2_dy = DIR_VECTORS[self.p2_dir]

        next_p1 = (self.p1_pos[0] + p1_dx, self.p1_pos[1] + p1_dy)
        next_p2 = (self.p2_pos[0] + p2_dx, self.p2_pos[1] + p2_dy)

        # Check head-on collision
        if next_p1 == next_p2:
            self.p1_crashed = True
            self.p2_crashed = True
            self._explode(next_p1[0], next_p1[1], (255, 255, 255))
            self.round_delay = 1.5
            return

        # Check P1 collision
        p1_hit = False
        if not (0 < next_p1[0] < 63 and 0 < next_p1[1] < 63) or self.grid[next_p1[1], next_p1[0]] != 0:
            p1_hit = True

        # Check P2 collision
        p2_hit = False
        if not (0 < next_p2[0] < 63 and 0 < next_p2[1] < 63) or self.grid[next_p2[1], next_p2[0]] != 0:
            p2_hit = True

        if p1_hit and p2_hit:
            self.p1_crashed = True
            self.p2_crashed = True
            self._explode(next_p1[0], next_p1[1], (0, 229, 255))
            self._explode(next_p2[0], next_p2[1], (255, 145, 0))
            self.round_delay = 1.5
        elif p1_hit:
            self.p1_crashed = True
            self.p2_rounds += 1
            self._explode(next_p1[0], next_p1[1], (0, 229, 255))
            self.round_delay = 1.5
        elif p2_hit:
            self.p2_crashed = True
            self.p1_rounds += 1
            self._explode(next_p2[0], next_p2[1], (255, 145, 0))
            self.round_delay = 1.5
        else:
            # Move both cycles forward and lay trails
            self.p1_pos = next_p1
            self.p2_pos = next_p2
            self.grid[self.p1_pos[1], self.p1_pos[0]] = 1
            self.grid[self.p2_pos[1], self.p2_pos[0]] = 2

    def _explode(self, x: int, y: int, color: tuple[int, int, int]) -> None:
        for _ in range(24):
            speed = random.uniform(20.0, 70.0)
            pvx = speed * random.uniform(-1.0, 1.0)
            pvy = speed * random.uniform(-1.0, 1.0)
            life = random.uniform(0.3, 0.7)
            self.particles.append((float(x), float(y), pvx, pvy, color, life))

    def render(self, draw: ImageDraw.ImageDraw) -> None:
        # 1. Arena boundary wall
        draw.rectangle([0, 0, 63, 63], outline=(40, 40, 90))

        # 2. Draw light trails
        p1_ys, p1_xs = np.where(self.grid == 1)
        for y, x in zip(p1_ys, p1_xs):
            draw.point((int(x), int(y)), fill=(0, 180, 220))

        p2_ys, p2_xs = np.where(self.grid == 2)
        for y, x in zip(p2_ys, p2_xs):
            draw.point((int(x), int(y)), fill=(220, 110, 0))

        # 3. Draw cycle heads (bright glowing)
        if not self.p1_crashed:
            draw.point(self.p1_pos, fill=(255, 255, 255))
            draw.point((self.p1_pos[0], self.p1_pos[1]), fill=(0, 255, 255))

        if not self.p2_crashed:
            draw.point(self.p2_pos, fill=(255, 255, 255))
            draw.point((self.p2_pos[0], self.p2_pos[1]), fill=(255, 170, 0))

        # 4. Particles
        for px, py, _, _, color, _ in self.particles:
            if 0 <= px < 64 and 0 <= py < 64:
                draw.point((int(px), int(py)), fill=color)

        # 5. Round win indicators at top (dots for rounds won)
        # P1 round dots (Cyan)
        for i in range(self.p1_rounds):
            draw.rectangle([20 + i * 4, 2, 22 + i * 4, 3], fill=(0, 229, 255))
        # P2 round dots (Orange)
        for i in range(self.p2_rounds):
            draw.rectangle([40 + i * 4, 2, 42 + i * 4, 3], fill=(255, 145, 0))

    def get_status_text(self) -> str:
        return f"ROUNDS: P1 {self.p1_rounds} - {self.p2_rounds} P2 (R{self.current_round})"
