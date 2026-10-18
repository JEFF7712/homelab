"""Interactive Flappy Bird arcade game for 64x64 LED Matrix."""

from __future__ import annotations

import random
from dataclasses import dataclass
from PIL import ImageDraw, ImageFont
from modes.text_utils import draw_boxed_message

from .base import MultiplayerGame
from .registry import register_game


@dataclass
class Pipe:
    x: float
    gap_y: float  # Center of gap
    passed: bool = False


@register_game(
    game_id="flappy",
    name="FLAPPY BIRD",
    short_title="FLAPPY",
    badge="1P SOLO",
    desc="Tap to flap through scrolling pipe obstacles",
    supports_2p=False,
    color=(255, 210, 0),
    order=8,
)
class FlappyBirdGame(MultiplayerGame):
    """Side-scrolling Flappy Bird obstacle run on 64x64 LED canvas."""

    PIPE_W = 8.0
    GAP_H = 20.0
    GROUND_Y = 59.0
    SCROLL_SPEED = 28.0
    GRAVITY = 145.0
    FLAP_IMPULSE = -58.0

    @staticmethod
    def draw_icon(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        # Green pipe outline
        draw.rectangle([cx + 2, cy - 8, cx + 7, cy - 2], fill=(40, 190, 60), outline=(20, 120, 30))
        draw.rectangle([cx + 2, cy + 3, cx + 7, cy + 8], fill=(40, 190, 60), outline=(20, 120, 30))
        # Yellow bird
        draw.rectangle([cx - 7, cy - 2, cx - 3, cy + 2], fill=(255, 220, 0))
        draw.point((cx - 4, cy - 1), fill=(255, 255, 255))
        draw.point((cx - 2, cy), fill=(255, 120, 0))  # Beak

    def __init__(self) -> None:
        super().__init__(solo_vs_ai=True)
        self.font = ImageFont.load_default(size=9)
        self.bird_x = 16.0
        self.bird_y = 28.0
        self.bird_vy = 0.0
        self.score = 0
        self.pipes: list[Pipe] = []
        self._prev_flap_input = False
        self.reset()

    def reset(self) -> None:
        self.winner = 0
        self.score = 0
        self.bird_x = 16.0
        self.bird_y = 28.0
        self.bird_vy = 0.0
        self.pipes.clear()
        self._prev_flap_input = False

        # Pre-seed pipes ahead
        self.pipes.append(Pipe(x=54.0, gap_y=random.uniform(20.0, 42.0)))
        self.pipes.append(Pipe(x=88.0, gap_y=random.uniform(20.0, 42.0)))

    def get_final_score(self) -> int:
        return self.score

    def get_status_text(self) -> str:
        return f"SCORE: {self.score}"

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

        # 1. Edge-triggered flap input
        is_flap_held = bool(p1_inputs & {"up", "action_a", "action_b"})
        event_flap = bool(p1_events and (set(p1_events) & {"up", "action_a", "action_b"}))

        if (is_flap_held and not self._prev_flap_input) or event_flap:
            self.bird_vy = self.FLAP_IMPULSE
            self.play_sfx("jump")
        self._prev_flap_input = is_flap_held

        # 2. Bird Physics
        self.bird_vy += self.GRAVITY * dt
        self.bird_vy = min(80.0, self.bird_vy)
        self.bird_y += self.bird_vy * dt

        # 3. Ground & Ceiling Collisions
        bird_w = 4.0
        bird_h = 4.0
        if self.bird_y + bird_h >= self.GROUND_Y:
            self.bird_y = self.GROUND_Y - bird_h
            self.winner = 1
            self.play_sfx("crash")
            self.play_sfx("game_over")
            return
        elif self.bird_y < 0:
            self.bird_y = 0.0
            self.bird_vy = 0.0

        # 4. Scroll Pipes & Check Collisions
        keep_pipes: list[Pipe] = []
        for p in self.pipes:
            p.x -= self.SCROLL_SPEED * dt

            # Score increment when passing pipe
            if not p.passed and (p.x + self.PIPE_W) < self.bird_x:
                p.passed = True
                self.score += 1
                self.play_sfx("point")

            # Check Pipe Collision
            # Horizontal overlap
            if (self.bird_x + bird_w > p.x) and (self.bird_x < p.x + self.PIPE_W):
                gap_top = p.gap_y - self.GAP_H / 2.0
                gap_bottom = p.gap_y + self.GAP_H / 2.0
                # If bird is not cleanly inside the vertical gap:
                if self.bird_y < gap_top or (self.bird_y + bird_h) > gap_bottom:
                    self.winner = 1
                    self.play_sfx("crash")
                    self.play_sfx("game_over")
                    return

            if p.x + self.PIPE_W > -2.0:
                keep_pipes.append(p)

        self.pipes = keep_pipes

        # Spawn new pipe when the last pipe scrolls past 34px from right
        if not self.pipes or self.pipes[-1].x < 64.0 - 32.0:
            spawn_x = max(64.0, self.pipes[-1].x + 34.0 if self.pipes else 64.0)
            self.pipes.append(Pipe(x=spawn_x, gap_y=random.uniform(20.0, 42.0)))

    def render(self, draw: ImageDraw.ImageDraw) -> None:
        # Sky background (deep dusk cyan/navy)
        draw.rectangle([0, 0, 63, 63], fill=(8, 20, 36))

        # Pipes
        for p in self.pipes:
            ix = int(p.x)
            gap_top = int(p.gap_y - self.GAP_H / 2.0)
            gap_bot = int(p.gap_y + self.GAP_H / 2.0)

            # Upper Pipe
            if gap_top > 0:
                draw.rectangle([ix, 0, ix + int(self.PIPE_W) - 1, gap_top], fill=(40, 200, 60), outline=(20, 130, 40))
                # Upper collar lip
                draw.rectangle([ix - 1, gap_top - 3, ix + int(self.PIPE_W), gap_top], fill=(50, 225, 75), outline=(20, 140, 40))

            # Lower Pipe
            if gap_bot < int(self.GROUND_Y):
                draw.rectangle([ix, gap_bot, ix + int(self.PIPE_W) - 1, int(self.GROUND_Y) - 1], fill=(40, 200, 60), outline=(20, 130, 40))
                # Lower collar lip
                draw.rectangle([ix - 1, gap_bot, ix + int(self.PIPE_W), gap_bot + 3], fill=(50, 225, 75), outline=(20, 140, 40))

        # Ground
        gy = int(self.GROUND_Y)
        draw.line([0, gy, 63, gy], fill=(70, 230, 60))
        draw.rectangle([0, gy + 1, 63, 63], fill=(150, 100, 40))

        # Bird (animated sprite)
        bx = int(self.bird_x)
        by = int(self.bird_y)
        # Body
        draw.rectangle([bx, by, bx + 3, by + 3], fill=(255, 225, 0))
        # Wing
        wing_offset = 1 if self.bird_vy < 0 else 2
        draw.point((bx + 1, by + wing_offset), fill=(255, 255, 255))
        # Eye
        draw.point((bx + 3, by + 1), fill=(255, 255, 255))
        draw.point((bx + 3, by + 1), fill=(0, 0, 0))
        # Beak
        draw.point((bx + 4, by + 2), fill=(255, 120, 0))

        # Score HUD
        draw.text((2, 1), str(self.score), fill=(255, 255, 255), font=self.font)

        # Game Over Banner
        if self.is_game_over():
            draw_boxed_message(
                draw,
                title="GAME OVER",
                rect=(4, 23, 59, 41),
                bg_color=(10, 10, 16),
                border_color=(255, 210, 0),
                title_color=(255, 70, 70),
            )
