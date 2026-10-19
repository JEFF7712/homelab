"""Asyncio WebSocket & HTTP server for the mobile web controller."""

import asyncio
import json
import logging
import math
from collections import deque
from collections.abc import Callable
from typing import Any

from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Response

from modes.multiplayer.high_scores import HighScoreManager
from modes.multiplayer.registry import GAME_REGISTRY

from .controller_html import CONTROLLER_HTML

logger = logging.getLogger("web_controller")

# Auto-discovered game configuration derived from GAME_REGISTRY
GAMES_CONFIG = GAME_REGISTRY.get_games_config()


class MultiplayerHub:
    """Central state and communication hub for connected mobile gamepads."""

    def __init__(self, high_score_manager: HighScoreManager | None = None) -> None:
        self.p1_ws: ServerConnection | None = None
        self.p2_ws: ServerConnection | None = None
        self.spectators: set[ServerConnection] = set()

        self.high_score_manager = high_score_manager or HighScoreManager()

        self.phase: str = "lobby"  # 'lobby', 'countdown', 'playing', 'gameover'
        self.countdown_remaining: float = 0.0
        self.selected_game_index: int = 0
        games = GAME_REGISTRY.get_games_config()
        self.selected_game: str = games[0]["id"] if games else "pong"
        self.solo_vs_ai: bool = False
        self.matrix_view: str = "auto"  # 'auto', 'carousel', 'qr'


        # Active input state per player (keys currently held down)
        self.p1_inputs: set[str] = set()
        self.p2_inputs: set[str] = set()

        # Discrete key tap events for step/grid/turn based games & menu navigation
        self.p1_events: deque[str] = deque(maxlen=10)
        self.p2_events: deque[str] = deque(maxlen=10)

        # Continuous tilt axis in [-1, 1]; None when the controller is not tilting
        self.p1_analog: float | None = None
        self.p2_analog: float | None = None

        self.loop: asyncio.AbstractEventLoop | None = None

        # Callbacks for game lifecycle
        self.on_start_game: Callable[[str, bool], None] | None = None
        self.on_rematch: Callable[[], None] | None = None
        self.on_menu_exit: Callable[[], None] | None = None

    @property
    def p1_connected(self) -> bool:
        return self.p1_ws is not None

    @property
    def p2_connected(self) -> bool:
        return self.p2_ws is not None

    def get_high_score(self, game_id: str) -> int:
        return self.high_score_manager.get_score(game_id)

    def record_game_score(self, game_id: str, score: int) -> tuple[bool, int]:
        return self.high_score_manager.update_score(game_id, score)

    def play_sfx(self, sound_name: str) -> None:
        """Broadcast a retro 8-bit sound effect to all connected web controllers."""
        self.broadcast_sync({"type": "sfx", "name": sound_name})

    def get_lobby_state(self) -> dict[str, Any]:
        return {
            "type": "lobby_state",
            "p1_connected": self.p1_connected,
            "p2_connected": self.p2_connected or self.solo_vs_ai,
            "phase": self.phase,
            "selected_game": self.selected_game,
            "selected_game_index": self.selected_game_index,
            "games": GAMES_CONFIG,
            "solo_vs_ai": self.solo_vs_ai,
            "matrix_view": self.matrix_view,
            "high_scores": self.high_score_manager.get_all(),
        }

    async def broadcast(self, data: dict[str, Any]) -> None:
        payload = json.dumps(data)
        targets: list[ServerConnection] = []
        if self.p1_ws:
            targets.append(self.p1_ws)
        if self.p2_ws:
            targets.append(self.p2_ws)
        targets.extend(self.spectators)

        for ws in targets:
            try:
                await ws.send(payload)
            except Exception:
                pass

    def broadcast_sync(self, data: dict[str, Any]) -> None:
        """Thread-safe non-blocking broadcast from main rendering thread."""
        if self.loop and self.loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(self.broadcast(data), self.loop)
            except Exception:
                pass

    def register_client(self, ws: ServerConnection) -> int:
        """Assign client to Player 1, Player 2, or Spectator (0)."""
        addr = getattr(ws, "remote_address", "unknown")
        if self.p1_ws is None:
            self.p1_ws = ws
            logger.info("Player 1 connected from %s", addr)
            return 1
        elif self.p2_ws is None and not self.solo_vs_ai:
            self.p2_ws = ws
            logger.info("Player 2 connected from %s", addr)
            return 2
        else:
            self.spectators.add(ws)
            logger.info("Spectator connected from %s", addr)
            return 0

    def unregister_client(self, ws: ServerConnection) -> None:
        if self.p1_ws == ws:
            self.p1_ws = None
            self.p1_inputs.clear()
            self.p1_events.clear()
            self.p1_analog = None
            logger.info("Player 1 disconnected")
        elif self.p2_ws == ws:
            self.p2_ws = None
            self.p2_inputs.clear()
            self.p2_events.clear()
            self.p2_analog = None
            logger.info("Player 2 disconnected")
        else:
            self.spectators.discard(ws)

        # If in game and a required player leaves, revert to lobby
        if self.phase in ("countdown", "playing") and (
            not self.p1_connected or (not self.solo_vs_ai and not self.p2_connected)
        ):
            self.phase = "lobby"

    def handle_input(self, player: int, action: str, state: str) -> None:
        target_inputs = self.p1_inputs if player == 1 else self.p2_inputs
        target_events = self.p1_events if player == 1 else self.p2_events
        actions = [action]
        if action == "action_a":
            actions.append("a")
        elif action == "a":
            actions.append("action_a")
        elif action == "action_b":
            actions.append("b")
        elif action == "b":
            actions.append("action_b")

        if state == "down":
            for a in actions:
                target_inputs.add(a)
                target_events.append(a)
        elif state == "up":
            for a in actions:
                target_inputs.discard(a)

    def handle_analog(self, player: int, value: object) -> None:
        axis: float | None = None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if math.isfinite(value):
                axis = max(-1.0, min(1.0, float(value)))
        if player == 1:
            self.p1_analog = axis
        else:
            self.p2_analog = axis

    def consume_events(self, player: int) -> list[str]:
        target_events = self.p1_events if player == 1 else self.p2_events
        events = list(target_events)
        target_events.clear()
        return events

    def clear_inputs(self) -> None:
        self.p1_inputs.clear()
        self.p2_inputs.clear()
        self.p1_events.clear()
        self.p2_events.clear()
        self.p1_analog = None
        self.p2_analog = None

    def navigate_menu(self, direction: str) -> None:
        if direction in ("left", "prev"):
            self.selected_game_index = (self.selected_game_index - 1) % len(GAMES_CONFIG)
        elif direction in ("right", "next"):
            self.selected_game_index = (self.selected_game_index + 1) % len(GAMES_CONFIG)
        self.selected_game = GAMES_CONFIG[self.selected_game_index]["id"]
        if not GAMES_CONFIG[self.selected_game_index]["supports_2p"]:
            self.solo_vs_ai = True
        self.broadcast_sync(self.get_lobby_state())

    def set_selected_game(self, game_id: str) -> None:
        for idx, g in enumerate(GAMES_CONFIG):
            if g["id"] == game_id:
                self.selected_game_index = idx
                self.selected_game = game_id
                if not g["supports_2p"]:
                    self.solo_vs_ai = True
                break
        self.broadcast_sync(self.get_lobby_state())

    def toggle_matrix_qr(self) -> None:
        if self.matrix_view == "qr":
            self.matrix_view = "carousel"
        else:
            self.matrix_view = "qr"
        self.broadcast_sync(self.get_lobby_state())

    def exit_to_menu(self) -> None:
        self.phase = "lobby"
        self.clear_inputs()
        if self.on_menu_exit:
            self.on_menu_exit()
        self.broadcast_sync(self.get_lobby_state())

    def trigger_game_start(self, game: str, solo: bool) -> None:
        self.selected_game = game
        for idx, g in enumerate(GAMES_CONFIG):
            if g["id"] == game:
                self.selected_game_index = idx
                break
        self.solo_vs_ai = solo
        self.clear_inputs()
        self.phase = "countdown"
        self.countdown_remaining = 3.0
        if self.on_start_game:
            self.on_start_game(game, solo)


