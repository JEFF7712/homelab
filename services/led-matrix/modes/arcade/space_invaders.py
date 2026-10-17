"""Autonomous AI Space Invaders arcade simulation on 64x64 RGB matrix."""

from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw, ImageFont

from .base import ArcadeGame

COLS = 6
ROWS = 4
INV_W = 5
INV_H = 4
SPACING_X = 8
SPACING_Y = 6


class SpaceInvadersGame(ArcadeGame):
    name = "SPACE INVADERS"

    def __init__(self) -> None:
        self.font = ImageFont.load_default(size=9)
        self.score = 0
        self.wave = 1
        self.lives = 3
        self.reset()

    def reset(self) -> None:
        self.fleet_x = 4.0
        self.fleet_y = 12.0
        self.fleet_dir = 1.0
        self.step_timer = 0.0
        self.anim_frame = 0

        # Invader grid: True if alive
        self.invaders = [[True] * COLS for _ in range(ROWS)]

        # Cannon state
        self.cannon_x = 28.0
        self.cannon_y = 57.0
        self.cannon_speed = 36.0

        # Bullets & Bombs
        self.bullet: tuple[float, float] | None = None
        self.bombs: list[list[float]] = []  # [x, y, vy]

        # UFO / Mystery Saucer
        self.ufo_x: float | None = None
        self.ufo_timer = random.uniform(10.0, 16.0)

        # Defense Bunkers (bitmask sets of (bx, by))
        self.bunkers: set[tuple[int, int]] = set()
        self._build_bunkers()

        # Explosions & Popups
        self.explosions: list[dict] = []
        self.popups: list[dict] = []

    def _build_bunkers(self) -> None:
        self.bunkers.clear()
        bunker_x_coords = [8, 27, 46]
        for bx in bunker_x_coords:
            for dy in range(5):
                for dx in range(9):
                    # Arch cutout at bottom center
                    if dy >= 3 and 3 <= dx <= 5:
                        continue
                    # Rounded top corners
                    if dy == 0 and (dx == 0 or dx == 8):
                        continue
                    self.bunkers.add((bx + dx, 48 + dy))

    @property
    def alive_count(self) -> int:
        return sum(1 for row in self.invaders for cell in row if cell)

    def _damage_bunker(self, cx: int, cy: int, radius: int = 1) -> None:
        to_remove = set()
        for bx, by in self.bunkers:
            if math.hypot(bx - cx, by - cy) <= radius:
                to_remove.add((bx, by))
        self.bunkers -= to_remove

    def _fire_bullet(self) -> None:
        if self.bullet is None:
            self.bullet = (self.cannon_x + 3.0, self.cannon_y - 2.0)

    def update(self, dt: float) -> None:
        dt = min(dt, 0.05)

        # UFO Logic
        if self.ufo_x is not None:
            self.ufo_x += 24.0 * dt
            if self.ufo_x > 66.0:
                self.ufo_x = None
                self.ufo_timer = random.uniform(12.0, 18.0)
        else:
            self.ufo_timer -= dt
            if self.ufo_timer <= 0:
                self.ufo_x = -10.0

        # Update explosions & popups
        for exp in list(self.explosions):
            exp["life"] -= dt
            if exp["life"] <= 0:
                self.explosions.remove(exp)

        for pop in list(self.popups):
            pop["life"] -= dt
            pop["y"] -= dt * 6.0
            if pop["life"] <= 0:
                self.popups.remove(pop)

        # Fleet marching step
        alive = self.alive_count
        if alive == 0:
            self.wave += 1
            self.score += 500
            self.reset()
            return

        # Speed increases as invaders are cleared
        step_interval = max(0.08, 0.38 * (alive / (COLS * ROWS)))
        self.step_timer += dt
        if self.step_timer >= step_interval:
            self.step_timer = 0.0
            self.anim_frame = 1 - self.anim_frame

            # Find bounding box of alive invaders
            min_c = COLS
            max_c = -1
            max_r = -1
            for r in range(ROWS):
                for c in range(COLS):
                    if self.invaders[r][c]:
                        min_c = min(min_c, c)
                        max_c = max(max_c, c)
                        max_r = max(max_r, r)

            fleet_left = self.fleet_x + min_c * SPACING_X
            fleet_right = self.fleet_x + max_c * SPACING_X + INV_W
            fleet_bottom = self.fleet_y + max_r * SPACING_Y + INV_H

            # Check boundary hit
            if (self.fleet_dir > 0 and fleet_right >= 61) or (
                self.fleet_dir < 0 and fleet_left <= 2
            ):
                self.fleet_dir = -self.fleet_dir
                self.fleet_y += 3.0
            else:
                self.fleet_x += self.fleet_dir * 2.0

            # Invaders reach bottom
            if fleet_bottom >= 55:
                self.lives -= 1
                if self.lives <= 0:
                    self.score = 0
                    self.wave = 1
                    self.lives = 3
                self.reset()
                return

            # Random bomb drops from bottom-most alive invaders
            if random.random() < 0.45:
                drop_candidates = []
                for c in range(COLS):
                    for r in range(ROWS - 1, -1, -1):
                        if self.invaders[r][c]:
                            drop_candidates.append((r, c))
                            break
                if drop_candidates:
                    drop_r, drop_c = random.choice(drop_candidates)
                    bx = self.fleet_x + drop_c * SPACING_X + 2.0
                    by = self.fleet_y + drop_r * SPACING_Y + INV_H
                    self.bombs.append([bx, by, 34.0])

        # Bullet update
        if self.bullet is not None:
            bx, by = self.bullet
            by -= 80.0 * dt
            if by < 9:
                self.bullet = None
            else:
                self.bullet = (bx, by)
                # Check UFO collision
                if (
                    self.ufo_x is not None
                    and abs(bx - (self.ufo_x + 4)) < 5
                    and abs(by - 10) < 3
                ):
                    pts = random.choice([100, 200, 300])
                    self.score += pts
                    self.popups.append(
                        {"x": bx, "y": by, "text": f"{pts}", "life": 0.8}
                    )
                    self.explosions.append(
                        {"x": bx, "y": by, "col": (255, 40, 50), "life": 0.3}
                    )
                    self.ufo_x = None
                    self.ufo_timer = random.uniform(14.0, 20.0)
                    self.bullet = None
                # Check bunker collision
                elif (round(bx), round(by)) in self.bunkers:
                    self._damage_bunker(round(bx), round(by), radius=2)
                    self.bullet = None
                # Check invader collision
                else:
                    for r in range(ROWS):
                        for c in range(COLS):
                            if not self.invaders[r][c]:
                                continue
                            ix = self.fleet_x + c * SPACING_X
                            iy = self.fleet_y + r * SPACING_Y
                            if ix <= bx <= ix + INV_W and iy <= by <= iy + INV_H:
                                self.invaders[r][c] = False
                                self.bullet = None
                                pts = [40, 20, 20, 10][r]
                                self.score += pts
                                self.explosions.append(
                                    {
                                        "x": ix + 2,
                                        "y": iy + 2,
                                        "col": (255, 255, 255),
                                        "life": 0.25,
                                    }
                                )
                                break
                        if self.bullet is None:
                            break

        # Bombs update
        for bomb in list(self.bombs):
            bomb[1] += bomb[2] * dt
            bx, by = round(bomb[0]), round(bomb[1])

            # Hit bunker
            if (bx, by) in self.bunkers:
                self._damage_bunker(bx, by, radius=2)
                self.bombs.remove(bomb)
            # Hit player cannon
            elif 56 <= by <= 61 and abs(bomb[0] - (self.cannon_x + 3)) < 4:
                self.explosions.append(
                    {
                        "x": self.cannon_x + 3,
                        "y": self.cannon_y + 2,
                        "col": (255, 200, 50),
                        "life": 0.5,
                    }
                )
                self.lives -= 1
                self.bombs.remove(bomb)
                if self.lives <= 0:
                    self.score = 0
                    self.wave = 1
                    self.lives = 3
                    self.reset()
                    return
                self.cannon_x = 28.0
                return
            elif bomb[1] > 63:
                self.bombs.remove(bomb)

        # Autonomous AI Cannon
        self._ai_cannon(dt)

    def _ai_cannon(self, dt: float) -> None:
        """Intelligent AI cannon that hunts UFOs, shoots columns, and dodges bombs."""
        target_x = self.cannon_x

        # Priority 1: Target UFO if present
        if self.ufo_x is not None:
            target_x = self.ufo_x + 1.0

        # Priority 2: Dodge falling bombs
        threat = None
        for b in self.bombs:
            if b[1] > 36.0 and abs(b[0] - (self.cannon_x + 3)) < 5.5:
                threat = b
                break

        if threat is not None:
            # Move away from threat
            if threat[0] < self.cannon_x + 3:
                target_x = min(56.0, self.cannon_x + 12.0)
            else:
                target_x = max(1.0, self.cannon_x - 12.0)
        elif self.ufo_x is None:
            # Target column with lowest invaders
            cols_alive = []
            for c in range(COLS):
                for r in range(ROWS - 1, -1, -1):
                    if self.invaders[r][c]:
                        cols_alive.append((c, r))
                        break
            if cols_alive:
                # Prefer columns closest to cannon
                cols_alive.sort(
                    key=lambda item: abs(
                        (self.fleet_x + item[0] * SPACING_X + 2) - self.cannon_x
                    )
                )
                best_c = cols_alive[0][0]
                target_x = self.fleet_x + best_c * SPACING_X

        # Move cannon towards target_x
        diff = target_x - self.cannon_x
        if abs(diff) > 0.8:
            step = math.copysign(min(abs(diff), self.cannon_speed * dt), diff)
            self.cannon_x = max(1.0, min(56.0, self.cannon_x + step))

        # Auto-fire bullet when aligned with an invader or UFO
        if self.bullet is None:
            aligned = False
            if self.ufo_x is not None and abs(self.cannon_x + 3 - (self.ufo_x + 4)) < 4:
                aligned = True
            else:
                for c in range(COLS):
                    col_x = self.fleet_x + c * SPACING_X + 2
                    if abs(self.cannon_x + 3 - col_x) < 3.5 and any(
                        self.invaders[r][c] for r in range(ROWS)
                    ):
                        aligned = True
                        break
            if aligned:
                self._fire_bullet()

    def draw(
        self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64
    ) -> None:
        draw.rectangle([(0, 0), (size - 1, size - 1)], fill=(2, 4, 10))

        # Top HUD
        draw.text((2, 0), f"S{self.score}", fill=(240, 240, 240), font=self.font)
        draw.text((size - 22, 0), f"W{self.wave}", fill=(100, 240, 120), font=self.font)

        # Draw UFO
        if self.ufo_x is not None:
            ux = int(self.ufo_x)
            draw.rectangle([(ux + 2, 9), (ux + 5, 9)], fill=(255, 255, 255))
            draw.rectangle([(ux, 10), (ux + 7, 11)], fill=(255, 40, 50))
            draw.point((ux + 2, 12), fill=(255, 200, 40))
            draw.point((ux + 5, 12), fill=(255, 200, 40))

        # Draw Invaders
        colors = [
            (255, 80, 220),  # Squid
            (0, 230, 255),  # Crab
            (0, 230, 255),  # Crab
            (60, 255, 100),  # Octopus
        ]

        for r in range(ROWS):
            col = colors[r]
            for c in range(COLS):
                if not self.invaders[r][c]:
                    continue
                ix = int(self.fleet_x + c * SPACING_X)
                iy = int(self.fleet_y + r * SPACING_Y)

                # 5x4 Invader Sprite with 2-frame animation
                draw.rectangle([(ix + 1, iy + 1), (ix + 3, iy + 2)], fill=col)
                draw.point((ix + 1, iy), fill=col)
                draw.point((ix + 3, iy), fill=col)

                if self.anim_frame == 0:
                    # Feet out
                    draw.point((ix, iy + 3), fill=col)
                    draw.point((ix + 4, iy + 3), fill=col)
                    draw.point((ix, iy + 1), fill=col)
                    draw.point((ix + 4, iy + 1), fill=col)
                else:
                    # Feet down/in
                    draw.point((ix + 1, iy + 3), fill=col)
                    draw.point((ix + 3, iy + 3), fill=col)
                    draw.point((ix, iy + 2), fill=col)
                    draw.point((ix + 4, iy + 2), fill=col)

        # Draw Defense Bunkers
        for bx, by in self.bunkers:
            draw.point((bx, by), fill=(40, 220, 80))

        # Draw Cannon
        cx = int(self.cannon_x)
        cy = int(self.cannon_y)
        draw.rectangle([(cx, cy + 2), (cx + 6, cy + 4)], fill=(70, 240, 70))
        draw.rectangle([(cx + 2, cy + 1), (cx + 4, cy + 1)], fill=(70, 240, 70))
        draw.point((cx + 3, cy), fill=(255, 255, 255))

        # Draw Bullet
        if self.bullet is not None:
            bx, by = int(self.bullet[0]), int(self.bullet[1])
            draw.line([(bx, by), (bx, by + 2)], fill=(255, 255, 255), width=1)

        # Draw Bombs
        for bomb in self.bombs:
            bx, by = int(bomb[0]), int(bomb[1])
            draw.line([(bx, by), (bx, by + 2)], fill=(255, 210, 40), width=1)

        # Explosions
        for exp in self.explosions:
            ex, ey = int(exp["x"]), int(exp["y"])
            c = exp["col"]
            draw.point((ex, ey), fill=c)
            draw.point((ex - 1, ey), fill=c)
            draw.point((ex + 1, ey), fill=c)
            draw.point((ex, ey - 1), fill=c)
            draw.point((ex, ey + 1), fill=c)

        # Popups
        for pop in self.popups:
            draw.text(
                (int(pop["x"]), int(pop["y"])),
                pop["text"],
                fill=(255, 255, 80),
                font=self.font,
            )
