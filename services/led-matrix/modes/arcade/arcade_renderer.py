"""Retro Arcade mode renderer with automated multi-game attract rotation."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from modes.base import ModeRenderer, RenderContext

from .base import ArcadeGame
from .breakout import BreakoutGame
from .dungeon import DungeonGame
from .pong import PongGame
from .snake import SnakeGame
from .tetris import TetrisGame


class RetroArcadeRenderer(ModeRenderer):
    def __init__(self, cycle_interval: float = 60.0) -> None:
        self.games: list[ArcadeGame] = [
            PongGame(),
            SnakeGame(),
            BreakoutGame(),
            TetrisGame(),
            DungeonGame(),
        ]
        self.cycle_interval = cycle_interval
        self.current_idx = 0
        self.game_timer = 0.0
        self.banner_timer = 1.8  # show intro banner at start of each game
        self.font_title = ImageFont.load_default(size=11)
        self.font_sub = ImageFont.load_default(size=8)

    @property
    def current_game(self) -> ArcadeGame:
        return self.games[self.current_idx]

    def _next_game(self) -> None:
        self.current_idx = (self.current_idx + 1) % len(self.games)
        self.current_game.reset()
        self.game_timer = 0.0
        self.banner_timer = 1.8

    def render(self, ctx: RenderContext, size: int = 64) -> Image.Image:
        dt = ctx.dt
        self.game_timer += dt

        # Switch games when cycle interval expires
        if self.game_timer >= self.cycle_interval:
            self._next_game()

        # Update active game simulation
        self.current_game.update(dt)

        # Render active game
        frame = Image.new("RGB", (size, size), (0, 0, 0))
        draw = ImageDraw.Draw(frame)
        self.current_game.draw(frame, draw, size=size)

        # Intro Banner overlay for first 1.8s of each game
        if self.banner_timer > 0:
            self.banner_timer -= dt
            self._draw_intro_banner(frame, draw, size)

        return frame

    def _draw_intro_banner(
        self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int
    ) -> None:
        title = self.current_game.name
        sub = "DEMO MODE"

        # Dim background banner
        box_y1 = 20
        box_y2 = 45
        draw.rectangle([(0, box_y1), (size - 1, box_y2)], fill=(10, 14, 25))
        draw.line([(0, box_y1), (size - 1, box_y1)], fill=(0, 200, 255), width=1)
        draw.line([(0, box_y2), (size - 1, box_y2)], fill=(0, 200, 255), width=1)

        # Title
        bbox_t = draw.textbbox((0, 0), title, font=self.font_title)
        tw = bbox_t[2] - bbox_t[0]
        draw.text(
            ((size - tw) // 2, box_y1 + 3),
            title,
            fill=(255, 255, 255),
            font=self.font_title,
        )

        # Subtitle
        bbox_s = draw.textbbox((0, 0), sub, font=self.font_sub)
        sw = bbox_s[2] - bbox_s[0]
        draw.text(
            ((size - sw) // 2, box_y1 + 14),
            sub,
            fill=(255, 200, 40),
            font=self.font_sub,
        )
