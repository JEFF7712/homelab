"""Interactive Air Hockey arcade game for 64x64 LED Matrix."""

from __future__ import annotations

import math
import random
from PIL import ImageDraw, ImageFont
from modes.text_utils import draw_boxed_message

from .base import MultiplayerGame
from .registry import register_game


@register_game(
    game_id="air_hockey",
    name="AIR HOCKEY",
    short_title="HOCKEY",
    badge="1P / 2P",
    desc="Fast puck-and-mallet duel with goals and rebound physics",
    supports_2p=True,
    color=(0, 210, 255),
    order=6,
)
class AirHockeyGame(MultiplayerGame):
    """Air hockey table duel supporting 2-player or 1P vs smart CPU."""

    RINK_L = 3
    RINK_R = 60
    RINK_T = 4
    RINK_B = 59

    GOAL_T = 23
    GOAL_B = 40

    MALLET_R = 3.5
    PUCK_R = 1.8

    P1_SPEED = 55.0
    P2_SPEED = 55.0
    CPU_SPEED = 46.0
    MAX_PUCK_SPEED = 85.0
    TARGET_SCORE = 5

    @staticmethod
    def draw_icon(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        # Mini air hockey rink icon
        draw.rectangle([cx - 8, cy - 6, cx + 8, cy + 6], outline=(0, 180, 230))
        draw.line([cx, cy - 6, cx, cy + 6], fill=(40, 100, 140))
        # Left goal slot & mallet
        draw.rectangle([cx - 8, cy - 2, cx - 7, cy + 2], fill=(255, 60, 60))
        draw.ellipse([cx - 5, cy - 2, cx - 2, cy + 1], fill=(255, 60, 60))
        # Right goal slot & mallet
        draw.rectangle([cx + 7, cy - 2, cx + 8, cy + 2], fill=(40, 200, 255))
        draw.ellipse([cx + 2, cy - 1, cx + 5, cy + 2], fill=(40, 200, 255))
        # Center puck
        draw.rectangle([cx - 1, cy, cx + 1, cy + 1], fill=(255, 240, 80))

    def __init__(self, solo_vs_ai: bool = False) -> None:
        super().__init__(solo_vs_ai=solo_vs_ai)
        self.font = ImageFont.load_default(size=9)
        self.p1_score = 0
        self.p2_score = 0
        self.p1_x = 12.0
        self.p1_y = 31.5
        self.p1_vx = 0.0
        self.p1_vy = 0.0

        self.p2_x = 51.0
        self.p2_y = 31.5
        self.p2_vx = 0.0
        self.p2_vy = 0.0

        self.puck_x = 31.5
        self.puck_y = 31.5
        self.puck_vx = 0.0
        self.puck_vy = 0.0

        self.goal_timer = 0.0
        self.goal_flash = 0
        self.reset()

    def reset(self) -> None:
        self.winner = 0
        self.p1_score = 0
        self.p2_score = 0
        self.p1_x = 12.0
        self.p1_y = 31.5
        self.p1_vx = 0.0
        self.p1_vy = 0.0
        self.p2_x = 51.0
        self.p2_y = 31.5
        self.p2_vx = 0.0
        self.p2_vy = 0.0
        self.goal_timer = 0.0
        self.goal_flash = 0
        self._serve_puck(direction=random.choice([-1, 1]))

    def _serve_puck(self, direction: int) -> None:
        self.puck_x = 31.5
        self.puck_y = 31.5
        angle = random.uniform(-0.55, 0.55)
        speed = 36.0
        self.puck_vx = direction * speed * math.cos(angle)
        self.puck_vy = speed * math.sin(angle)

    def get_final_score(self) -> int:
        return max(self.p1_score, self.p2_score)

    def get_status_text(self) -> str:
        p2_label = "CPU" if self.solo_vs_ai else "P2"
        return f"P1: {self.p1_score}  {p2_label}: {self.p2_score}"

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

        # Cap dt to prevent physics tunneling
        dt = min(dt, 0.05)

        if self.goal_timer > 0:
            self.goal_timer -= dt
            if self.goal_timer <= 0:
                self.goal_flash = 0
            return

        # --- 1. P1 Mallet Movement ---
        p1_dx = 0.0
        p1_dy = 0.0
        if "up" in p1_inputs:
            p1_dy -= 1.0
        if "down" in p1_inputs:
            p1_dy += 1.0
        if "left" in p1_inputs:
            p1_dx -= 1.0
        if "right" in p1_inputs:
            p1_dx += 1.0

        if p1_dx != 0 and p1_dy != 0:
            norm = 1.0 / math.sqrt(2.0)
            p1_dx *= norm
            p1_dy *= norm

        prev_p1_x = self.p1_x
        prev_p1_y = self.p1_y
        self.p1_x = max(
            self.RINK_L + self.MALLET_R,
            min(29.0 - self.MALLET_R, self.p1_x + p1_dx * self.P1_SPEED * dt),
        )
        self.p1_y = max(
            self.RINK_T + self.MALLET_R,
            min(self.RINK_B - self.MALLET_R, self.p1_y + p1_dy * self.P1_SPEED * dt),
        )
        self.p1_vx = (self.p1_x - prev_p1_x) / dt if dt > 0 else 0.0
        self.p1_vy = (self.p1_y - prev_p1_y) / dt if dt > 0 else 0.0

        # --- 2. P2 / CPU Mallet Movement ---
        p2_dx = 0.0
        p2_dy = 0.0
        if self.solo_vs_ai:
            # Smart AI logic: defends own half and strikes puck
            target_x = 49.0
            target_y = 31.5

            if self.puck_x > 32.0:
                # Puck in AI half
                target_x = max(35.0, min(56.0, self.puck_x + 3.0))
                target_y = self.puck_y
            elif self.puck_vx > 0.0:
                # Puck moving toward AI half: intercept
                target_y = self.puck_y + (self.puck_vy * 0.2)
                target_x = 50.0
            else:
                # Puck retreating: center up
                target_x = 48.0
                target_y = 31.5

            diff_x = target_x - self.p2_x
            diff_y = target_y - self.p2_y
            dist = math.hypot(diff_x, diff_y)
            if dist > 1.0:
                p2_dx = diff_x / dist
                p2_dy = diff_y / dist
            speed = self.CPU_SPEED
        else:
            if "up" in p2_inputs:
                p2_dy -= 1.0
            if "down" in p2_inputs:
                p2_dy += 1.0
            if "left" in p2_inputs:
                p2_dx -= 1.0
            if "right" in p2_inputs:
                p2_dx += 1.0
            if p2_dx != 0 and p2_dy != 0:
                norm = 1.0 / math.sqrt(2.0)
                p2_dx *= norm
                p2_dy *= norm
            speed = self.P2_SPEED

        prev_p2_x = self.p2_x
        prev_p2_y = self.p2_y
        self.p2_x = max(
            34.0 + self.MALLET_R,
            min(self.RINK_R - self.MALLET_R, self.p2_x + p2_dx * speed * dt),
        )
        self.p2_y = max(
            self.RINK_T + self.MALLET_R,
            min(self.RINK_B - self.MALLET_R, self.p2_y + p2_dy * speed * dt),
        )
        self.p2_vx = (self.p2_x - prev_p2_x) / dt if dt > 0 else 0.0
        self.p2_vy = (self.p2_y - prev_p2_y) / dt if dt > 0 else 0.0

        # --- 3. Puck Physics & Wall Bouncing ---
        self.puck_x += self.puck_vx * dt
        self.puck_y += self.puck_vy * dt

        # Slight friction/air drag
        drag = max(0.0, 1.0 - 0.08 * dt)
        self.puck_vx *= drag
        self.puck_vy *= drag

        # Top / bottom rails
        if self.puck_y <= self.RINK_T + self.PUCK_R:
            self.puck_y = self.RINK_T + self.PUCK_R
            self.puck_vy = abs(self.puck_vy)
            self.play_sfx("wall")
        elif self.puck_y >= self.RINK_B - self.PUCK_R:
            self.puck_y = self.RINK_B - self.PUCK_R
            self.puck_vy = -abs(self.puck_vy)
            self.play_sfx("wall")

        # Left / right rails or goals
        # Left side:
        if self.puck_x <= self.RINK_L + self.PUCK_R:
            if self.GOAL_T <= self.puck_y <= self.GOAL_B:
                # GOAL FOR P2 / CPU!
                self.p2_score += 1
                self.goal_timer = 0.8
                self.goal_flash = 2
                self.play_sfx("point")
                if self.p2_score >= self.TARGET_SCORE:
                    self.winner = 2
                    self.play_sfx("game_over")
                else:
                    self._serve_puck(direction=1)
                return
            else:
                self.puck_x = self.RINK_L + self.PUCK_R
                self.puck_vx = abs(self.puck_vx)
                self.play_sfx("wall")

        # Right side:
        if self.puck_x >= self.RINK_R - self.PUCK_R:
            if self.GOAL_T <= self.puck_y <= self.GOAL_B:
                # GOAL FOR P1!
                self.p1_score += 1
                self.goal_timer = 0.8
                self.goal_flash = 1
                self.play_sfx("point")
                if self.p1_score >= self.TARGET_SCORE:
                    self.winner = 1
                    self.play_sfx("game_over")
                else:
                    self._serve_puck(direction=-1)
                return
            else:
                self.puck_x = self.RINK_R - self.PUCK_R
                self.puck_vx = -abs(self.puck_vx)
                self.play_sfx("wall")

        # --- 4. Mallet Collisions ---
        self._check_mallet_collision(self.p1_x, self.p1_y, self.p1_vx, self.p1_vy)
        self._check_mallet_collision(self.p2_x, self.p2_y, self.p2_vx, self.p2_vy)

    def _check_mallet_collision(self, mx: float, my: float, mvx: float, mvy: float) -> None:
        dx = self.puck_x - mx
        dy = self.puck_y - my
        min_dist = self.MALLET_R + self.PUCK_R
        dist_sq = dx * dx + dy * dy
        if dist_sq >= min_dist * min_dist or dist_sq <= 0.0001:
            return

        dist = math.sqrt(dist_sq)
        nx = dx / dist
        ny = dy / dist

        # Push puck outside mallet
        overlap = min_dist - dist
        self.puck_x += nx * overlap
        self.puck_y += ny * overlap

        # Relative velocity
        rel_vx = self.puck_vx - mvx
        rel_vy = self.puck_vy - mvy
        vel_norm = rel_vx * nx + rel_vy * ny

        if vel_norm < 0:
            # Elastic reflection + mallet kinetic transfer
            restitution = 1.6
            self.puck_vx -= restitution * vel_norm * nx + 0.45 * mvx
            self.puck_vy -= restitution * vel_norm * ny + 0.45 * mvy

            speed = math.hypot(self.puck_vx, self.puck_vy)
            if speed > self.MAX_PUCK_SPEED:
                scale = self.MAX_PUCK_SPEED / speed
                self.puck_vx *= scale
                self.puck_vy *= scale
            elif speed < 18.0:
                scale = 18.0 / max(0.1, speed)
                self.puck_vx *= scale
                self.puck_vy *= scale

            self.play_sfx("paddle")

    def render(self, draw: ImageDraw.ImageDraw) -> None:
        # 1. Rink surface & boundary
        bg_color = (6, 18, 30)
        draw.rectangle([0, 0, 63, 63], fill=(0, 0, 0))
        draw.rectangle([self.RINK_L, self.RINK_T, self.RINK_R, self.RINK_B], fill=bg_color)

        # Rink boundary outline
        border_col = (0, 180, 240) if self.goal_flash == 0 else (255, 255, 255)
        draw.rectangle([self.RINK_L, self.RINK_T, self.RINK_R, self.RINK_B], outline=border_col)

        # Center line & center circle
        draw.line([31, self.RINK_T + 1, 31, self.RINK_B - 1], fill=(20, 60, 90))
        draw.line([32, self.RINK_T + 1, 32, self.RINK_B - 1], fill=(20, 60, 90))
        draw.ellipse([25, 25, 38, 38], outline=(20, 70, 100))

        # Goal openings
        draw.rectangle([self.RINK_L, self.GOAL_T, self.RINK_L + 1, self.GOAL_B], fill=(255, 40, 50))
        draw.rectangle([self.RINK_R - 1, self.GOAL_T, self.RINK_R, self.GOAL_B], fill=(40, 190, 255))

        # Scores rendered at top border of rink
        s1 = str(self.p1_score)
        s2 = str(self.p2_score)
        draw.text((22, 1), s1, fill=(255, 100, 100), font=self.font)
        draw.text((37, 1), s2, fill=(100, 200, 255), font=self.font)

        # 2. P1 Mallet (Red/Orange neon)
        p1_r = int(self.MALLET_R)
        p1_ix = int(self.p1_x)
        p1_iy = int(self.p1_y)
        draw.ellipse(
            [p1_ix - p1_r, p1_iy - p1_r, p1_ix + p1_r, p1_iy + p1_r],
            fill=(255, 50, 60),
            outline=(255, 140, 150),
        )
        draw.point((p1_ix, p1_iy), fill=(255, 255, 255))

        # 3. P2 Mallet (Cyan/Blue neon)
        p2_r = int(self.MALLET_R)
        p2_ix = int(self.p2_x)
        p2_iy = int(self.p2_y)
        draw.ellipse(
            [p2_ix - p2_r, p2_iy - p2_r, p2_ix + p2_r, p2_iy + p2_r],
            fill=(30, 160, 255),
            outline=(140, 215, 255),
        )
        draw.point((p2_ix, p2_iy), fill=(255, 255, 255))

        # 4. Puck (Bright yellow neon disk with core)
        px = int(self.puck_x)
        py = int(self.puck_y)
        draw.ellipse([px - 2, py - 2, px + 2, py + 2], fill=(255, 240, 60))
        draw.point((px, py), fill=(255, 255, 255))

        # Game Over Banner
        if self.is_game_over():
            winner_text = "P1 WINS!" if self.winner == 1 else ("CPU WINS" if self.solo_vs_ai else "P2 WINS!")
            draw_boxed_message(
                draw,
                title=winner_text,
                rect=(4, 23, 59, 41),
                bg_color=(10, 10, 16),
                border_color=(255, 215, 0),
                title_color=(255, 220, 40),
            )
