"""Asyncio WebSocket & HTTP server for the mobile web controller."""

import asyncio
import json
import logging
from typing import Any, Callable

from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Response

from .controller_html import CONTROLLER_HTML

logger = logging.getLogger("web_controller")


class MultiplayerHub:
    """Central state and communication hub for connected mobile gamepads."""

    def __init__(self) -> None:
        self.p1_ws: ServerConnection | None = None
        self.p2_ws: ServerConnection | None = None
        self.spectators: set[ServerConnection] = set()

        self.phase: str = "lobby"  # 'lobby', 'countdown', 'playing', 'gameover'
        self.countdown_remaining: float = 0.0
        self.selected_game: str = "pong"
        self.solo_vs_ai: bool = False

        # Active input state per player (keys currently held down)
        self.p1_inputs: set[str] = set()
        self.p2_inputs: set[str] = set()

        self.loop: asyncio.AbstractEventLoop | None = None

        # Callbacks for game lifecycle
        self.on_start_game: Callable[[str, bool], None] | None = None
        self.on_rematch: Callable[[], None] | None = None

    @property
    def p1_connected(self) -> bool:
        return self.p1_ws is not None

    @property
    def p2_connected(self) -> bool:
        return self.p2_ws is not None

    def get_lobby_state(self) -> dict[str, Any]:
        return {
            "type": "lobby_state",
            "p1_connected": self.p1_connected,
            "p2_connected": self.p2_connected or self.solo_vs_ai,
            "phase": self.phase,
            "selected_game": self.selected_game,
            "solo_vs_ai": self.solo_vs_ai,
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
            logger.info("Player 1 disconnected")
        elif self.p2_ws == ws:
            self.p2_ws = None
            self.p2_inputs.clear()
            logger.info("Player 2 disconnected")
        else:
            self.spectators.discard(ws)

        # If in game and a required player leaves, revert to lobby
        if self.phase in ("countdown", "playing"):
            if not self.p1_connected or (not self.solo_vs_ai and not self.p2_connected):
                self.phase = "lobby"

    def handle_input(self, player: int, action: str, state: str) -> None:
        target_inputs = self.p1_inputs if player == 1 else self.p2_inputs
        if state == "down":
            target_inputs.add(action)
        elif state == "up":
            target_inputs.discard(action)

    def trigger_game_start(self, game: str, solo: bool) -> None:
        self.selected_game = game
        self.solo_vs_ai = solo
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
                await ws.send(json.dumps({"type": "game_start", "game": self.hub.selected_game}))

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
                elif msg_type == "start_game" and role in (1, 2):
                    game = data.get("game", "pong")
                    solo = bool(data.get("solo", False))
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
