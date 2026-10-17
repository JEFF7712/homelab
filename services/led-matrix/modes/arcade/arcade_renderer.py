"""Retro Arcade mode renderer with automated multi-game attract rotation."""

from __future__ import annotations

from PIL import Image, ImageDraw

from modes.base import ModeRenderer, RenderContext

from .base import ArcadeGame
from .breakout import BreakoutGame
from .donkey_kong import DonkeyKongGame
from .pacman import PacmanGame
from .pong import PongGame
from .snake import SnakeGame
from .space_invaders import SpaceInvadersGame
from .tetris import TetrisGame


class RetroArcadeRenderer(ModeRenderer):
    def __init__(self, cycle_interval: float = 60.0) -> None:
        self.games: list[ArcadeGame] = [
            PongGame(),
            SnakeGame(),
            BreakoutGame(),
            TetrisGame(),
            PacmanGame(),
            SpaceInvadersGame(),
            DonkeyKongGame(),
        ]
        self.cycle_interval = cycle_interval
        self.current_idx = 0
        self.game_timer = 0.0

    @property
    def current_game(self) -> ArcadeGame:
        return self.games[self.current_idx]

    def _next_game(self) -> None:
        self.current_idx = (self.current_idx + 1) % len(self.games)
        self.current_game.reset()
        self.game_timer = 0.0

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

        return frame
