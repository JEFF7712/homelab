"""Read cached Home Assistant playback state and follow entity events."""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
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
        self,
        base_url: str,
        token: str,
        entity: str,
        stale_after: float = 120,
        on_update: Callable[[PlaybackState], None] | None = None,
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
        self.on_update = on_update
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
            if self.on_update:
                try:
                    self.on_update(PlaybackState())
                except Exception:
                    pass
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
                notify = True
            else:
                notify = False
        if notify and self.on_update:
            try:
                self.on_update(state)
            except Exception:
                pass

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
    def __init__(self, source: HomeAssistantSource | HybridPlaybackSource) -> None:
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


def parse_spotify_state(
    data: dict[str, Any] | None, now: float | None = None
) -> PlaybackState:
    timestamp = time.time() if now is None else now
    if not data:
        return PlaybackState(
            available=False,
            playing=False,
            title="",
            artist="",
            artwork_url="",
            updated_at=timestamp,
        )
    is_playing = bool(data.get("is_playing", False))
    item = data.get("item")
    if not item:
        return PlaybackState(
            available=False,
            playing=is_playing,
            title="",
            artist="",
            artwork_url="",
            updated_at=timestamp,
        )
    title = str(item.get("name") or "")
    if "artists" in item:
        artist = ", ".join(
            str(a.get("name") or "") for a in item.get("artists", []) if a.get("name")
        )
    elif "show" in item:
        artist = str(item.get("show", {}).get("name") or "")
    else:
        artist = ""
    images = item.get("album", {}).get("images") or item.get("images") or []
    artwork_url = str(images[0].get("url") or "") if images else ""
    return PlaybackState(
        available=True,
        playing=is_playing,
        title=title,
        artist=artist,
        artwork_url=artwork_url,
        updated_at=timestamp,
    )


class HybridPlaybackSource:
    def __init__(
        self,
        ha_source: HomeAssistantSource,
        spotify_client: Any,
        poll_interval_playing: float = 1.0,
        poll_interval_paused: float | None = None,
        poll_interval_idle: float = 5.0,
        quota_cooldown: float = 3600.0,
        error_cooldown: float = 15.0,
        active_window: float = 180.0,
    ) -> None:
        self.ha_source = ha_source
        self.spotify_client = spotify_client
        self.poll_interval_playing = poll_interval_playing
        self.poll_interval_paused = poll_interval_paused
        self.poll_interval_idle = poll_interval_idle
        self.quota_cooldown = quota_cooldown
        self.error_cooldown = error_cooldown
        self.active_window = active_window

        self._spotify_state = PlaybackState()
        self._spotify_available = False
        self._last_active_at = time.monotonic()
        self._fallback_until = 0.0
        self._fallback_reason = ""
        self._active_provider = "home_assistant"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake_event = threading.Event()
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="spotify-poll"
        )

        # Wake poller on Home Assistant state updates
        self.ha_source.on_update = self._on_ha_update

    def _on_ha_update(self, _state: PlaybackState) -> None:
        self._wake_event.set()

    def start(self) -> None:
        self.ha_source.start()
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._wake_event.set()
        self._thread.join(timeout=10)
        self.ha_source.close()

    @property
    def connected(self) -> bool:
        with self._lock:
            spotify_ok = self._spotify_available
        return spotify_ok or self.ha_source.connected

    @property
    def active_provider(self) -> str:
        now = time.monotonic()
        with self._lock:
            if now < self._fallback_until or not self._spotify_available:
                return "home_assistant"
            return "spotify"

    def _switch_provider(self, new_provider: str, reason: str = "") -> None:
        if self._active_provider != new_provider:
            self._active_provider = new_provider
            extra = f" ({reason})" if reason else ""
            print(
                f"[Hybrid] Switched active provider to {new_provider.upper()}{extra}",
                flush=True,
            )

    def snapshot(self) -> PlaybackState:
        now = time.monotonic()
        with self._lock:
            use_ha = now < self._fallback_until or not self._spotify_available
            spotify_state = self._spotify_state
        if use_ha:
            return self.ha_source.snapshot()
        return spotify_state

    def fetch_artwork(self, picture: str) -> Image.Image:
        return self.ha_source.fetch_artwork(picture)

    def _poll_once(self) -> float:
        """Poll Spotify once and return the delay until the next poll."""
        from spotify_matrix_streamer import SpotifyQuotaExceeded, SpotifyRateLimited

        try:
            data = self.spotify_client.get_currently_playing(
                retry_auth=True,
                raise_on_rate_limit=True,
                raise_on_error=True,
            )
            new_state = parse_spotify_state(data)
            now = time.monotonic()
            with self._lock:
                self._spotify_state = new_state
                self._spotify_available = True
                self._switch_provider("spotify")
                if new_state.playing:
                    self._last_active_at = now

            if new_state.playing:
                return self.poll_interval_playing
            elif new_state.available:
                if self.poll_interval_paused is not None:
                    return self.poll_interval_paused
                # If recently active (within active_window), poll at playing rate so resume is immediate
                if now - self._last_active_at < self.active_window:
                    return self.poll_interval_playing
                return min(self.poll_interval_idle, max(2.5, self.poll_interval_playing * 2.0))
            else:
                if now - self._last_active_at < 60.0:
                    return min(2.5, self.poll_interval_idle)
                return self.poll_interval_idle
        except SpotifyRateLimited as error:
            retry = max(error.retry_after, 1.0)
            with self._lock:
                self._fallback_until = time.monotonic() + retry
                self._fallback_reason = f"rate limited ({retry:g}s)"
                self._spotify_available = False
                self._switch_provider("home_assistant", self._fallback_reason)
            return retry
        except SpotifyQuotaExceeded:
            with self._lock:
                self._fallback_until = time.monotonic() + self.quota_cooldown
                self._fallback_reason = "quota exceeded"
                self._spotify_available = False
                self._switch_provider("home_assistant", self._fallback_reason)
            return self.quota_cooldown
        except Exception as error:
            with self._lock:
                self._fallback_until = time.monotonic() + self.error_cooldown
                self._fallback_reason = f"error ({type(error).__name__}: {error})"
                self._spotify_available = False
                self._switch_provider("home_assistant", self._fallback_reason)
            return self.error_cooldown

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            with self._lock:
                fallback_left = max(0.0, self._fallback_until - now)
            if fallback_left > 0:
                self._wake_event.wait(timeout=min(fallback_left, 1.0))
                self._wake_event.clear()
                continue

            delay = self._poll_once()
            self._wake_event.wait(timeout=delay)
            self._wake_event.clear()
