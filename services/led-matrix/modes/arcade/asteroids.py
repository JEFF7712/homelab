"""Autonomous AI Asteroids arcade simulation on 64x64 RGB matrix."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from modes.text_utils import draw_scoreboard

from .base import ArcadeGame


@dataclass
class AIBullet:
    x: float
    y: float
    vx: float
    vy: float
    life: float = 0.8


@dataclass
class AIAsteroid:
    x: float
    y: float
    vx: float
    vy: float
    tier: int  # 3: Large, 2: Med, 1: Small
    radius: float
    offsets: list[tuple[float, float]]


class AsteroidsGame(ArcadeGame):
    name = "ASTEROIDS"

    def __init__(self) -> None:
        self.font = ImageFont.load_default(size=9)
        self.score = 0
        self.wave = 1
        self.ship_x = 32.0
        self.ship_y = 32.0
        self.ship_vx = 0.0
        self.ship_vy = 0.0
        self.ship_angle = 90.0  # degrees
        self.thrusting = False
        self.cooldown = 0.0
        self.bullets: list[AIBullet] = []
        self.asteroids: list[AIAsteroid] = []
        self.reset()

    def reset(self) -> None:
        self.score = 0
        self.wave = 1
        self.ship_x = 32.0
        self.ship_y = 32.0
        self.ship_vx = 0.0
        self.ship_vy = 0.0
        self.ship_angle = 90.0
        self.thrusting = False
        self.cooldown = 0.0
        self.bullets.clear()
        self._spawn_wave(3)

    def _make_asteroid(self, x: float, y: float, tier: int) -> AIAsteroid:
        radius = 5.2 if tier == 3 else (3.4 if tier == 2 else 2.0)
        speed = random.uniform(8.0, 16.0)
        ang = random.uniform(0, 2 * math.pi)
        vx = math.cos(ang) * speed
        vy = math.sin(ang) * speed

        num_pts = 8
        offsets: list[tuple[float, float]] = []
        for i in range(num_pts):
            a = (2 * math.pi * i) / num_pts
            r = radius * random.uniform(0.75, 1.25)
            offsets.append((math.cos(a) * r, math.sin(a) * r))

        return AIAsteroid(
            x=x, y=y, vx=vx, vy=vy, tier=tier, radius=radius, offsets=offsets
        )

    def _spawn_wave(self, count: int) -> None:
        self.asteroids.clear()
        for _ in range(count):
            if random.random() < 0.5:
                x = random.choice([2.0, 61.0])
                y = random.uniform(0, 63.0)
            else:
                x = random.uniform(0, 63.0)
                y = random.choice([2.0, 61.0])
            self.asteroids.append(self._make_asteroid(x, y, tier=3))

    def update(self, dt: float) -> None:
        dt = min(dt, 0.05)

        # 1. Update Asteroids
        for a in self.asteroids:
            a.x = (a.x + a.vx * dt) % 64.0
            a.y = (a.y + a.vy * dt) % 64.0

        # 2. Update Bullets
        alive_bullets: list[AIBullet] = []
        for b in self.bullets:
            b.x = (b.x + b.vx * dt) % 64.0
            b.y = (b.y + b.vy * dt) % 64.0
            b.life -= dt
            if b.life > 0:
                alive_bullets.append(b)
        self.bullets = alive_bullets

        # 3. Bullet vs Asteroid Collisions
        new_rocks: list[AIAsteroid] = []
        surviving_rocks: list[AIAsteroid] = []
        for a in self.asteroids:
            hit = False
            for b in self.bullets:
                dx = abs(b.x - a.x)
                if dx > 32.0:
                    dx = 64.0 - dx
                dy = abs(b.y - a.y)
                if dy > 32.0:
                    dy = 64.0 - dy

                if dx * dx + dy * dy <= a.radius * a.radius:
                    hit = True
                    b.life = -1.0
                    break

            if hit:
                if a.tier == 3:
                    self.score += 20
                    new_rocks.append(self._make_asteroid(a.x, a.y, tier=2))
                    new_rocks.append(self._make_asteroid(a.x, a.y, tier=2))
                elif a.tier == 2:
                    self.score += 50
                    new_rocks.append(self._make_asteroid(a.x, a.y, tier=1))
                    new_rocks.append(self._make_asteroid(a.x, a.y, tier=1))
                else:
                    self.score += 100
            else:
                surviving_rocks.append(a)

        self.bullets = [b for b in self.bullets if b.life > 0]
        self.asteroids = surviving_rocks + new_rocks

        if not self.asteroids:
            self.score += 250
            self.wave += 1
            self._spawn_wave(min(7, 2 + self.wave))

        # 4. Autonomous AI Interceptor Logic
        self._ai_control(dt)

        # 5. Ship Physics
        self.ship_x = (self.ship_x + self.ship_vx * dt) % 64.0
        self.ship_y = (self.ship_y + self.ship_vy * dt) % 64.0
        drag = max(0.0, 1.0 - 0.5 * dt)
        self.ship_vx *= drag
        self.ship_vy *= drag

    def _ai_control(self, dt: float) -> None:
        if not self.asteroids:
            return

        if self.cooldown > 0:
            self.cooldown = max(0.0, self.cooldown - dt)

        # Find closest threatening asteroid
        closest_dist = 999.0
        target = self.asteroids[0]
        for a in self.asteroids:
            dx = abs(self.ship_x - a.x)
            if dx > 32.0:
                dx = 64.0 - dx
            dy = abs(self.ship_y - a.y)
            if dy > 32.0:
                dy = 64.0 - dy
            d = math.hypot(dx, dy)
            if d < closest_dist:
                closest_dist = d
                target = a

        # Compute aim angle toward target with basic velocity compensation
        aim_x = target.x + target.vx * 0.15
        aim_y = target.y + target.vy * 0.15
        dx = aim_x - self.ship_x
        dy = -(aim_y - self.ship_y)
        target_ang = (math.degrees(math.atan2(dy, dx))) % 360.0

        # Rotate toward target
        diff = (target_ang - self.ship_angle + 180.0) % 360.0 - 180.0
        rot_speed = 220.0 * dt
        if abs(diff) < rot_speed:
            self.ship_angle = target_ang
        elif diff > 0:
            self.ship_angle = (self.ship_angle + rot_speed) % 360.0
        else:
            self.ship_angle = (self.ship_angle - rot_speed) % 360.0

        # Dodge / Evade if asteroid is very close
        self.thrusting = False
        if closest_dist < 14.0:
            # Thrust to create distance
            rad = math.radians(self.ship_angle)
            self.ship_vx += math.cos(rad) * 45.0 * dt
            self.ship_vy -= math.sin(rad) * 45.0 * dt
            self.thrusting = True

        # Shoot if roughly on target
        if abs(diff) < 18.0 and self.cooldown <= 0 and len(self.bullets) < 4:
            self.cooldown = 0.22
            rad = math.radians(self.ship_angle)
            bullet_spd = 75.0
            bx = (self.ship_x + math.cos(rad) * 4.0) % 64.0
            by = (self.ship_y - math.sin(rad) * 4.0) % 64.0
            self.bullets.append(
                AIBullet(
                    x=bx,
                    y=by,
                    vx=math.cos(rad) * bullet_spd + self.ship_vx * 0.2,
                    vy=-math.sin(rad) * bullet_spd + self.ship_vy * 0.2,
                )
            )

    def draw(
        self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64
    ) -> None:
        draw.rectangle([(0, 0), (size - 1, size - 1)], fill=(0, 0, 0))

        # Top HUD
        draw_scoreboard(draw, f"S{self.score}", "ASTEROIDS", size=size)

        # Asteroids
        for a in self.asteroids:
            pts = [(int(a.x + ox) % 64, int(a.y + oy) % 64) for ox, oy in a.offsets]
            col = (
                (210, 210, 230)
                if a.tier == 3
                else ((180, 190, 220) if a.tier == 2 else (140, 150, 190))
            )
            if len(pts) >= 3:
                draw.polygon(pts, outline=col)

        # Bullets
        for b in self.bullets:
            draw.point((int(b.x) % 64, int(b.y) % 64), fill=(255, 60, 60))

        # Ship
        rad = math.radians(self.ship_angle)
        p_nose = (self.ship_x + math.cos(rad) * 4.5, self.ship_y - math.sin(rad) * 4.5)
        rad_l = rad + 2.5
        p_left = (
            self.ship_x + math.cos(rad_l) * 4.0,
            self.ship_y - math.sin(rad_l) * 4.0,
        )
        rad_r = rad - 2.5
        p_right = (
            self.ship_x + math.cos(rad_r) * 4.0,
            self.ship_y - math.sin(rad_r) * 4.0,
        )

        ship_pts = [
            (int(p_nose[0]) % 64, int(p_nose[1]) % 64),
            (int(p_left[0]) % 64, int(p_left[1]) % 64),
            (int(p_right[0]) % 64, int(p_right[1]) % 64),
        ]
        draw.polygon(ship_pts, outline=(255, 255, 255), fill=(20, 40, 80))

        if self.thrusting:
            rad_back = rad + math.pi
            flame_tip = (
                int(self.ship_x + math.cos(rad_back) * 4.0) % 64,
                int(self.ship_y - math.sin(rad_back) * 4.0) % 64,
            )
            draw.line(
                [(int(self.ship_x) % 64, int(self.ship_y) % 64), flame_tip],
                fill=(255, 140, 20),
            )
