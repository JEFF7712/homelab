"""ModeRenderer coordinating lobby, web controllers, Pong 2P, and Tron 2P."""

import asyncio
import math
import time
from typing import Optional
from PIL import Image, ImageDraw

from modes.base import ModeRenderer, RenderContext
from web_controller.server import MultiplayerHub
from .base import MultiplayerGame
from .lobby import LobbyRenderer
from .pong_2p import Pong2P
from .tron_2p import Tron2P


class MultiplayerArcadeRenderer(ModeRenderer):
    """Orchestrates 2-Player Interactive Arcade Mode with mobile web controllers."""

    def __init__(self, hub: MultiplayerHub) -> None:
        self.hub = hub
        self.lobby = LobbyRenderer(hub)
        self.active_game: Optional[MultiplayerGame] = None
        self.last_update = time.time()
        self.status_broadcast_timer = 0.0

        # Wire up hub callbacks
        self.hub.on_start_game = self._on_start_game
        self.hub.on_rematch = self._on_rematch

    def _on_start_game(self, game_name: str, solo: bool) -> None:
        if game_name == "tron":
            self.active_game = Tron2P(solo_vs_ai=solo)
        else:
            self.active_game = Pong2P(solo_vs_ai=solo)
        self.hub.phase = "countdown"
        self.hub.countdown_remaining = 3.0

    def _on_rematch(self) -> None:
        if self.active_game:
            self.active_game.reset()
        else:
            self._on_start_game(self.hub.selected_game, self.hub.solo_vs_ai)
        self.hub.phase = "countdown"
        self.hub.countdown_remaining = 3.0

    def reset(self) -> None:
        self.last_update = time.time()

    def render(self, ctx: Optional[RenderContext] = None, size: int = 64) -> Image.Image:
        if ctx is not None:
            dt = ctx.dt
            self.last_update = time.time()
        else:
            now = time.time()
            dt = min(0.1, max(0.001, now - self.last_update))
            self.last_update = now

        image = Image.new("RGB", (size, size), (0, 0, 0))
        draw = ImageDraw.Draw(image)

        self.lobby.update(dt)

        if self.hub.phase == "lobby":
            self.lobby.render_lobby(draw)

        elif self.hub.phase == "countdown":
            self.hub.countdown_remaining -= dt
            count = int(math.ceil(self.hub.countdown_remaining))
            if self.hub.countdown_remaining <= 0:
                self.hub.phase = "playing"
                if self.active_game:
                    self.active_game.reset()
            else:
                self.lobby.render_countdown(draw, count)

        elif self.hub.phase == "playing" and self.active_game:
            self.active_game.update(dt, self.hub.p1_inputs, self.hub.p2_inputs)
            self.active_game.render(draw)

            # Broadcast status text to controllers periodically
            self.status_broadcast_timer += dt
            if self.status_broadcast_timer >= 0.4:
                self.status_broadcast_timer = 0.0
                self.hub.broadcast_sync({
                    "type": "game_update",
                    "score_text": self.active_game.get_status_text(),
                })

            if self.active_game.is_game_over():
                self.hub.phase = "gameover"
                winner = self.active_game.winner
                self.hub.broadcast_sync({
                    "type": "game_over",
                    "winner": winner,
                    "winner_text": f"P{winner} WINS!",
                })


        elif self.hub.phase == "gameover":
            winner = self.active_game.winner if self.active_game else 1
            self.lobby.render_game_over(draw, winner)

        return image