class WebControllerServer:
    """Embedded HTTP and WebSocket server running inside the LED matrix streamer."""

    def __init__(self, hub: MultiplayerHub, host: str = "0.0.0.0", port: int = 8000) -> None:
        self.hub = hub
        self.host = host
        self.port = port
        self.server: Any = None
        self._server_task: asyncio.Task[None] | None = None

    def process_request(self, connection: ServerConnection, request: Any) -> Response | None:
        """Serve HTTP GET endpoints or return None for WebSocket upgrades."""
        path = getattr(request, "path", "/")
        if path in ("/", "/index.html"):
            headers = Headers([
                ("Content-Type", "text/html; charset=utf-8"),
                ("Cache-Control", "no-cache, no-store, must-revalidate"),
            ])
            return Response(200, "OK", headers=headers, body=CONTROLLER_HTML.encode("utf-8"))
        elif path == "/health":
            headers = Headers([("Content-Type", "application/json")])
            return Response(200, "OK", headers=headers, body=b'{"status":"healthy"}')
        elif path == "/scores":
            headers = Headers([
                ("Content-Type", "application/json"),
                ("Cache-Control", "no-cache"),
            ])
            body = json.dumps({"scores": self.hub.high_score_manager.get_all()}).encode("utf-8")
            return Response(200, "OK", headers=headers, body=body)
        elif path == "/ws":
            return None  # Upgrade to WebSocket
        return Response(404, "Not Found", headers=Headers(), body=b"404 Not Found")


    async def handle_connection(self, ws: ServerConnection) -> None:
        role = self.hub.register_client(ws)
        try:
            # 1. Send player role assignment
            await ws.send(json.dumps({"type": "assign", "player": role}))
            # 2. Broadcast updated lobby state to all
            await self.hub.broadcast(self.hub.get_lobby_state())

            # If a game is already running, notify the newly connected player
            if self.hub.phase == "playing":
                await ws.send(json.dumps({
                    "type": "game_start",
                    "game": self.hub.selected_game,
                    "solo": self.hub.solo_vs_ai,
                }))

            # 3. Handle incoming client messages
            async for raw_msg in ws:
                try:
                    data = json.loads(raw_msg)
                except Exception:
                    continue

                msg_type = data.get("type")
                if msg_type == "input" and role in (1, 2):
                    action = data.get("action", "")
                    state = data.get("state", "down")
                    self.hub.handle_input(role, action, state)
                elif msg_type == "analog" and role in (1, 2):
                    self.hub.handle_analog(role, data.get("x"))
                elif msg_type == "menu_nav" and role in (1, 2):
                    self.hub.navigate_menu(data.get("dir", "next"))
                elif msg_type == "select_game" and role in (1, 2):
                    self.hub.set_selected_game(data.get("game", "pong"))
                elif msg_type == "toggle_qr":
                    self.hub.toggle_matrix_qr()
                elif msg_type == "menu_exit":
                    self.hub.exit_to_menu()
                elif msg_type == "start_game" and role in (1, 2):
                    game = data.get("game", self.hub.selected_game)
                    solo = bool(data.get("solo", self.hub.solo_vs_ai))
                    self.hub.trigger_game_start(game, solo)
                    await self.hub.broadcast({
                        "type": "game_start",
                        "game": game,
                        "solo": solo,
                    })
                elif msg_type == "rematch":
                    if self.hub.on_rematch:
                        self.hub.on_rematch()
                    await self.hub.broadcast({
                        "type": "game_start",
                        "game": self.hub.selected_game,
                        "solo": self.hub.solo_vs_ai,
                    })
        finally:
            self.hub.unregister_client(ws)
            await self.hub.broadcast(self.hub.get_lobby_state())

    async def start(self) -> None:
        logger.info("Starting Web Controller Server on %s:%d...", self.host, self.port)
        self.server = await serve(
            self.handle_connection,
            self.host,
            self.port,
            process_request=self.process_request,
        )
        logger.info("Web Controller Server is listening on http://%s:%d", self.host, self.port)

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            logger.info("Web Controller Server stopped")


def start_background_server(
    hub: MultiplayerHub,
    host: str = "0.0.0.0",
    port: int = 8000,
    stop_event: Any = None,
) -> Any:
    """Launch the WebControllerServer in a dedicated background daemon thread."""
    import threading

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        hub.loop = loop
        server = WebControllerServer(hub, host, port)

        async def _main() -> None:
            await server.start()
            while stop_event is None or not stop_event.is_set():
                await asyncio.sleep(0.5)
            await server.stop()

        try:
            loop.run_until_complete(_main())
        finally:
            loop.close()

    thread = threading.Thread(target=_runner, daemon=True, name="web-controller")
    thread.start()
    return thread
