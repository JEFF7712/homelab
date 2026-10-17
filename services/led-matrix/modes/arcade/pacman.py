"""Autonomous AI Pac-Man arcade simulation on 64x64 RGB matrix."""

from __future__ import annotations

import math
import random
from collections import deque

from PIL import Image, ImageDraw, ImageFont

from .base import ArcadeGame

GRID_W = 15
GRID_H = 13
CELL = 4
ORIGIN_X = 2
ORIGIN_Y = 10

# 1: Wall, 0: Dot, 2: Energizer, 3: Empty corridor (no dot), 4: Ghost house
MAZE_LAYOUT = [
    [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
    [1, 2, 0, 0, 0, 0, 1, 1, 1, 0, 0, 0, 0, 2, 1],
    [1, 0, 1, 1, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 1],
    [1, 0, 1, 1, 0, 1, 1, 3, 1, 1, 0, 1, 1, 0, 1],
    [3, 0, 0, 0, 0, 1, 4, 4, 4, 1, 0, 0, 0, 0, 3],
    [1, 0, 1, 1, 0, 1, 4, 4, 4, 1, 0, 1, 1, 0, 1],
    [1, 0, 0, 1, 0, 1, 1, 1, 1, 1, 0, 1, 0, 0, 1],
    [1, 1, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 1, 1],
    [1, 0, 0, 0, 0, 1, 1, 0, 1, 1, 0, 0, 0, 0, 1],
    [1, 0, 1, 1, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 1],
    [1, 0, 0, 1, 0, 1, 1, 1, 1, 1, 0, 1, 0, 0, 1],
    [1, 2, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 2, 1],
    [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
]

GHOST_COLORS = [
    (255, 30, 30),  # Blinky (Red)
    (255, 160, 220),  # Pinky (Pink)
    (0, 230, 255),  # Inky (Cyan)
    (255, 170, 50),  # Clyde (Orange)
]


class PacmanGame(ArcadeGame):
    name = "PAC-MAN"

    def __init__(self) -> None:
        self.font = ImageFont.load_default(size=9)
        self.score = 0
        self.high_score = 1200
        self.lives = 3
        self.reset()

    def reset(self) -> None:
        self.dots: set[tuple[int, int]] = set()
        self.energizers: set[tuple[int, int]] = set()
        for y, row in enumerate(MAZE_LAYOUT):
            for x, cell in enumerate(row):
                if cell == 0:
                    self.dots.add((x, y))
                elif cell == 2:
                    self.energizers.add((x, y))

        # Pac-man starting state
        self.pac_x = 7.0
        self.pac_y = 9.0
        self.pac_dir = (-1, 0)
        self.pac_next_dir = (-1, 0)
        self.pac_speed = 3.8
        self.mouth_timer = 0.0
        self.mouth_open = True

        # Ghosts
        # x, y, dx, dy, state ("normal", "frightened", "eaten"), respawn_timer
        self.ghosts = [
            {
                "x": 7.0,
                "y": 4.0,
                "dx": 0,
                "dy": -1,
                "state": "normal",
                "color": GHOST_COLORS[0],
                "home": (7, 4),
            },
            {
                "x": 6.0,
                "y": 5.0,
                "dx": 0,
                "dy": 1,
                "state": "normal",
                "color": GHOST_COLORS[1],
                "home": (6, 5),
            },
            {
                "x": 7.0,
                "y": 5.0,
                "dx": 0,
                "dy": -1,
                "state": "normal",
                "color": GHOST_COLORS[2],
                "home": (7, 5),
            },
            {
                "x": 8.0,
                "y": 5.0,
                "dx": 0,
                "dy": 1,
                "state": "normal",
                "color": GHOST_COLORS[3],
                "home": (8, 5),
            },
        ]
        self.ghost_speed = 3.2
        self.frightened_timer = 0.0
        self.ghosts_eaten = 0
        self.flash_timer = 0.0
        self.score_popups: list[dict] = []

    def _is_walkable(self, x: int, y: int, is_ghost: bool = False) -> bool:
        # Warp tunnel wrap
        if y == 4 and (x < 0 or x >= GRID_W):
            return True
        if not (0 <= y < GRID_H and 0 <= x < GRID_W):
            return False
        cell = MAZE_LAYOUT[y][x]
        if cell == 1:
            return False
        return not (cell == 4 and not is_ghost)

    def _get_valid_dirs(
        self, gx: int, gy: int, is_ghost: bool = False
    ) -> list[tuple[int, int]]:
        dirs = []
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx = (gx + dx) % GRID_W if gy == 4 else gx + dx
            ny = gy + dy
            if self._is_walkable(nx, ny, is_ghost=is_ghost):
                dirs.append((dx, dy))
        return dirs

    def _pac_ai_choose_dir(self) -> tuple[int, int]:
        """Greedy BFS pathfinder for Pac-Man attract mode."""
        gx = round(self.pac_x) % GRID_W
        gy = round(self.pac_y)
        valid_dirs = self._get_valid_dirs(gx, gy, is_ghost=False)
        if not valid_dirs:
            return self.pac_dir

        # Check proximity to dangerous ghosts
        closest_ghost_dist = 999.0
        ghost_dir = (0, 0)
        for g in self.ghosts:
            if g["state"] == "normal":
                dist = math.hypot(g["x"] - self.pac_x, g["y"] - self.pac_y)
                if dist < closest_ghost_dist:
                    closest_ghost_dist = dist
                    ghost_dir = (g["x"] - self.pac_x, g["y"] - self.pac_y)

        # Flee from close non-frightened ghosts
        if closest_ghost_dist < 3.2:
            best_dir = self.pac_dir
            max_dist = -1.0
            for dx, dy in valid_dirs:
                # Disallow 180 reverse unless trapped
                if len(valid_dirs) > 1 and (
                    dx == -self.pac_dir[0] and dy == -self.pac_dir[1]
                ):
                    continue
                nx = (gx + dx) % GRID_W
                ny = gy + dy
                dist = math.hypot(nx - (gx + ghost_dir[0]), ny - (gy + ghost_dir[1]))
                if dist > max_dist:
                    max_dist = dist
                    best_dir = (dx, dy)
            return best_dir

        # If frightened ghosts exist, hunt the closest one!
        if self.frightened_timer > 0.5:
            frightened = [g for g in self.ghosts if g["state"] == "frightened"]
            if frightened:
                frightened.sort(
                    key=lambda g: math.hypot(g["x"] - self.pac_x, g["y"] - self.pac_y)
                )
                target = (round(frightened[0]["x"]), round(frightened[0]["y"]))
                path = self._bfs((gx, gy), target)
                if len(path) >= 2:
                    return (path[1][0] - gx, path[1][1] - gy)

        # Hunt closest energizer or dot
        targets = list(self.energizers) if self.energizers else list(self.dots)
        if not targets:
            return valid_dirs[0]

        targets.sort(key=lambda t: math.hypot(t[0] - gx, t[1] - gy))
        for target in targets[:4]:
            path = self._bfs((gx, gy), target)
            if len(path) >= 2:
                step_dx = path[1][0] - gx
                step_dy = path[1][1] - gy
                if (step_dx, step_dy) in valid_dirs:
                    return (step_dx, step_dy)

        return random.choice(valid_dirs)

    def _bfs(
        self, start: tuple[int, int], target: tuple[int, int]
    ) -> list[tuple[int, int]]:
        queue = deque([[start]])
        visited = {start}
        while queue:
            path = queue.popleft()
            cx, cy = path[-1]
            if (cx, cy) == target:
                return path
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx = (cx + dx) % GRID_W if cy == 4 else cx + dx
                ny = cy + dy
                if (nx, ny) not in visited and self._is_walkable(nx, ny):
                    visited.add((nx, ny))
                    queue.append(path + [(nx, ny)])
        return []

    def update(self, dt: float) -> None:
        dt = min(dt, 0.05)
        self.mouth_timer += dt
        if self.mouth_timer >= 0.12:
            self.mouth_timer = 0.0
            self.mouth_open = not self.mouth_open

        # Timers
        if self.frightened_timer > 0:
            self.frightened_timer -= dt
            if self.frightened_timer <= 0:
                for g in self.ghosts:
                    if g["state"] == "frightened":
                        g["state"] = "normal"

        if self.flash_timer > 0:
            self.flash_timer -= dt

        # Update popups
        for pop in list(self.score_popups):
            pop["life"] -= dt
            pop["y"] -= dt * 6.0
            if pop["life"] <= 0:
                self.score_popups.remove(pop)

        # Pac-man movement
        curr_gx = round(self.pac_x)
        curr_gy = round(self.pac_y)
        dist_to_center = math.hypot(self.pac_x - curr_gx, self.pac_y - curr_gy)

        if dist_to_center < 0.15:
            # Snap to center of tile
            self.pac_x = float(curr_gx)
            self.pac_y = float(curr_gy)
            self.pac_dir = self._pac_ai_choose_dir()

        self.pac_x += self.pac_dir[0] * self.pac_speed * dt
        self.pac_y += self.pac_dir[1] * self.pac_speed * dt

        # Warp tunnel wrap
        if self.pac_y >= 3.8 and self.pac_y <= 4.2:
            if self.pac_x < -0.5:
                self.pac_x = float(GRID_W - 0.5)
            elif self.pac_x >= GRID_W - 0.5:
                self.pac_x = -0.5

        # Eat dots & energizers
        gx = round(self.pac_x) % GRID_W
        gy = round(self.pac_y)
        if (gx, gy) in self.dots:
            self.dots.remove((gx, gy))
            self.score += 10
            self.high_score = max(self.high_score, self.score)

        if (gx, gy) in self.energizers:
            self.energizers.remove((gx, gy))
            self.score += 50
            self.frightened_timer = 6.5
            self.ghosts_eaten = 0
            for g in self.ghosts:
                if g["state"] == "normal":
                    g["state"] = "frightened"
            self.high_score = max(self.high_score, self.score)

        if not self.dots and not self.energizers:
            self.flash_timer = 0.5
            self.reset()
            return

        # Ghost movement & AI
        for g in self.ghosts:
            speed = self.ghost_speed
            if g["state"] == "frightened":
                speed = 1.9
            elif g["state"] == "eaten":
                speed = 5.0

            gx = round(g["x"]) % GRID_W
            gy = round(g["y"])
            dist_c = math.hypot(g["x"] - gx, g["y"] - gy)

            if dist_c < 0.15:
                g["x"] = float(gx)
                g["y"] = float(gy)
                valid = self._get_valid_dirs(gx, gy, is_ghost=True)

                # Disallow immediate 180 turn
                rev = (-g["dx"], -g["dy"])
                filtered = [d for d in valid if d != rev]
                if not filtered:
                    filtered = valid

                if g["state"] == "eaten":
                    # Head back to home
                    target = g["home"]
                    if (gx, gy) == target:
                        g["state"] = "normal"
                    else:
                        filtered.sort(
                            key=lambda d: math.hypot(
                                (gx + d[0]) - target[0], (gy + d[1]) - target[1]
                            )
                        )
                        g["dx"], g["dy"] = filtered[0]
                elif g["state"] == "frightened":
                    # Run away from Pac-Man
                    filtered.sort(
                        key=lambda d: math.hypot(
                            (gx + d[0]) - self.pac_x, (gy + d[1]) - self.pac_y
                        ),
                        reverse=True,
                    )
                    g["dx"], g["dy"] = filtered[0]
                else:
                    # Target Pac-Man
                    filtered.sort(
                        key=lambda d: math.hypot(
                            (gx + d[0]) - self.pac_x, (gy + d[1]) - self.pac_y
                        )
                    )
                    g["dx"], g["dy"] = filtered[0]

            g["x"] += g["dx"] * speed * dt
            g["y"] += g["dy"] * speed * dt

            # Warp wrap
            if g["y"] >= 3.8 and g["y"] <= 4.2:
                if g["x"] < -0.5:
                    g["x"] = float(GRID_W - 0.5)
                elif g["x"] >= GRID_W - 0.5:
                    g["x"] = -0.5

            # Collision with Pac-Man
            dist_to_pac = math.hypot(g["x"] - self.pac_x, g["y"] - self.pac_y)
            if dist_to_pac < 0.75:
                if g["state"] == "frightened":
                    # Eat ghost
                    g["state"] = "eaten"
                    pts = 200 * (2**self.ghosts_eaten)
                    self.ghosts_eaten += 1
                    self.score += pts
                    self.high_score = max(self.high_score, self.score)
                    px = ORIGIN_X + int(g["x"] * CELL)
                    py = ORIGIN_Y + int(g["y"] * CELL)
                    self.score_popups.append(
                        {"x": px, "y": py, "text": f"{pts}", "life": 0.8}
                    )
                elif g["state"] == "normal":
                    # Pac-Man dies and resets round
                    self.lives -= 1
                    if self.lives <= 0:
                        self.score = 0
                        self.lives = 3
                    self.pac_x = 7.0
                    self.pac_y = 9.0
                    self.pac_dir = (-1, 0)
                    for ghost in self.ghosts:
                        ghost["x"] = float(ghost["home"][0])
                        ghost["y"] = float(ghost["home"][1])
                        ghost["state"] = "normal"
                    return

    def draw(
        self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64
    ) -> None:
        # Background
        draw.rectangle([(0, 0), (size - 1, size - 1)], fill=(0, 0, 0))

        # Top HUD
        draw.text((2, 0), f"S{self.score}", fill=(240, 240, 240), font=self.font)
        draw.text((36, 0), "PAC-MAN", fill=(255, 230, 0), font=self.font)

        # Flash effect when clearing maze
        wall_col = (255, 255, 255) if self.flash_timer > 0 else (28, 42, 215)
        inner_wall = (15, 22, 110) if self.flash_timer <= 0 else (200, 200, 255)

        # Draw Maze Walls
        for y, row in enumerate(MAZE_LAYOUT):
            for x, cell in enumerate(row):
                px = ORIGIN_X + x * CELL
                py = ORIGIN_Y + y * CELL
                if cell == 1:
                    draw.rectangle(
                        [(px, py), (px + CELL - 1, py + CELL - 1)],
                        fill=inner_wall,
                        outline=wall_col,
                    )
                elif cell == 4:
                    # Ghost house
                    draw.rectangle(
                        [(px, py), (px + CELL - 1, py + CELL - 1)], fill=(12, 14, 25)
                    )
                    if y == 4 and x == 7:
                        # Pink gate
                        draw.line(
                            [(px, py), (px + CELL - 1, py)],
                            fill=(255, 140, 190),
                            width=1,
                        )

        # Draw Dots
        for x, y in self.dots:
            px = ORIGIN_X + x * CELL + 1
            py = ORIGIN_Y + y * CELL + 1
            draw.point((px, py), fill=(255, 225, 150))

        # Draw Energizers (blinking)
        if int(self.mouth_timer * 10) % 2 == 0:
            for x, y in self.energizers:
                px = ORIGIN_X + x * CELL
                py = ORIGIN_Y + y * CELL
                draw.rectangle([(px, py), (px + 2, py + 2)], fill=(255, 255, 255))

        # Draw Pac-Man
        pac_px = int(ORIGIN_X + self.pac_x * CELL)
        pac_py = int(ORIGIN_Y + self.pac_y * CELL)
        # Body
        draw.rectangle(
            [(pac_px - 1, pac_py - 1), (pac_px + 2, pac_py + 2)], fill=(255, 235, 0)
        )

        # Mouth chomp cut
        if self.mouth_open:
            if self.pac_dir == (1, 0):  # facing right
                draw.point((pac_px + 2, pac_py), fill=(0, 0, 0))
                draw.point((pac_px + 2, pac_py + 1), fill=(0, 0, 0))
            elif self.pac_dir == (-1, 0):  # facing left
                draw.point((pac_px - 1, pac_py), fill=(0, 0, 0))
                draw.point((pac_px - 1, pac_py + 1), fill=(0, 0, 0))
            elif self.pac_dir == (0, -1):  # facing up
                draw.point((pac_px, pac_py - 1), fill=(0, 0, 0))
                draw.point((pac_px + 1, pac_py - 1), fill=(0, 0, 0))
            elif self.pac_dir == (0, 1):  # facing down
                draw.point((pac_px, pac_py + 2), fill=(0, 0, 0))
                draw.point((pac_px + 1, pac_py + 2), fill=(0, 0, 0))

        # Draw Ghosts
        for g in self.ghosts:
            gx = int(ORIGIN_X + g["x"] * CELL)
            gy = int(ORIGIN_Y + g["y"] * CELL)

            if g["state"] == "normal":
                color = g["color"]
                draw.rectangle([(gx - 1, gy - 1), (gx + 2, gy + 1)], fill=color)
                # Wavy feet
                draw.point((gx - 1, gy + 2), fill=color)
                draw.point((gx + 2, gy + 2), fill=color)
                # Eyes
                draw.point((gx, gy - 1), fill=(255, 255, 255))
                draw.point((gx + 1, gy - 1), fill=(255, 255, 255))
                draw.point((gx + max(-1, min(1, g["dx"])), gy - 1), fill=(20, 20, 120))
            elif g["state"] == "frightened":
                # Flash white when expiring
                is_flashing = (
                    self.frightened_timer < 2.0
                    and int(self.frightened_timer * 6) % 2 == 0
                )
                fright_col = (240, 240, 255) if is_flashing else (35, 60, 240)
                draw.rectangle([(gx - 1, gy - 1), (gx + 2, gy + 2)], fill=fright_col)
                # Wavy mouth
                draw.point((gx, gy + 1), fill=(255, 180, 180))
            elif g["state"] == "eaten":
                # Eyes returning to pen
                draw.point((gx - 1, gy), fill=(255, 255, 255))
                draw.point((gx + 1, gy), fill=(255, 255, 255))
                draw.point((gx - 1, gy + 1), fill=(20, 40, 200))
                draw.point((gx + 1, gy + 1), fill=(20, 40, 200))

        # Score popups
        for pop in self.score_popups:
            draw.text(
                (int(pop["x"]), int(pop["y"])),
                pop["text"],
                fill=(0, 240, 255),
                font=self.font,
            )
