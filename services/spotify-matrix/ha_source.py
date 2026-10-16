"""Read cached Home Assistant playback state and follow entity events."""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from PIL import Image
from websockets.exceptions import WebSocketException
from websockets.sync.client import connect
from websockets.sync.connection import Connection


@dataclass(frozen=True)
class PlaybackState:
    available: bool = False
    playing: bool = False
    title: str = ""
    artist: str = ""
    artwork_url: str = ""
    updated_at: float = 0


class HomeAssistantSource:
    def __init__(
        self, base_url: str, token: str, entity: str, stale_after: float = 120
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.netloc
            or parsed.username
            or parsed.password
        ):
            raise ValueError("HA URL must be an HTTP(S) origin without credentials")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("HA URL must not contain a path, query, or fragment")
        if not token or not entity.startswith("media_player.") or stale_after <= 0:
            raise ValueError(
                "HA token, media_player entity and positive stale timeout are required"
            )
        self.base_url = base_url.rstrip("/")
        self.ws_url = urlunsplit(
            (
                "wss" if parsed.scheme == "https" else "ws",
                parsed.netloc,
                "/api/websocket",
                "",
                "",
            )
        )
        self.token = token
        self.entity = entity
        self.stale_after = stale_after
        self._state = PlaybackState()
        self._compact: dict[str, Any] = {}
        self._connected = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._socket: Connection | None = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="ha-events")
        self._heartbeat_interval = 20.0
        self._heartbeat_timeout = 10.0

    def start(self) -> None:
        self._thread.start()

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    def close(self) -> None:
        self._stop.set()
        if self._socket:
            self._socket.close()
        self._thread.join(timeout=12)

    def snapshot(self) -> PlaybackState:
        with self._lock:
            state = self._state
            connected = self._connected
        if not connected or (
            state.playing and time.time() - state.updated_at > self.stale_after
        ):
            return replace(state, available=False, playing=False)
        return state

    def apply_state(self, value: dict[str, Any] | None) -> None:
        if value is None:
            with self._lock:
                self._state = PlaybackState()
            return
        if value.get("entity_id") != self.entity:
            return
        attributes = value.get("attributes") or {}
        try:
            updated_at = datetime.fromisoformat(value["last_updated"]).timestamp()
        except (KeyError, TypeError, ValueError):
            updated_at = 0.0
        state = PlaybackState(
            available=value.get("state") in ("playing", "paused"),
            playing=value.get("state") == "playing",
            title=str(attributes.get("media_title") or ""),
            artist=str(attributes.get("media_artist") or ""),
            artwork_url=str(attributes.get("entity_picture") or ""),
            updated_at=updated_at,
        )
        with self._lock:
            if updated_at >= self._state.updated_at:
                self._state = state

    def handle_event(self, message: dict[str, Any]) -> None:
        if message.get("type") == "event" and message.get("id") == 1:
            event = message.get("event", {})
            if self.entity in event.get("r", []):
                self._compact = {}
                self.apply_state(None)
                return
            if self.entity in event.get("a", {}):
                self._compact = event["a"][self.entity]
            elif self.entity in event.get("c", {}):
                diff = event["c"][self.entity]
                additions = diff.get("+", {})
                if "lc" in additions and "lu" not in additions:
                    self._compact.pop("lu", None)
                for key, value in additions.items():
                    if key == "a":
                        self._compact.setdefault("a", {}).update(value)
                    else:
                        self._compact[key] = value
                for key in diff.get("-", {}).get("a", []):
                    self._compact.get("a", {}).pop(key, None)
            else:
                return
            timestamp = self._compact.get("lu", self._compact.get("lc", 0))
            self.apply_state(
                {
                    "entity_id": self.entity,
                    "state": self._compact.get("s"),
                    "attributes": dict(self._compact.get("a", {})),
                    "last_updated": datetime.fromtimestamp(timestamp, UTC).isoformat(),
                }
            )

    def _session(self, socket: Connection) -> None:
        if json.loads(socket.recv(timeout=10)).get("type") != "auth_required":
            raise PermissionError("Unexpected HA authentication response")
        socket.send(json.dumps({"type": "auth", "access_token": self.token}))
        if json.loads(socket.recv(timeout=10)).get("type") != "auth_ok":
            raise PermissionError("HA authentication failed")
        socket.send(
            json.dumps(
                {
                    "id": 1,
                    "type": "subscribe_entities",
                    "entity_ids": [self.entity],
                }
            )
        )
        response = json.loads(socket.recv(timeout=10))
        if (
            response.get("id") != 1
            or response.get("type") != "result"
            or not response.get("success")
        ):
            raise PermissionError("HA entity subscription failed")
        self._compact = {}
        self.apply_state(None)
        with self._lock:
            self._connected = True
        print(f"[HA] Subscribed to {self.entity}", flush=True)
        last_ping = time.monotonic()
        pending_ping = None
        ping_id = 2
        while not self._stop.is_set():
            now = time.monotonic()
            if pending_ping is not None and now - last_ping > self._heartbeat_timeout:
                raise TimeoutError("HA heartbeat timed out")
            if pending_ping is None and now - last_ping >= self._heartbeat_interval:
                socket.send(json.dumps({"id": ping_id, "type": "ping"}))
                pending_ping = ping_id
                ping_id += 1
                last_ping = now
            try:
                message = json.loads(socket.recv(timeout=1))
                if (
                    message.get("type") == "pong" and message.get("id") == pending_ping
                ) or (message.get("type") == "event" and message.get("id") == 1):
                    pending_ping = None
                    last_ping = time.monotonic()
                self.handle_event(message)
            except TimeoutError:
                continue

    def _run(self) -> None:
        retry = 1.0
        while not self._stop.is_set():
            connected_at = time.monotonic()
            try:
                with connect(
                    self.ws_url,
                    ping_interval=None,
                    proxy=None,
                    open_timeout=10,
                    close_timeout=2,
                    max_size=1024 * 1024,
                ) as socket:
                    self._socket = socket
                    self._session(socket)
            except (
                OSError,
                WebSocketException,
                ValueError,
                PermissionError,
                requests.RequestException,
            ) as error:
                if not self._stop.is_set():
                    print(
                        f"[HA] Connection unavailable ({type(error).__name__}); reconnecting",
                        flush=True,
                    )
            finally:
                self._socket = None
                with self._lock:
                    self._connected = False
            if time.monotonic() - connected_at > 30:
                retry = 1.0
            if self._stop.wait(retry):
                break
            retry = min(retry * 2, 60)

    def fetch_artwork(self, picture: str) -> Image.Image:
        url = urljoin(self.base_url + "/", picture)
        parsed = urlsplit(url)
        same_origin = (parsed.scheme, parsed.netloc) == (
            urlsplit(self.base_url).scheme,
            urlsplit(self.base_url).netloc,
        )
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("Invalid artwork URL")
        if same_origin:
            if not parsed.path.startswith("/api/media_player_proxy/"):
                raise ValueError("Only the HA artwork proxy may receive HA credentials")
            headers = {"Authorization": f"Bearer {self.token}"}
        else:
            if parsed.scheme != "https" or parsed.hostname in (
                "api.spotify.com",
                "accounts.spotify.com",
            ):
                raise ValueError(
                    "External artwork must use HTTPS and cannot use Spotify API endpoints"
                )
            headers = {}
        with requests.get(
            url, headers=headers, timeout=10, allow_redirects=False, stream=True
        ) as response:
            if response.status_code != 200:
                raise ConnectionError(f"Artwork returned HTTP {response.status_code}")
            payload = bytearray()
            for chunk in response.iter_content(65536):
                payload.extend(chunk)
                if len(payload) > 4 * 1024 * 1024:
                    raise ValueError("Artwork exceeds 4 MiB")
        with Image.open(BytesIO(payload)) as image:
            result = image.convert("RGB")
            result.thumbnail((128, 128))
            return result


class ArtworkCache:
    def __init__(self, source: HomeAssistantSource) -> None:
        self.source = source
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="artwork")
        self._future: Future[Image.Image] | None = None
        self._loading = ""
        self._failed = ""
        self._retry_at = 0.0
        self._images: OrderedDict[str, Image.Image] = OrderedDict()

    def get(self, url: str) -> Image.Image | None:
        if self._future and self._future.done():
            try:
                self._images[self._loading] = self._future.result()
                self._images.move_to_end(self._loading)
                while len(self._images) > 8:
                    self._images.popitem(last=False)
            except (OSError, ValueError, requests.RequestException) as error:
                self._failed = self._loading
                self._retry_at = time.monotonic() + 60
                print(
                    f"[Artwork] Fetch failed ({type(error).__name__}); retry delayed",
                    flush=True,
                )
            self._future = None
        if not url:
            return None
        if url in self._images:
            self._images.move_to_end(url)
            return self._images[url]
        if self._future is None and (
            url != self._failed or time.monotonic() >= self._retry_at
        ):
            self._loading = url
            self._future = self._executor.submit(self.source.fetch_artwork, url)
        return None

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)
