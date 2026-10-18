"""Interactive Asteroids arcade game for 64x64 LED Matrix."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from PIL import ImageDraw, ImageFont
from modes.text_utils import draw_boxed_message

from .base import MultiplayerGame
from .registry import register_game


@dataclass
class Bullet:
    x: float
    y: float
    vx: float
    vy: float
    life: float = 0.85


@dataclass
class Asteroid:
    x: float
    y: float
    vx: float
    vy: float
    tier: int  # 3: Large, 2: Medium, 1: Small
    radius: float
    offsets: list[tuple[float, float]]


@dataclass
class Debris:
    x: float
    y: float
    vx: float
    vy: float
    color: tuple[int, int, int]
    life: float = 0.5


@register_game(
    game_id="asteroids",
    name="ASTEROIDS",
    short_title="ASTEROID",
    badge="1P SOLO",
    desc="Space shooter with inertia, splitting rocks, and bullet physics",
    supports_2p=False,
    color=(255, 220, 60),
    order=7,
)
class AsteroidsGame(MultiplayerGame):
    """Classic vector-style Asteroids running on 64x64 LED canvas."""

    @staticmethod
    def draw_icon(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        # Mini vector ship pointing up-right
        draw.polygon([(cx + 4, cy - 4), (cx - 5, cy), (cx - 1, cy + 5)], outline=(255, 255, 255), fill=(40, 60, 100))
        # Thruster spark
        draw.point((cx - 4, cy + 4), fill=(255, 120, 20))
        # Nearby asteroid rock
        draw.ellipse([cx - 7, cy - 7, cx - 2, cy - 2], outline=(200, 200, 220))
        # Tiny bullet
        draw.point((cx + 6, cy - 6), fill=(255, 60, 60))

    def __init__(self) -> None:
        super().__init__(solo_vs_ai=True)
        self.font = ImageFont.load_default(size=9)
        self.ship_x = 32.0
        self.ship_y = 32.0
        self.ship_vx = 0.0
        self.ship_vy = 0.0
        self.ship_angle = 90.0  # Pointing straight up
        self.thrusting = False
        self.cooldown = 0.0
        self.invulnerable = 2.0
        self.lives = 3
        self.score = 0
        self.wave = 1

        self.bullets: list[Bullet] = []
        self.asteroids: list[Asteroid] = []
        self.debris: list[Debris] = []
        self.reset()

    def reset(self) -> None:
        self.winner = 0
        self.score = 0
        self.lives = 3
        self.wave = 1
        self.bullets.clear()
        self.debris.clear()
        self._respawn_ship(full_reset=True)
        self._spawn_wave(3)

    def _respawn_ship(self, full_reset: bool = False) -> None:
        self.ship_x = 32.0
        self.ship_y = 32.0
        self.ship_vx = 0.0
        self.ship_vy = 0.0
        if full_reset:
            self.ship_angle = 90.0
        self.invulnerable = 2.0
        self.cooldown = 0.0
        self.thrusting = False

    def _make_asteroid(self, x: float, y: float, tier: int, speed_boost: float = 0.0) -> Asteroid:
        radius = 5.5 if tier == 3 else (3.6 if tier == 2 else 2.0)
        speed = random.uniform(8.0 + speed_boost, 18.0 + speed_boost)
        angle = random.uniform(0, 2 * math.pi)
        vx = math.cos(angle) * speed
        vy = math.sin(angle) * speed

        # Jagged polygon vertices
        num_pts = 8
        offsets: list[tuple[float, float]] = []
        for i in range(num_pts):
            a = (2 * math.pi * i) / num_pts
            r = radius * random.uniform(0.75, 1.25)
            offsets.append((math.cos(a) * r, math.sin(a) * r))

        return Asteroid(x=x, y=y, vx=vx, vy=vy, tier=tier, radius=radius, offsets=offsets)

    def _spawn_wave(self, count: int) -> None:
        self.asteroids.clear()
        boost = (self.wave - 1) * 3.0
        for _ in range(count):
            # Spawn at border to stay away from center ship
            if random.random() < 0.5:
                x = random.choice([2.0, 61.0])
                y = random.uniform(0, 63.0)
            else:
                x = random.uniform(0, 63.0)
                y = random.choice([2.0, 61.0])
            self.asteroids.append(self._make_asteroid(x, y, tier=3, speed_boost=boost))

    def get_final_score(self) -> int:
        return self.score

    def get_status_text(self) -> str:
        return f"SCORE: {self.score}  LIVES: {self.lives}"

    def update(
        self,
        dt: float,
        p1_inputs: set[str],
        p2_inputs: set[str],
        p1_events: list[str] | None = None,
        p2_events: list[str] | None = None,
    ) -> None:
        if self.is_game_over() or dt <= 0:
            return

        dt = min(dt, 0.05)

        if self.invulnerable > 0:
            self.invulnerable = max(0.0, self.invulnerable - dt)

        # 1. Ship Controls
        rot_speed = 190.0  # degrees per sec
        if "left" in p1_inputs:
            self.ship_angle = (self.ship_angle + rot_speed * dt) % 360.0
        if "right" in p1_inputs:
            self.ship_angle = (self.ship_angle - rot_speed * dt) % 360.0

        rad = math.radians(self.ship_angle)
        self.thrusting = "up" in p1_inputs
        if self.thrusting:
            thrust_power = 65.0
            self.ship_vx += math.cos(rad) * thrust_power * dt
            self.ship_vy -= math.sin(rad) * thrust_power * dt

        # Inertia drag
        drag = max(0.0, 1.0 - 0.45 * dt)
        self.ship_vx *= drag
        self.ship_vy *= drag

        # Cap max speed
        speed = math.hypot(self.ship_vx, self.ship_vy)
        max_speed = 46.0
        if speed > max_speed:
            scale = max_speed / speed
            self.ship_vx *= scale
            self.ship_vy *= scale

        # Ship motion with screen wraparound
        self.ship_x = (self.ship_x + self.ship_vx * dt) % 64.0
        self.ship_y = (self.ship_y + self.ship_vy * dt) % 64.0

        # Shooting
        if self.cooldown > 0:
            self.cooldown = max(0.0, self.cooldown - dt)

        fire_requested = "action_a" in p1_inputs or "action_b" in p1_inputs
        if p1_events and ("action_a" in p1_events or "action_b" in p1_events):
            fire_requested = True

        if fire_requested and self.cooldown <= 0 and len(self.bullets) < 5:
            self.cooldown = 0.22
            bullet_speed = 80.0
            bx = (self.ship_x + math.cos(rad) * 4.5) % 64.0
            by = (self.ship_y - math.sin(rad) * 4.5) % 64.0
            bvx = math.cos(rad) * bullet_speed + self.ship_vx * 0.2
            bvy = -math.sin(rad) * bullet_speed + self.ship_vy * 0.2
            self.bullets.append(Bullet(x=bx, y=by, vx=bvx, vy=bvy))
            self.play_sfx("laser")

        # 2. Update Bullets
        alive_bullets: list[Bullet] = []
        for b in self.bullets:
            b.x = (b.x + b.vx * dt) % 64.0
            b.y = (b.y + b.vy * dt) % 64.0
            b.life -= dt
            if b.life > 0:
                alive_bullets.append(b)
        self.bullets = alive_bullets

        # 3. Update Asteroids
        for a in self.asteroids:
            a.x = (a.x + a.vx * dt) % 64.0
            a.y = (a.y + a.vy * dt) % 64.0

        # 4. Update Debris Sparks
        alive_debris: list[Debris] = []
        for d in self.debris:
            d.x += d.vx * dt
            d.y += d.vy * dt
            d.life -= dt
            if d.life > 0:
                alive_debris.append(d)
        self.debris = alive_debris

        # 5. Bullet vs Asteroid Collisions
        new_asteroids: list[Asteroid] = []
        asteroids_to_keep: list[Asteroid] = []
        boost = (self.wave - 1) * 3.0

        for a in self.asteroids:
            hit = False
            for b in self.bullets:
                # Distance with toroidal wrapping check
                dx = abs(b.x - a.x)
                if dx > 32.0:
                    dx = 64.0 - dx
                dy = abs(b.y - a.y)
                if dy > 32.0:
                    dy = 64.0 - dy

                if dx * dx + dy * dy <= a.radius * a.radius:
                    hit = True
                    b.life = -1.0  # Destroy bullet
                    break

            if hit:
                # Points & SFX
                if a.tier == 3:
                    self.score += 20
                    new_asteroids.append(self._make_asteroid(a.x, a.y, tier=2, speed_boost=boost))
                    new_asteroids.append(self._make_asteroid(a.x, a.y, tier=2, speed_boost=boost))
                elif a.tier == 2:
                    self.score += 50
                    new_asteroids.append(self._make_asteroid(a.x, a.y, tier=1, speed_boost=boost))
                    new_asteroids.append(self._make_asteroid(a.x, a.y, tier=1, speed_boost=boost))
                else:
                    self.score += 100

                self.play_sfx("point")
                # Spawn rock particles
                for _ in range(6):
                    ang = random.uniform(0, 2 * math.pi)
                    sp = random.uniform(15.0, 35.0)
                    self.debris.append(
                        Debris(
                            x=a.x,
                            y=a.y,
                            vx=math.cos(ang) * sp,
                            vy=math.sin(ang) * sp,
                            color=(210, 210, 230),
                        )
                    )
            else:
                asteroids_to_keep.append(a)

        self.bullets = [b for b in self.bullets if b.life > 0]
        self.asteroids = asteroids_to_keep + new_asteroids

        # Check wave completion
        if not self.asteroids:
            self.score += 250
            self.wave += 1
            self._spawn_wave(min(8, 2 + self.wave))
            self.play_sfx("paddle")

        # 6. Ship vs Asteroid Collision
        if self.invulnerable <= 0:
            ship_hit = False
            for a in self.asteroids:
                dx = abs(self.ship_x - a.x)
                if dx > 32.0:
                    dx = 64.0 - dx
                dy = abs(self.ship_y - a.y)
                if dy > 32.0:
                    dy = 64.0 - dy
                if dx * dx + dy * dy <= (a.radius + 2.5) * (a.radius + 2.5):
                    ship_hit = True
                    break

            if ship_hit:
                self.lives -= 1
                self.play_sfx("crash")
                # Explosion sparks
                for _ in range(12):
                    ang = random.uniform(0, 2 * math.pi)
                    sp = random.uniform(20.0, 50.0)
                    self.debris.append(
                        Debris(
                            x=self.ship_x,
                            y=self.ship_y,
                            vx=math.cos(ang) * sp,
                            vy=math.sin(ang) * sp,
                            color=random.choice([(255, 60, 40), (255, 180, 20), (255, 255, 100)]),
                        )
                    )

                if self.lives <= 0:
                    self.winner = 1
                    self.play_sfx("game_over")
                else:
                    self._respawn_ship()

    def render(self, draw: ImageDraw.ImageDraw) -> None:
        draw.rectangle([0, 0, 63, 63], fill=(0, 0, 0))

        # 1. Debris particles
        for d in self.debris:
            ix = int(d.x) % 64
            iy = int(d.y) % 64
            draw.point((ix, iy), fill=d.color)

        # 2. Asteroids
        for a in self.asteroids:
            pts = [(int(a.x + ox) % 64, int(a.y + oy) % 64) for ox, oy in a.offsets]
            color = (210, 210, 230) if a.tier == 3 else ((180, 190, 220) if a.tier == 2 else (140, 150, 190))
            if len(pts) >= 3:
                draw.polygon(pts, outline=color)

        # 3. Bullets (bright neon red dots)
        for b in self.bullets:
            ix = int(b.x) % 64
            iy = int(b.y) % 64
            draw.point((ix, iy), fill=(255, 60, 60))

        # 4. Ship
        if not self.is_game_over() and (self.invulnerable <= 0 or int(self.invulnerable * 12) % 2 == 0):
            rad = math.radians(self.ship_angle)
            # Nose
            p_nose = (self.ship_x + math.cos(rad) * 4.5, self.ship_y - math.sin(rad) * 4.5)
            # Left wing
            rad_l = rad + 2.5
            p_left = (self.ship_x + math.cos(rad_l) * 4.0, self.ship_y - math.sin(rad_l) * 4.0)
            # Right wing
            rad_r = rad - 2.5
            p_right = (self.ship_x + math.cos(rad_r) * 4.0, self.ship_y - math.sin(rad_r) * 4.0)

            ship_pts = [
                (int(p_nose[0]) % 64, int(p_nose[1]) % 64),
                (int(p_left[0]) % 64, int(p_left[1]) % 64),
                (int(p_right[0]) % 64, int(p_right[1]) % 64),
            ]
            draw.polygon(ship_pts, outline=(255, 255, 255), fill=(20, 40, 80))

            # Thruster flame
            if self.thrusting:
                rad_back = rad + math.pi
                flame_len = random.uniform(3.0, 5.0)
                flame_tip = (
                    int(self.ship_x + math.cos(rad_back) * flame_len) % 64,
                    int(self.ship_y - math.sin(rad_back) * flame_len) % 64,
                )
                draw.line(
                    [(int(self.ship_x) % 64, int(self.ship_y) % 64), flame_tip],
                    fill=(255, 140, 20),
                )

        # 5. HUD: Score and Lives
        draw.text((2, 1), str(self.score), fill=(255, 220, 60), font=self.font)
        # Small ship icons for remaining lives
        for i in range(self.lives):
            lx = 58 - i * 6
            draw.line([(lx, 2), (lx - 2, 6)], fill=(255, 255, 255))
            draw.line([(lx, 2), (lx + 2, 6)], fill=(255, 255, 255))
            draw.line([(lx - 2, 6), (lx + 2, 6)], fill=(120, 160, 255))

        # Game Over Banner
        if self.is_game_over():
            draw_boxed_message(
                draw,
                title="GAME OVER",
                rect=(4, 23, 59, 41),
                bg_color=(10, 10, 16),
                border_color=(255, 60, 60),
                title_color=(255, 70, 70),
            )
