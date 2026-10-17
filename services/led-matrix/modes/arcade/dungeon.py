"""Autonomous first-person raycast dungeon crawl on 64x64 RGB matrix."""

from __future__ import annotations

import math
import random
from collections import deque

from PIL import Image, ImageDraw

from .base import ArcadeGame

GRID = 21
FOV = 0.66
MOVE_SPEED = 1.7
TURN_RATE = 7.0

CEILING_TOP = (8, 10, 20)
CEILING_BOT = (26, 32, 52)
FLOOR_TOP = (30, 28, 34)
FLOOR_BOT = (10, 9, 12)

WALL_X = (74, 96, 140)
WALL_Y = (54, 70, 106)


def _carve_maze(seed: int) -> list[list[int]]:
    rng = random.Random(seed)
    grid = [[1] * GRID for _ in range(GRID)]
    stack = [(1, 1)]
    grid[1][1] = 0
    while stack:
        cx, cy = stack[-1]
        neighbors = []
        for dx, dy in ((2, 0), (-2, 0), (0, 2), (0, -2)):
            nx, ny = cx + dx, cy + dy
            if 1 <= nx < GRID - 1 and 1 <= ny < GRID - 1 and grid[ny][nx] == 1:
                neighbors.append((nx, ny, dx, dy))
        if not neighbors:
            stack.pop()
            continue
        nx, ny, dx, dy = rng.choice(neighbors)
        grid[cy + dy // 2][cx + dx // 2] = 0
        grid[ny][nx] = 0
        stack.append((nx, ny))
    return grid


class DungeonGame(ArcadeGame):
    name = "DUNGEON"

    def __init__(self) -> None:
        self.ceiling = self._gradient(0, 32, CEILING_TOP, CEILING_BOT)
        self.floor = self._gradient(32, 64, FLOOR_TOP, FLOOR_BOT)
        self.reset()

    @staticmethod
    def _gradient(y0: int, y1: int, top: tuple[int, int, int], bot: tuple[int, int, int]) -> list[tuple[int, int, int]]:
        span = max(1, y1 - y0 - 1)
        out = []
        for i in range(y1 - y0):
            t = i / span
            out.append(
                (
                    int(top[0] + (bot[0] - top[0]) * t),
                    int(top[1] + (bot[1] - top[1]) * t),
                    int(top[2] + (bot[2] - top[2]) * t),
                )
            )
        return out

    def reset(self) -> None:
        self.grid = _carve_maze(random.randrange(1 << 30))
        self.px, self.py = 1.5, 1.5
        self.angle = 0.0
        self.z_buffer = [1e9] * 64
        self.path: list[tuple[int, int]] = []
        self.goal = (1, 1)
        self._pick_goal()
        self.pulse = 0.0

    def _open_cells(self) -> list[tuple[int, int]]:
        return [
            (x, y)
            for y in range(1, GRID - 1)
            for x in range(1, GRID - 1)
            if self.grid[y][x] == 0
        ]

    def _pick_goal(self) -> None:
        start = (int(self.px), int(self.py))
        cells = self._open_cells()
        candidates = [c for c in cells if c != start]
        goal = random.choice(candidates) if candidates else start
        self.goal = goal
        self.path = self._bfs(start, goal)

    def _bfs(self, start: tuple[int, int], goal: tuple[int, int]) -> list[tuple[int, int]]:
        if start == goal:
            return []
        prev: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
        queue = deque([start])
        while queue:
            cell = queue.popleft()
            if cell == goal:
                break
            cx, cy = cell
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = (cx + dx, cy + dy)
                if self.grid[nxt[1]][nxt[0]] == 0 and nxt not in prev:
                    prev[nxt] = cell
                    queue.append(nxt)
        if goal not in prev:
            return []
        path = []
        cell: tuple[int, int] | None = goal
        while cell is not None and cell != start:
            path.append(cell)
            cell = prev[cell]
        path.reverse()
        return path

    def update(self, dt: float) -> None:
        dt = min(dt, 0.05)
        self.pulse += dt
        if not self.path:
            self._pick_goal()
            if not self.path:
                return

        target = self.path[0]
        tx, ty = target[0] + 0.5, target[1] + 0.5
        desired = math.atan2(ty - self.py, tx - self.px)

        diff = (desired - self.angle + math.pi) % (2 * math.pi) - math.pi
        self.angle += diff * min(1.0, TURN_RATE * dt)
        self.angle = (self.angle + math.pi) % (2 * math.pi) - math.pi

        step = MOVE_SPEED * dt
        dist = math.hypot(tx - self.px, ty - self.py)
        if dist <= step:
            self.px, self.py = tx, ty
            self.path.pop(0)
            if not self.path:
                self._pick_goal()
        else:
            self.px += (tx - self.px) / dist * step
            self.py += (ty - self.py) / dist * step

    def _cast(self, ray_x: float, ray_y: float) -> tuple[float, int, float]:
        map_x, map_y = int(self.px), int(self.py)
        delta_x = 1e9 if ray_x == 0 else abs(1.0 / ray_x)
        delta_y = 1e9 if ray_y == 0 else abs(1.0 / ray_y)
        if ray_x < 0:
            step_x, side_x = -1, (self.px - map_x) * delta_x
        else:
            step_x, side_x = 1, (map_x + 1 - self.px) * delta_x
        if ray_y < 0:
            step_y, side_y = -1, (self.py - map_y) * delta_y
        else:
            step_y, side_y = 1, (map_y + 1 - self.py) * delta_y
        side = 0
        while True:
            if side_x < side_y:
                side_x += delta_x
                map_x += step_x
                side = 0
            else:
                side_y += delta_y
                map_y += step_y
                side = 1
            if not (0 <= map_x < GRID and 0 <= map_y < GRID):
                return 1e9, side, 0.0
            if self.grid[map_y][map_x] == 1:
                break
        if side == 0:
            dist = side_x - delta_x
            wall_x = self.py + dist * ray_y
        else:
            dist = side_y - delta_y
            wall_x = self.px + dist * ray_x
        return max(dist, 1e-4), side, wall_x - math.floor(wall_x)

    def draw(
        self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64
    ) -> None:
        for y in range(32):
            draw.line([(0, y), (size - 1, y)], fill=self.ceiling[y])
        for y in range(32, size):
            draw.line([(0, y), (size - 1, y)], fill=self.floor[y - 32])

        dir_x, dir_y = math.cos(self.angle), math.sin(self.angle)
        plane_x, plane_y = -dir_y * FOV, dir_x * FOV

        for x in range(size):
            camera = 2.0 * x / size - 1.0
            ray_x = dir_x + plane_x * camera
            ray_y = dir_y + plane_y * camera
            dist, side, wall_x = self._cast(ray_x, ray_y)
            self.z_buffer[x] = dist
            line_h = int(size / dist)
            start = max(0, 32 - line_h // 2)
            end = min(size - 1, 32 + line_h // 2)
            base = WALL_X if side == 0 else WALL_Y
            stripe = 1.0 if int(wall_x * 8) % 2 == 0 else 0.84
            shade = max(0.22, 1.0 - dist / 13.0) * stripe
            color = (
                int(base[0] * shade),
                int(base[1] * shade),
                int(base[2] * shade),
            )
            draw.line([(x, start), (x, end)], fill=color)
            seam = (int(color[0] * 0.55), int(color[1] * 0.55), int(color[2] * 0.55))
            draw.point((x, start), fill=seam)
            draw.point((x, end), fill=seam)

        self._draw_orb(draw, size, dir_x, dir_y, plane_x, plane_y)

    def _wisp(self, dir_x: float, dir_y: float) -> tuple[float, float]:
        dist = 1.2
        step = 0.25
        while dist < 5.0:
            nx = int(self.px + dir_x * (dist + step))
            ny = int(self.py + dir_y * (dist + step))
            if not (0 <= nx < GRID and 0 <= ny < GRID) or self.grid[ny][nx] == 1:
                break
            dist += step
        return self.px + dir_x * dist, self.py + dir_y * dist

    def _draw_orb(
        self,
        draw: ImageDraw.ImageDraw,
        size: int,
        dir_x: float,
        dir_y: float,
        plane_x: float,
        plane_y: float,
    ) -> None:
        world_x, world_y = self._wisp(dir_x, dir_y)
        sprite_x = world_x - self.px
        sprite_y = world_y - self.py
        det = plane_x * dir_y - dir_x * plane_y
        if abs(det) < 1e-6:
            return
        inv = 1.0 / det
        transform_x = inv * (dir_y * sprite_x - dir_x * sprite_y)
        transform_y = inv * (-plane_y * sprite_x + plane_x * sprite_y)
        if transform_y <= 0.05:
            return
        screen_x = int((size // 2) * (1.0 + transform_x / transform_y))
        radius = max(2, min(14, int(12.0 / transform_y)))
        center_y = 31 + int(2.5 * math.sin(self.pulse * 3.0))
        glow = 0.6 + 0.4 * math.sin(self.pulse * 6.0)
        color = (int(80 * glow), int(240 * glow), int(160 * glow))
        for dy in range(-radius, radius + 1):
            y = center_y + dy
            if not (0 <= y < size):
                continue
            span = int(math.sqrt(max(0, radius * radius - dy * dy)))
            for dx in range(-span, span + 1):
                x = screen_x + dx
                if 0 <= x < size and transform_y < self.z_buffer[x]:
                    draw.point((x, y), fill=color)
