"""Autonomous AI Frogger arcade simulation on 64x64 RGB matrix."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from PIL import Image, ImageDraw, ImageFont

from modes.text_utils import draw_right_aligned_text
from .base import ArcadeGame


@dataclass
class Obstacle:
    x: float
    y: int
    w: int
    h: int
    speed: float  # px/s (+ right, - left)
    color: tuple[int, int, int]
    is_log: bool = False
    is_turtle: bool = False


class FroggerGame(ArcadeGame):
    name = "FROGGER"

    LILY_HOMES_X = [7, 19, 31, 43, 55]
    LILY_Y = 11

    def __init__(self) -> None:
        self.font = ImageFont.load_default(size=9)
        self.score = 0
        self.lives = 3
        self.level = 1
        self.frog_x = 31.0
        self.frog_y = 59.0
        self.homes_filled = [False] * 5
        self.obstacles: list[Obstacle] = []
        self.splat_timer = 0.0
        self.level_clear_timer = 0.0
        self.hop_cooldown = 0.0
        self.anim_phase = 0.0
        self.reset()

    def reset(self) -> None:
        self.score = 0
        self.lives = 3
        self.level = 1
        self.homes_filled = [False] * 5
        self.splat_timer = 0.0
        self.level_clear_timer = 0.0
        self.hop_cooldown = 0.0
        self.anim_phase = 0.0
        self._respawn_frog()
        self._init_obstacles()

    def _respawn_frog(self) -> None:
        self.frog_x = 31.0
        self.frog_y = 59.0
        self.hop_cooldown = 0.15

    def _init_obstacles(self) -> None:
        self.obstacles.clear()
        spd_scale = 1.0 + (self.level - 1) * 0.15

        # --- River Lanes (Y=16..35) ---
        # Lane 1 (y=16): Small logs ->
        for x in (4, 28, 52):
            self.obstacles.append(Obstacle(x=float(x), y=16, w=14, h=4, speed=16.0 * spd_scale, color=(140, 85, 30), is_log=True))

        # Lane 2 (y=21): Turtles <-
        for x in (8, 30, 52):
            self.obstacles.append(Obstacle(x=float(x), y=21, w=11, h=4, speed=-18.0 * spd_scale, color=(40, 160, 70), is_turtle=True))

        # Lane 3 (y=26): Big logs ->
        for x in (2, 36):
            self.obstacles.append(Obstacle(x=float(x), y=26, w=20, h=4, speed=13.0 * spd_scale, color=(140, 85, 30), is_log=True))

        # Lane 4 (y=31): Small logs <-
        for x in (6, 32, 56):
            self.obstacles.append(Obstacle(x=float(x), y=31, w=13, h=4, speed=-20.0 * spd_scale, color=(140, 85, 30), is_log=True))

        # --- Road Lanes (Y=41..56) ---
        # Lane 1 (y=41): Yellow sedans ->
        for x in (6, 36):
            self.obstacles.append(Obstacle(x=float(x), y=41, w=7, h=4, speed=20.0 * spd_scale, color=(240, 220, 40)))

        # Lane 2 (y=45): White trucks <-
        for x in (12, 44):
            self.obstacles.append(Obstacle(x=float(x), y=45, w=10, h=4, speed=-14.0 * spd_scale, color=(220, 225, 235)))

        # Lane 3 (y=49): Red racecars ->
        for x in (18, 50):
            self.obstacles.append(Obstacle(x=float(x), y=49, w=7, h=4, speed=26.0 * spd_scale, color=(240, 50, 60)))

        # Lane 4 (y=53): Orange bulldozers <-
        for x in (8, 38):
            self.obstacles.append(Obstacle(x=float(x), y=53, w=8, h=4, speed=-12.0 * spd_scale, color=(255, 130, 20)))

    def update(self, dt: float) -> None:
        dt = min(dt, 0.05)
        self.anim_phase += dt

        if self.level_clear_timer > 0:
            self.level_clear_timer -= dt
            if self.level_clear_timer <= 0:
                self.level += 1
                self.score += 1000
                self.homes_filled = [False] * 5
                self._init_obstacles()
                self._respawn_frog()
            return

        if self.splat_timer > 0:
            self.splat_timer -= dt
            if self.splat_timer <= 0:
                self.lives -= 1
                if self.lives <= 0:
                    self.reset()
                else:
                    self._respawn_frog()
            return

        # 1. Move Obstacles
        for obs in self.obstacles:
            obs.x += obs.speed * dt
            if obs.speed > 0 and obs.x > 64:
                obs.x = -obs.w
            elif obs.speed < 0 and obs.x + obs.w < 0:
                obs.x = 64

        # 2. Check Frog on River Log Drift
        in_river = 15 <= self.frog_y <= 35
        on_platform = False
        platform_drift = 0.0

        if in_river:
            for obs in self.obstacles:
                if (obs.is_log or obs.is_turtle) and abs(self.frog_y - (obs.y + 1)) <= 2:
                    if (obs.x - 1) <= self.frog_x <= (obs.x + obs.w):
                        on_platform = True
                        platform_drift = obs.speed * dt
                        break

            if on_platform:
                self.frog_x += platform_drift
                if self.frog_x < 2 or self.frog_x > 61:
                    # Carried off screen
                    self.splat_timer = 0.5
                    return
            else:
                # Drowned in river
                self.splat_timer = 0.5
                return

        # 3. Check Traffic Collision
        in_road = 40 <= self.frog_y <= 56
        if in_road:
            for obs in self.obstacles:
                if not obs.is_log and not obs.is_turtle and abs(self.frog_y - (obs.y + 1)) <= 2:
                    if (obs.x - 2) <= self.frog_x <= (obs.x + obs.w):
                        self.splat_timer = 0.5
                        return

        # 4. Check Home Entry (Y <= 12)
        if self.frog_y <= 13:
            matched_home = -1
            for idx, hx in enumerate(self.LILY_HOMES_X):
                if abs(self.frog_x - hx) <= 3:
                    matched_home = idx
                    break

            if matched_home >= 0 and not self.homes_filled[matched_home]:
                self.homes_filled[matched_home] = True
                self.score += 200
                if all(self.homes_filled):
                    self.level_clear_timer = 1.0
                else:
                    self._respawn_frog()
            else:
                # Hit bush or already filled home
                self.splat_timer = 0.5
            return

        # 5. Autonomous AI Navigation
        if self.hop_cooldown > 0:
            self.hop_cooldown -= dt
            return

        self._ai_step()

    def _ai_step(self) -> None:
        # Determine target home index (pick nearest empty lily pad)
        unfilled = [i for i, f in enumerate(self.homes_filled) if not f]
        if not unfilled:
            return
        target_idx = min(unfilled, key=lambda i: abs(self.LILY_HOMES_X[i] - self.frog_x))
        target_hx = self.LILY_HOMES_X[target_idx]

        step_y = 4.0

        # Scenario A: In Traffic
        if self.frog_y >= 40:
            target_next_y = self.frog_y - step_y
            is_forward_safe = True
            for obs in self.obstacles:
                if not obs.is_log and not obs.is_turtle and abs(target_next_y - (obs.y + 1)) <= 3:
                    # Vehicle will pass through within 0.3s
                    pred_x1 = obs.x + min(0, obs.speed * 0.35) - 2
                    pred_x2 = obs.x + obs.w + max(0, obs.speed * 0.35) + 2
                    if pred_x1 <= self.frog_x <= pred_x2:
                        is_forward_safe = False
                        break

            if is_forward_safe:
                self.frog_y -= step_y
                self.score += 10
                self.hop_cooldown = 0.18
                return
            else:
                # Try sidestepping towards target_hx if safe
                if abs(self.frog_x - target_hx) > 2:
                    dx = 3.0 if target_hx > self.frog_x else -3.0
                    new_x = max(4.0, min(59.0, self.frog_x + dx))
                    self.frog_x = new_x
                    self.hop_cooldown = 0.16
                    return
                # Wait for opening
                self.hop_cooldown = 0.10
                return

        # Scenario B: Grassy Median (y around 36..39)
        if 36 <= self.frog_y < 40:
            # Prepare to enter River Lane 4 (y=31)
            target_lane_y = 31
            for obs in self.obstacles:
                if obs.y == target_lane_y:
                    pred_x = obs.x + obs.speed * 0.18
                    if pred_x + 1 <= self.frog_x <= pred_x + obs.w - 1:
                        self.frog_y = 32
                        self.score += 10
                        self.hop_cooldown = 0.22
                        return
            self.hop_cooldown = 0.08
            return

        # Scenario C: In River (y=16..35)
        if 15 <= self.frog_y <= 35:
            target_next_y = self.frog_y - 5.0
            if target_next_y <= 13:
                # Entering home!
                if abs(self.frog_x - target_hx) <= 3:
                    self.frog_y = 11
                    self.frog_x = target_hx
                    self.hop_cooldown = 0.25
                    return
                else:
                    # Adjust X to align with target home
                    dx = 2.5 if target_hx > self.frog_x else -2.5
                    self.frog_x = max(4.0, min(59.0, self.frog_x + dx))
                    self.hop_cooldown = 0.12
                    return

            # Moving across river lanes
            target_lane = int(round(target_next_y))
            can_hop = False
            for obs in self.obstacles:
                if (obs.is_log or obs.is_turtle) and abs(target_lane - obs.y) <= 2:
                    pred_x = obs.x + obs.speed * 0.2
                    if pred_x <= self.frog_x <= pred_x + obs.w:
                        can_hop = True
                        break

            if can_hop:
                self.frog_y = target_next_y
                self.score += 10
                self.hop_cooldown = 0.22
                return
            else:
                # Shift position along current platform to align with target home
                dx = 2.0 if target_hx > self.frog_x else -2.0
                self.frog_x = max(4.0, min(59.0, self.frog_x + dx))
                self.hop_cooldown = 0.12
                return

    def draw(self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64) -> None:
        # 1. Background Regions
        # Top goal bank (dark green bush)
        draw.rectangle([(0, 0), (63, 14)], fill=(12, 60, 24))

        # River
        draw.rectangle([(0, 15), (63, 35)], fill=(10, 30, 80))

        # Grassy Median
        draw.rectangle([(0, 36), (63, 39)], fill=(28, 120, 36))

        # Road
        draw.rectangle([(0, 40), (63, 56)], fill=(20, 20, 24))
        # Road lane dividers
        for ly in (44, 48, 52):
            for lx in range(2, 63, 8):
                draw.line([(lx, ly), (lx + 3, ly)], fill=(70, 70, 80))

        # Starting curb
        draw.rectangle([(0, 57), (63, 63)], fill=(28, 120, 36))

        # 2. Lily Pad Homes
        for idx, hx in enumerate(self.LILY_HOMES_X):
            hy = self.LILY_Y
            if self.homes_filled[idx]:
                # Sitted frog in home
                draw.rectangle([(hx - 3, hy - 3), (hx + 3, hy + 3)], fill=(10, 30, 80))
                draw.rectangle([(hx - 2, hy - 2), (hx + 2, hy + 2)], fill=(40, 220, 60))
                draw.point((hx - 1, hy - 2), fill=(255, 255, 255))
                draw.point((hx + 1, hy - 2), fill=(255, 255, 255))
            else:
                # Open blue goal slot with lily pad ring
                draw.rectangle([(hx - 3, hy - 3), (hx + 3, hy + 3)], fill=(8, 20, 50), outline=(40, 180, 80))
                draw.ellipse([(hx - 2, hy - 2), (hx + 2, hy + 2)], fill=(30, 140, 50))

        # 3. Obstacles (Logs, Turtles, Cars)
        for obs in self.obstacles:
            ix = int(obs.x)
            iy = obs.y
            if obs.is_log:
                draw.rectangle([(ix, iy), (ix + obs.w - 1, iy + obs.h - 1)], fill=obs.color)
                # Wood ring accents
                draw.point((ix + 1, iy + 1), fill=(100, 55, 15))
                draw.point((ix + obs.w - 2, iy + 2), fill=(100, 55, 15))
            elif obs.is_turtle:
                # Row of paddling turtles
                for tx in range(ix, ix + obs.w - 2, 4):
                    draw.ellipse([(tx, iy), (tx + 3, iy + 3)], fill=obs.color)
                    draw.point((tx + 1, iy + 1), fill=(20, 90, 40))
            else:
                # Car / Truck
                draw.rectangle([(ix, iy), (ix + obs.w - 1, iy + obs.h - 1)], fill=obs.color)
                # Headlights
                if obs.speed > 0:
                    draw.point((ix + obs.w - 1, iy + 1), fill=(255, 255, 200))
                    draw.point((ix + obs.w - 1, iy + 2), fill=(255, 255, 200))
                else:
                    draw.point((ix, iy + 1), fill=(255, 255, 200))
                    draw.point((ix, iy + 2), fill=(255, 255, 200))

        # 4. Frog
        fx = int(self.frog_x)
        fy = int(self.frog_y)
        if self.splat_timer > 0:
            # Splat / splash effect
            draw.ellipse([(fx - 3, fy - 3), (fx + 3, fy + 3)], fill=(255, 80, 40))
            draw.point((fx, fy), fill=(255, 255, 255))
        else:
            # Animated frog sprite
            draw.rectangle([(fx - 1, fy - 1), (fx + 1, fy + 1)], fill=(40, 240, 50))
            # Eyes
            draw.point((fx - 1, fy - 2), fill=(255, 255, 255))
            draw.point((fx + 1, fy - 2), fill=(255, 255, 255))
            # Legs
            draw.point((fx - 2, fy), fill=(25, 160, 35))
            draw.point((fx + 2, fy), fill=(25, 160, 35))
            draw.point((fx - 2, fy + 1), fill=(25, 160, 35))
            draw.point((fx + 2, fy + 1), fill=(25, 160, 35))

        # 5. Top HUD
        draw.text((2, 0), f"S{self.score}", fill=(240, 240, 240), font=self.font)
        draw_right_aligned_text(draw, "FROGGER", right_x=62, y=1, fill=(50, 240, 50), max_width=34, preferred_size=7)

        # Level Clear Flash
        if self.level_clear_timer > 0 and int(self.anim_phase * 10) % 2 == 0:
            draw.rectangle([(4, 25), (59, 39)], fill=(0, 0, 0), outline=(255, 215, 0))
            draw.text((10, 28), "LEVEL CLEAR!", fill=(255, 220, 40), font=self.font)
