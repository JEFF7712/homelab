"""ModeRenderer coordinating lobby, web controllers, Pong 2P, and Tron 2P."""

import logging
import math
import threading
import time

from PIL import Image, ImageDraw

from modes.base import ModeRenderer, RenderContext
from web_controller.server import MultiplayerHub

from .base import MultiplayerGame
from .lobby import LobbyRenderer
from .registry import GAME_REGISTRY

logger = logging.getLogger("multiplayer_arcade")


class MultiplayerArcadeRenderer(ModeRenderer):
    """Orchestrates Interactive Arcade Mode with mobile web controllers."""

    def __init__(self, hub: MultiplayerHub) -> None:
        self.hub = hub
        self.lobby = LobbyRenderer(hub)
        self.active_game: MultiplayerGame | None = None
        self.last_update = time.time()
        self.status_broadcast_timer = 0.0
        self._lock = threading.RLock()
        self.countdown_pip = 4
        self.is_new_high = False
        self.best_score = 0

        # Wire up hub callbacks
        self.hub.on_start_game = self._on_start_game
        self.hub.on_rematch = self._on_rematch
        self.hub.on_menu_exit = self._on_menu_exit

    def _on_start_game(self, game_name: str, solo: bool) -> None:
        with self._lock:
            self.active_game = GAME_REGISTRY.create_game(game_name, solo=solo)
            if self.active_game:
                self.active_game.sfx_callback = self.hub.play_sfx

            self.countdown_pip = 4
            self.is_new_high = False
            self.best_score = self.hub.get_high_score(game_name)
            self.hub.phase = "countdown"
            self.hub.countdown_remaining = 3.0

    def _on_rematch(self) -> None:
        with self._lock:
            if self.active_game:
                self.active_game.sfx_callback = self.hub.play_sfx
                self.active_game.reset()
            else:
                self._on_start_game(self.hub.selected_game, self.hub.solo_vs_ai)
            self.countdown_pip = 4
            self.is_new_high = False
            self.best_score = self.hub.get_high_score(self.hub.selected_game)
            self.hub.phase = "countdown"
            self.hub.countdown_remaining = 3.0

    def _on_menu_exit(self) -> None:
        with self._lock:
            self.active_game = None
            self.hub.phase = "lobby"
            self.hub.play_sfx("button")


    def reset(self) -> None:
        with self._lock:
            self.last_update = time.time()

    def render(self, ctx: RenderContext | None = None, size: int = 64) -> Image.Image:
        if ctx is not None:
            dt = ctx.dt
            self.last_update = time.time()
        else:
            now = time.time()
            dt = min(0.1, max(0.001, now - self.last_update))
            self.last_update = now

        image = Image.new("RGB", (size, size), (0, 0, 0))
        draw = ImageDraw.Draw(image)

        with self._lock:
            self.lobby.update(dt)
            phase = self.hub.phase
            game = self.active_game

            if phase == "lobby":
                self.lobby.render_lobby(draw)

            elif phase == "countdown":
                self.hub.countdown_remaining -= dt
                count = math.ceil(self.hub.countdown_remaining)
                if count != self.countdown_pip and count > 0:
                    self.countdown_pip = count
                    self.hub.play_sfx("pip")

                if self.hub.countdown_remaining <= 0:
                    self.hub.phase = "playing"
                    self.hub.play_sfx("start")
                    if game:
                        game.reset()
                else:
                    self.lobby.render_countdown(draw, count)

            elif phase == "playing" and game:
                p1_events = self.hub.consume_events(1)
                p2_events = self.hub.consume_events(2)
                try:
                    game.update(
                        dt,
                        self.hub.p1_inputs,
                        self.hub.p2_inputs,
                        p1_events=p1_events,
                        p2_events=p2_events,
                    )
                    game.render(draw)
                except Exception:  # noqa: BLE001
                    logger.exception("Error in active game %s", type(game).__name__)
                    self.hub.phase = "lobby"
                    self.active_game = None
                    return image

                # Broadcast status text to controllers periodically
                self.status_broadcast_timer += dt
                if self.status_broadcast_timer >= 0.35:
                    self.status_broadcast_timer = 0.0
                    status_text = ""
                    try:
                        status_text = game.get_status_text()
                    except Exception:  # noqa: BLE001, S110
                        pass
                    self.hub.broadcast_sync({
                        "type": "game_update",
                        "score_text": status_text,
                    })

                if game.is_game_over():
                    self.hub.phase = "gameover"
                    winner = game.winner
                    is_solo = (
                        self.hub.selected_game in ("snake", "breakout", "tetris")
                        or self.hub.solo_vs_ai
                    )
                    winner_text = "GAME OVER" if is_solo else f"P{winner} WINS!"
                    status_text = ""
                    try:
                        status_text = game.get_status_text()
                    except Exception:  # noqa: BLE001, S110
                        pass

                    final_score = game.get_final_score()
                    is_new, best = self.hub.record_game_score(self.hub.selected_game, final_score)
                    self.is_new_high = is_new
                    self.best_score = best
                    if is_new:
                        self.hub.play_sfx("high_score")

                    self.hub.broadcast_sync({
                        "type": "game_over",
                        "winner": winner,
                        "winner_text": winner_text,
                        "score_text": status_text,
                        "score": final_score,
                        "high_score": best,
                        "new_high": is_new,
                    })

            elif phase == "gameover":
                winner = game.winner if game else 1
                is_solo = (
                    self.hub.selected_game in ("snake", "breakout", "tetris")
                    or self.hub.solo_vs_ai
                )
                score_text = ""
                if game:
                    try:
                        score_text = game.get_status_text()
                    except Exception:  # noqa: BLE001, S110
                        pass
                self.lobby.render_game_over(
                    draw,
                    winner,
                    is_solo=is_solo,
                    score_text=score_text,
                    new_high=self.is_new_high,
                    high_score=self.best_score,
                )

        return image

