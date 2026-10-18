"""Autonomous AI Donkey Kong arcade simulation on 64x64 RGB matrix."""

from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw, ImageFont

from modes.text_utils import draw_centered_text, draw_scoreboard

from .base import ArcadeGame


class DonkeyKongGame(ArcadeGame):
    name = "DONKEY KONG"

    def __init__(self) -> None:
        self.font = ImageFont.load_default(size=9)
        self.score = 0
        self.lives = 3
        self.level = 1
        self.reset()

    def reset(self) -> None:
        # Donkey Kong state
        self.dk_anim_timer = 0.0
        self.dk_frame = 0  # 0: chest beat, 1: barrel toss
        self.barrel_spawn_timer = 2.0

        # Barrels list: dict of x, y, tier, dir (+1 or -1), falling (bool), rot
        self.barrels: list[dict] = []

        # Jumpman (Mario)
        self.mario_x = 4.0
        self.mario_y = 57.0  # Ground level on Tier 3
        self.mario_tier = 3  # 3: bottom, 2: mid-low, 1: mid-high, 0: top
        self.mario_vx = 18.0
        self.mario_state = "run"  # "run", "climb", "jump"
        self.jump_t = 0.0
        self.jump_origin_y = 57.0
        self.face_dir = 1

        # Pauline & Hearts
        self.pauline_x = 28.0
        self.pauline_y = 15.0
        self.heart_timer = 0.0
        self.won = False
        self.win_timer = 0.0

        self.popups: list[dict] = []

    def _get_girder_y(self, tier: int, x: float) -> float:
        """Calculate surface y-coordinate on sloped girder tier."""
        if tier == 0:
            return 15.0
        elif tier == 1:
            # Slopes down to the right (x: 4..60 -> y: 28..32)
            t = max(0.0, min(1.0, (x - 4.0) / 56.0))
            return 28.0 + t * 4.0
        elif tier == 2:
            # Slopes down to the left (x: 4..60 -> y: 44..40)
            t = max(0.0, min(1.0, (x - 4.0) / 56.0))
            return 44.0 - t * 4.0
        else:
            # Bottom tier: flat floor
            return 57.0

    def update(self, dt: float) -> None:
        dt = min(dt, 0.05)
        self.dk_anim_timer += dt
        if self.dk_anim_timer >= 0.25:
            self.dk_anim_timer = 0.0
            self.dk_frame = 1 - self.dk_frame

        self.heart_timer += dt

        # Update floating score popups
        for pop in list(self.popups):
            pop["life"] -= dt
            pop["y"] -= dt * 6.0
            if pop["life"] <= 0:
                self.popups.remove(pop)

        # Win sequence
        if self.won:
            self.win_timer -= dt
            if self.win_timer <= 0:
                self.level += 1
                self.score += 500
                self.reset()
            return

        # Barrel Spawning by Donkey Kong
        self.barrel_spawn_timer -= dt
        if self.barrel_spawn_timer <= 0:
            self.barrel_spawn_timer = random.uniform(2.5, 3.8)
            self.barrels.append(
                {
                    "x": 20.0,
                    "y": self._get_girder_y(1, 20.0),
                    "tier": 1,
                    "dir": 1,
                    "falling": False,
                    "jumped": False,
                    "rot": 0,
                }
            )

        # Update Barrels
        for b in list(self.barrels):
            b["rot"] = (b["rot"] + 1) % 4
            if b["falling"]:
                b["y"] += 30.0 * dt
                target_y = self._get_girder_y(b["tier"], b["x"])
                if b["y"] >= target_y:
                    b["y"] = target_y
                    b["falling"] = False
                    # Reverse direction on lower tier
                    b["dir"] = -b["dir"]
            else:
                b["x"] += b["dir"] * 22.0 * dt
                b["y"] = self._get_girder_y(b["tier"], b["x"])

                # Check tier edges to drop to next tier down
                if b["tier"] == 1 and b["x"] >= 58.0:
                    b["tier"] = 2
                    b["falling"] = True
                elif b["tier"] == 2 and b["x"] <= 6.0:
                    b["tier"] = 3
                    b["falling"] = True
                elif b["tier"] == 3 and b["x"] >= 66.0:
                    self.barrels.remove(b)
                    continue

        # Autonomous AI Jumpman (Mario)
        self._ai_mario(dt)

        # Check Barrel Collisions & Jumping bonus
        for b in self.barrels:
            dx = abs(self.mario_x - b["x"])
            dy = abs(self.mario_y - b["y"])

            # Jump over barrel bonus (+100 pts)
            if (
                self.mario_state == "jump"
                and not b["jumped"]
                and dx < 5.0
                and self.mario_y < b["y"]
            ):
                b["jumped"] = True
                self.score += 100
                self.popups.append(
                    {
                        "x": self.mario_x,
                        "y": self.mario_y - 6,
                        "text": "+100",
                        "life": 0.8,
                    }
                )

            # Lethal collision
            if (
                dx < 3.2
                and dy < 3.2
                and not (self.mario_state == "jump" and self.mario_y < b["y"] - 3)
            ):
                self.lives -= 1
                if self.lives <= 0:
                    self.score = 0
                    self.level = 1
                    self.lives = 3
                self.reset()
                return

    def _ai_mario(self, dt: float) -> None:
        """Mario navigates girders, climbs ladders, and cleanly jumps over incoming barrels."""
        # Ladders connecting tiers:
        # Tier 3 -> Tier 2 at x=50.0
        # Tier 2 -> Tier 1 at x=14.0
        # Tier 1 -> Tier 0 at x=36.0 (Pauline's platform)
        ladder_targets = {
            3: 50.0,
            2: 14.0,
            1: 36.0,
            0: self.pauline_x,
        }

        target_x = ladder_targets[self.mario_tier]

        # Check for immediate jump trigger on approaching barrel
        if self.mario_state == "run":
            for b in self.barrels:
                if b["tier"] == self.mario_tier and not b["falling"]:
                    dist = b["x"] - self.mario_x
                    # If barrel is moving towards Mario and within 10px
                    if (b["dir"] > 0 and -10.0 < dist < -1.5) or (
                        b["dir"] < 0 and 1.5 < dist < 10.0
                    ):
                        self.mario_state = "jump"
                        self.jump_t = 0.0
                        self.jump_origin_y = self.mario_y
                        break

        # State machine
        if self.mario_state == "jump":
            self.jump_t += dt * 3.8
            # Jump forward in current facing direction
            self.mario_x += self.face_dir * 16.0 * dt
            ground_y = self._get_girder_y(self.mario_tier, self.mario_x)
            # High 10px parabolic arc for clean barrel clearance
            self.mario_y = ground_y - 10.0 * math.sin(
                min(math.pi, self.jump_t * math.pi)
            )
            if self.jump_t >= 1.0:
                self.mario_state = "run"
                self.mario_y = ground_y

        elif self.mario_state == "climb":
            self.mario_y -= 15.0 * dt
            dest_tier = self.mario_tier - 1
            dest_y = self._get_girder_y(dest_tier, self.mario_x)
            if self.mario_y <= dest_y:
                self.mario_y = dest_y
                self.mario_tier = dest_tier
                self.mario_state = "run"
                if self.mario_tier == 0:
                    # Won! Reached Pauline
                    self.won = True
                    self.win_timer = 2.0

        elif self.mario_state == "run":
            diff = target_x - self.mario_x
            if abs(diff) > 0.8:
                self.face_dir = 1 if diff > 0 else -1
                self.mario_x += self.face_dir * self.mario_vx * dt
                self.mario_y = self._get_girder_y(self.mario_tier, self.mario_x)
            else:
                # Aligned with ladder!
                if self.mario_tier > 0:
                    self.mario_state = "climb"

    def draw(
        self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64
    ) -> None:
        draw.rectangle([(0, 0), (size - 1, size - 1)], fill=(2, 4, 12))

        # Top HUD: Score & Donkey Kong title
        draw_scoreboard(
            draw, f"S{self.score}", "DK 1981", size=size, right_color=(255, 120, 160)
        )

        # Girders (Steel platforms in classic arcade red/magenta with depth shadow)
        girder_col = (225, 40, 80)
        girder_shadow = (140, 20, 45)

        # Donkey Kong Platform: x: 4..22, y: 19
        draw.line([(4, 19), (22, 19)], fill=girder_col, width=1)
        draw.line([(4, 20), (22, 20)], fill=girder_shadow, width=1)

        # Pauline Perch: x: 24..44, y: 15
        draw.line([(24, 15), (44, 15)], fill=girder_col, width=1)
        draw.line([(24, 16), (44, 16)], fill=girder_shadow, width=1)

        # Tier 1 (slope down right): (4, 28) to (60, 32)
        draw.line([(4, 28), (60, 32)], fill=girder_col, width=1)
        draw.line([(4, 29), (60, 33)], fill=girder_shadow, width=1)

        # Tier 2 (slope down left): (60, 40) to (4, 44)
        draw.line([(60, 40), (4, 44)], fill=girder_col, width=1)
        draw.line([(60, 41), (4, 45)], fill=girder_shadow, width=1)

        # Tier 3 (bottom floor): (0, 57) to (63, 57)
        draw.line([(0, 57), (63, 57)], fill=girder_col, width=1)
        draw.line([(0, 58), (63, 58)], fill=girder_shadow, width=1)

        # Blue Ladders connecting tiers cleanly
        ladder_col = (50, 180, 255)
        # Ladder 3: connects Tier 3 to Tier 2 at x=50
        self._draw_ladder(draw, 50, 41, 57, ladder_col)
        # Ladder 2: connects Tier 2 to Tier 1 at x=14
        self._draw_ladder(draw, 14, 29, 43, ladder_col)
        # Ladder 1: connects Tier 1 to Pauline Perch at x=36
        self._draw_ladder(draw, 36, 15, 30, ladder_col)

        # Draw Pauline standing on perch (x=28, y=15)
        px, py = int(self.pauline_x), int(self.pauline_y)
        # Blonde hair & face
        draw.rectangle([(px + 1, py - 7), (px + 3, py - 6)], fill=(255, 230, 80))
        draw.rectangle([(px + 1, py - 5), (px + 3, py - 5)], fill=(255, 200, 140))
        # Pink dress & shoes
        draw.rectangle([(px, py - 4), (px + 4, py - 2)], fill=(255, 130, 190))
        draw.rectangle([(px + 1, py - 1), (px + 3, py - 1)], fill=(220, 50, 120))
        # Beating heart animation above Pauline
        if int(self.heart_timer * 3) % 2 == 0 or self.won:
            draw.polygon(
                [
                    (px - 2, py - 11),
                    (px - 1, py - 12),
                    (px, py - 11),
                    (px + 1, py - 12),
                    (px + 2, py - 11),
                    (px, py - 8),
                ],
                fill=(255, 50, 130),
            )

        # Draw Donkey Kong (top left, standing on y=19 platform)
        dk_x = 8
        dk_y = 10
        # Gorilla brown body
        draw.rectangle([(dk_x, dk_y + 3), (dk_x + 9, dk_y + 8)], fill=(170, 75, 20))
        # Peach chest / face
        draw.rectangle(
            [(dk_x + 2, dk_y + 4), (dk_x + 6, dk_y + 7)], fill=(255, 190, 130)
        )
        # Head & eyes
        draw.rectangle([(dk_x + 2, dk_y), (dk_x + 7, dk_y + 3)], fill=(170, 75, 20))
        draw.point((dk_x + 3, dk_y + 1), fill=(255, 255, 255))
        draw.point((dk_x + 6, dk_y + 1), fill=(255, 255, 255))
        # Arms animation (chest beating)
        if self.dk_frame == 0:
            draw.line(
                [(dk_x - 1, dk_y + 4), (dk_x, dk_y + 6)], fill=(170, 75, 20), width=1
            )
            draw.line(
                [(dk_x + 10, dk_y + 4), (dk_x + 9, dk_y + 6)],
                fill=(170, 75, 20),
                width=1,
            )
        else:
            draw.line(
                [(dk_x - 1, dk_y + 2), (dk_x, dk_y + 4)], fill=(170, 75, 20), width=1
            )
            draw.line(
                [(dk_x + 10, dk_y + 2), (dk_x + 9, dk_y + 4)],
                fill=(170, 75, 20),
                width=1,
            )

        # Draw Rolling Barrels (strictly on top of girder surface)
        for b in self.barrels:
            bx, by = int(b["x"]), int(b["y"])
            draw.rectangle([(bx - 2, by - 4), (bx + 1, by - 1)], fill=(195, 110, 25))
            # Rotating cross highlight
            if b["rot"] % 2 == 0:
                draw.point((bx - 1, by - 3), fill=(255, 190, 80))
                draw.point((bx, by - 2), fill=(255, 190, 80))
            else:
                draw.point((bx, by - 3), fill=(255, 190, 80))
                draw.point((bx - 1, by - 2), fill=(255, 190, 80))

        # Draw Jumpman (Mario) - strictly on top of girder surface at y=my
        mx, my = int(self.mario_x), int(self.mario_y)
        # Red Cap
        draw.rectangle([(mx - 1, my - 7), (mx + 2, my - 6)], fill=(240, 30, 30))
        # Face (skin tone) & Eye
        draw.rectangle([(mx, my - 5), (mx + 2, my - 5)], fill=(255, 200, 140))
        draw.point((mx + (1 if self.face_dir > 0 else 0), my - 5), fill=(30, 30, 30))
        # Blue Shirt
        draw.rectangle([(mx - 1, my - 4), (mx + 2, my - 3)], fill=(30, 70, 220))
        # Red Overalls
        draw.rectangle([(mx - 1, my - 2), (mx + 2, my - 2)], fill=(240, 30, 30))
        # Feet / Boots
        if self.mario_state == "jump":
            draw.point((mx - 2, my - 3), fill=(160, 80, 20))
            draw.point((mx + 3, my - 3), fill=(160, 80, 20))
        elif self.mario_state == "climb":
            alt = int(self.mario_y * 0.8) % 2
            draw.point((mx - 1, my - (1 if alt == 0 else 2)), fill=(160, 80, 20))
            draw.point((mx + 2, my - (2 if alt == 0 else 1)), fill=(160, 80, 20))
        else:
            draw.point((mx - 1, my - 1), fill=(160, 80, 20))
            draw.point((mx + 2, my - 1), fill=(160, 80, 20))

        # Popups
        for pop in self.popups:
            draw_centered_text(
                draw,
                pop["text"],
                min(52, max(12, int(pop["x"]))),
                min(59, max(13, int(pop["y"]))),
                fill=(255, 240, 60),
                max_width=24,
                max_height=7,
                preferred_size=7,
            )

    def _draw_ladder(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y1: int,
        y2: int,
        col: tuple[int, int, int],
    ) -> None:
        draw.line([(x - 2, y1), (x - 2, y2)], fill=col, width=1)
        draw.line([(x + 2, y1), (x + 2, y2)], fill=col, width=1)
        for ry in range(y1 + 2, y2, 3):
            draw.line([(x - 2, ry), (x + 2, ry)], fill=col, width=1)
