#!/usr/bin/env python3
"""Stream spinning Spotify album art to a Pico W over acknowledged USB or TCP."""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import requests
import serial
from PIL import Image, ImageDraw, ImageOps
from serial.tools import list_ports

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
CURRENTLY_PLAYING_URL = "https://api.spotify.com/v1/me/player/currently-playing"
SCOPE = "user-read-currently-playing"


def write_private_file(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def frame_to_rgb565(image: Image.Image, brightness: float = 1.0) -> bytes:
    if image.size != (64, 64):
        raise ValueError("Matrix frames must be 64x64")
    arr = np.asarray(image.convert("RGB"), dtype=np.float32)
    arr = np.clip(arr * brightness, 0, 255).astype(np.uint16)
    r = (arr[..., 0] >> 3) & 0x1F
    g = (arr[..., 1] >> 2) & 0x3F
    b = (arr[..., 2] >> 3) & 0x1F
    return ((r << 11) | (g << 5) | b).astype("<u2").tobytes()


class USBMatrixStreamer:
    def __init__(self, port: str | None = None) -> None:
        self.port = port
        self.serial: serial.Serial | None = None
        self.frames = 0
        self.failures = 0
        self.last_error = 0.0

    def send_frame(self, image: Image.Image, brightness: float = 1.0) -> bool:
        raw_bytes = frame_to_rgb565(image, brightness)
        try:
            if self.serial is None:
                port = self.port
                if port is None:
                    ports = [
                        p.device
                        for p in list_ports.comports()
                        if p.vid == 0x239A
                        and p.interface == "CircuitPython CDC2 control"
                    ]
                    if len(ports) != 1:
                        raise OSError(
                            "Select a dedicated Pico data endpoint with --serial-port"
                        )
                    port = ports[0]
                self.serial = serial.Serial(
                    port, 115200, timeout=1.0, write_timeout=1.0, exclusive=True
                )
                self.serial.reset_input_buffer()
            if self.serial.write(b"S565" + raw_bytes) != 8196:
                raise OSError("Incomplete USB frame write")
            if self.serial.read(1) != b"A":
                raise OSError("Pico did not acknowledge the USB frame")
            self.frames += 1
            return True
        except (OSError, serial.SerialException) as error:
            self.failures += 1
            self.close()
            now = time.monotonic()
            if now - self.last_error >= 5:
                print(
                    f"[Matrix USB] {error}; reconnecting with the latest frame",
                    flush=True,
                )
                self.last_error = now
            time.sleep(0.25)
            return False

    def close(self) -> None:
        if self.serial is not None:
            self.serial.close()
            self.serial = None


class TCPMatrixStreamer:
    """Send one little-endian RGB565 frame, then wait for display completion."""

    def __init__(self, host: str, port: int = 9999) -> None:
        self.host = host
        self.port = port
        self.sock: socket.socket | None = None
        self.frames = 0
        self.failures = 0
        self.last_error = 0.0

    def send_frame(self, image: Image.Image, brightness: float = 1.0) -> bool:
        raw_bytes = frame_to_rgb565(image, brightness)
        try:
            if self.sock is None:
                self.sock = socket.create_connection(
                    (self.host, self.port), timeout=1.0
                )
                self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                ready = bytearray()
                while len(ready) < 4:
                    part = self.sock.recv(4 - len(ready))
                    if not part:
                        raise ConnectionError(
                            "Pico disconnected before the ready handshake"
                        )
                    ready.extend(part)
                if ready != b"R565":
                    raise ConnectionError("Pico returned an invalid ready handshake")
            self.sock.sendall(raw_bytes)
            if self.sock.recv(1) != b"A":
                raise ConnectionError(
                    "Pico disconnected or returned an invalid frame acknowledgement"
                )
            self.frames += 1
            return True
        except OSError as error:
            self.failures += 1
            self.close()
            now = time.monotonic()
            if now - self.last_error >= 5:
                print(
                    f"[Matrix] {error}; reconnecting with the latest frame", flush=True
                )
                self.last_error = now
            time.sleep(0.25)
            return False

    def close(self) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None


# --- Album Art Rendering (Spinning Record) ---
def render_record(art: Image.Image | None, angle: float, size: int = 64) -> Image.Image:
    frame = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    if art is None:
        return frame.convert("RGB")

    margin = max(2, size // 32)
    disc_size = size - margin * 2

    # Crop album art to disk, rotate by angle
    art_square = ImageOps.fit(
        art, (disc_size, disc_size), method=Image.Resampling.LANCZOS
    )
    rotated = art_square.rotate(angle, resample=Image.Resampling.BICUBIC)

    # Circular mask
    disc_mask = Image.new("L", (disc_size, disc_size), 0)
    mask_draw = ImageDraw.Draw(disc_mask)
    mask_draw.ellipse((0, 0, disc_size - 1, disc_size - 1), fill=255)
    frame.paste(rotated.convert("RGBA"), (margin, margin), disc_mask)

    # Draw vinyl edges and center spindle label
    draw = ImageDraw.Draw(frame, "RGBA")
    outer = (margin, margin, size - margin - 1, size - margin - 1)
    draw.ellipse(outer, outline=(6, 6, 6, 255), width=max(1, size // 32))

    center = size // 2
    label_radius = max(5, size // 11)
    hole_radius = max(2, size // 25)

    draw.ellipse(
        (
            center - label_radius,
            center - label_radius,
            center + label_radius,
            center + label_radius,
        ),
        fill=(16, 16, 16, 210),
        outline=(220, 220, 220, 90),
    )
    draw.ellipse(
        (
            center - hole_radius,
            center - hole_radius,
            center + hole_radius,
            center + hole_radius,
        ),
        fill=(0, 0, 0, 255),
    )
    return frame.convert("RGB")


def render_idle(size: int = 64) -> Image.Image:
    frame = Image.new("RGB", (size, size), (0, 0, 0))
    draw = ImageDraw.Draw(frame)
    margin = max(2, size // 32)
    draw.ellipse(
        (margin, margin, size - margin - 1, size - margin - 1),
        outline=(40, 40, 40),
        width=1,
    )
    center = size // 2
    radius = max(3, size // 18)
    draw.ellipse(
        (center - radius, center - radius, center + radius, center + radius),
        fill=(20, 20, 20),
    )
    return frame


def render_test_album(size: int = 64) -> Image.Image:
    """Creates a colorful mock album art for testing without Spotify."""
    img = Image.new("RGB", (size, size), (20, 20, 50))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, size // 2, size // 2), fill=(240, 50, 50))
    draw.rectangle((size // 2, 0, size, size // 2), fill=(50, 200, 50))
    draw.rectangle((0, size // 2, size // 2, size), fill=(50, 80, 240))
    draw.rectangle((size // 2, size // 2, size, size), fill=(240, 200, 40))
    draw.text((size // 4, size // 3), "VINYL", fill=(255, 255, 255))
    return img


# --- Spotify OAuth and API Client ---
class LocalCallbackServer:
    def __init__(self, host: str, port: int, path: str, expected_state: str) -> None:
        self.expected_state = expected_state
        self.code: str | None = None
        self.error: str | None = None
        self._server = HTTPServer((host, port), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)
                state = params.get("state", [None])[0]
                if state != outer.expected_state:
                    outer.error = "State mismatch"
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"State mismatch")
                    return

                outer.code = params.get("code", [None])[0]
                outer.error = params.get("error", [None])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h1>Spotify Authorized!</h1><p>You can close this tab now.</p></body></html>"
                )

            def log_message(self, format: str, *args: Any) -> None:
                pass

        return Handler

    def wait_for_code(self, timeout: float = 120) -> str:
        start = time.time()
        while time.time() - start < timeout:
            if self.code:
                self._server.shutdown()
                return self.code
            if self.error:
                self._server.shutdown()
                raise RuntimeError(f"Spotify auth error: {self.error}")
            time.sleep(0.2)
        self._server.shutdown()
        raise TimeoutError("Timed out waiting for Spotify OAuth callback.")


class SpotifyQuotaExceeded(RuntimeError):
    pass


class SpotifyClient:
    def __init__(
        self, client_id: str, client_secret: str, redirect_uri: str, token_cache: Path
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.token_cache = token_cache
        self.token = self._load_token()

    def get_currently_playing(self, retry_auth: bool = True) -> dict[str, Any] | None:
        token = self._valid_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        try:
            resp = requests.get(
                CURRENTLY_PLAYING_URL,
                params={"additional_types": "track,episode"},
                headers=headers,
                timeout=5,
            )
            if resp.status_code == 204:
                return None
            if resp.status_code == 401 and retry_auth:
                self._refresh_token()
                return self.get_currently_playing(retry_auth=False)
            if resp.status_code == 429:
                try:
                    reason = resp.json().get("error", {}).get("reason")
                except (ValueError, AttributeError):
                    reason = None
                if reason == "QUOTA_EXCEEDED":
                    raise SpotifyQuotaExceeded(
                        "Developer-account quota exhausted; API polling stopped until manual restart"
                    )
                try:
                    retry = int(resp.headers.get("Retry-After", "60"))
                except ValueError:
                    retry = 60
                print(
                    f"[Spotify] Rate limited; waiting at least {max(retry, 1)} seconds",
                    flush=True,
                )
                time.sleep(max(retry, 1))
                return None
            resp.raise_for_status()
            return resp.json()
        except SpotifyQuotaExceeded:
            raise
        except Exception as e:
            print(f"[Spotify] Request error: {e}")
            return None

    def _valid_access_token(self) -> str:
        if not self.token:
            # Check environment variable SPOTIFY_REFRESH_TOKEN
            env_refresh = os.environ.get("SPOTIFY_REFRESH_TOKEN")
            if env_refresh:
                self.token = {"refresh_token": env_refresh, "expires_at": 0}
            else:
                self.token = self._authorize()

        if time.time() >= float(self.token.get("expires_at", 0)):
            self._refresh_token()

        return str(self.token["access_token"])

    def _load_token(self) -> dict[str, Any] | None:
        if self.token_cache.exists():
            try:
                return json.loads(self.token_cache.read_text("utf-8"))
            except Exception:
                pass
        return None

    def _save_token(self, token: dict[str, Any]) -> None:
        self.token_cache.parent.mkdir(parents=True, exist_ok=True)
        token["expires_at"] = time.time() + int(token.get("expires_in", 3600)) - 60
        if (
            self.token
            and "refresh_token" in self.token
            and "refresh_token" not in token
        ):
            token["refresh_token"] = self.token["refresh_token"]
        write_private_file(self.token_cache, json.dumps(token, indent=2) + "\n")
        self.token = token

    def _refresh_token(self) -> None:
        refresh_token = (
            self.token.get("refresh_token")
            if self.token
            else os.environ.get("SPOTIFY_REFRESH_TOKEN")
        )
        if not refresh_token:
            self.token = self._authorize()
            return

        creds = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()
        resp = requests.post(
            TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            headers={
                "Authorization": f"Basic {creds}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=10,
        )
        resp.raise_for_status()
        self._save_token(resp.json())

    def _authorize(self) -> dict[str, Any]:
        state = secrets.token_urlsafe(16)
        parsed = urllib.parse.urlparse(self.redirect_uri)
        callback = LocalCallbackServer(
            parsed.hostname or "127.0.0.1",
            parsed.port or 8888,
            parsed.path or "/callback",
            state,
        )

        query = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "response_type": "code",
                "redirect_uri": self.redirect_uri,
                "scope": SCOPE,
                "state": state,
            }
        )
        auth_url = f"{AUTH_URL}?{query}"
        print("\n=== Spotify Authorization Needed ===")
        print(f"Open this URL in your browser:\n{auth_url}\n")

        code = callback.wait_for_code()
        creds = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()
        resp = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
            },
            headers={
                "Authorization": f"Basic {creds}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=10,
        )
        resp.raise_for_status()
        token = resp.json()
        self._save_token(token)
        print("Spotify authorized successfully!\n")
        return token


# --- Main Streaming Loop ---
def run_streamer(args: argparse.Namespace) -> None:
    pico_ip = args.pico_ip or os.environ.get("PICO_MATRIX_IP", "10.0.20.131")
    port = args.port
    fps = max(1, min(60, args.fps))
    frame_interval = 1.0 / fps
    rotation_speed = args.rotation_speed  # degrees / sec

    streamer = (
        USBMatrixStreamer(args.serial_port)
        if args.transport == "usb"
        else TCPMatrixStreamer(pico_ip, port)
    )
    print(f"Streaming over {args.transport} at {fps} FPS", flush=True)

    # If test mode is enabled, stream synthetic spinning vinyl
    if args.test:
        print("Running in TEST mode (synthetic album art, spinning continuously)")
        test_art = render_test_album(64)
        angle = 0.0
        last_t = time.monotonic()
        while True:
            t0 = time.monotonic()
            dt = t0 - last_t
            last_t = t0
            angle = (angle + dt * rotation_speed) % 360.0

            frame = render_record(test_art, angle, size=64)
            streamer.send_frame(frame, brightness=args.brightness)
            elapsed = time.monotonic() - t0
            sleep_time = max(0.0, frame_interval - elapsed)
            time.sleep(sleep_time)

    # Spotify Client setup
    client_id = args.client_id or os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = args.client_secret or os.environ.get("SPOTIFY_CLIENT_SECRET")
    redirect_uri = args.redirect_uri or os.environ.get(
        "SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback"
    )
    token_cache = Path.home() / ".cache" / "spotify_matrix_token.json"

    if not client_id or not client_secret:
        print(
            "ERROR: SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set via env vars or flags."
        )
        print("To test matrix hardware without Spotify credentials, run with: --test")
        sys.exit(1)

    client = SpotifyClient(client_id, client_secret, redirect_uri, token_cache)

    # State tracking
    current_art_url: str | None = None
    current_art_img: Image.Image | None = None
    is_playing = False
    angle = 0.0
    poll_interval = max(15.0, args.poll_interval)
    print(f"Spotify polling interval: {poll_interval:g} seconds", flush=True)

    def update_spotify() -> None:
        nonlocal current_art_url, current_art_img, is_playing
        while True:
            next_poll = poll_interval
            try:
                data = client.get_currently_playing()
                if data and "item" in data and data["item"]:
                    is_playing = bool(data.get("is_playing", False))
                    images = data["item"].get("album", {}).get("images", [])
                    if images:
                        # Grab highest quality image (or smallest suitable)
                        url = images[0].get("url")
                        if url != current_art_url:
                            resp = requests.get(url, timeout=10)
                            current_art_img = Image.open(BytesIO(resp.content)).convert(
                                "RGB"
                            )
                            current_art_url = url
                            track_name = data["item"].get("name", "Unknown Track")
                            artist_name = ", ".join(
                                a.get("name", "")
                                for a in data["item"].get("artists", [])
                            )
                            print(f"[Playing] {track_name} - {artist_name}")
                else:
                    is_playing = False
                if not is_playing:
                    next_poll = max(60.0, poll_interval)
            except SpotifyQuotaExceeded as e:
                is_playing = False
                print(f"[Spotify] {e}", flush=True)
                return
            except Exception as e:
                print(f"[Spotify Poll Error] {e}")

            time.sleep(next_poll)

    poll_thread = threading.Thread(target=update_spotify, daemon=True)
    poll_thread.start()

    last_frame_t = time.monotonic()

    print("Spotify Matrix Streamer active. Press Ctrl+C to stop.")
    while True:
        now = time.monotonic()
        dt = now - last_frame_t
        last_frame_t = now

        if current_art_img is not None:
            if is_playing:
                angle = (angle + dt * rotation_speed) % 360.0
            frame = render_record(current_art_img, angle, size=64)
            streamer.send_frame(frame, brightness=args.brightness)
        else:
            # Idle silhouette
            frame = render_idle(size=64)
            streamer.send_frame(frame, brightness=args.brightness)

        elapsed = time.monotonic() - now
        sleep_time = max(0.0, frame_interval - elapsed)
        time.sleep(sleep_time)


def main() -> None:
    parser = argparse.ArgumentParser(description="Spotify Matrix USB/TCP Streamer")
    parser.add_argument(
        "--transport",
        choices=("usb", "tcp"),
        default="tcp",
        help="Transport (default: tcp)",
    )
    parser.add_argument(
        "--serial-port",
        default=None,
        help="Dedicated USB data port (default: auto-detect)",
    )
    parser.add_argument(
        "--pico-ip", default=None, help="Pico W IP address (default: 10.0.20.131)"
    )
    parser.add_argument(
        "--port", type=int, default=9999, help="TCP port (default: 9999)"
    )
    parser.add_argument("--fps", type=int, default=20, help="Frame rate (default: 20)")
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=15,
        help="Spotify polling interval in seconds (minimum/default: 15; paused/idle: at least 60)",
    )
    parser.add_argument(
        "--rotation-speed",
        type=float,
        default=45.0,
        help="Degrees per second (default: 45.0)",
    )
    parser.add_argument(
        "--brightness",
        type=float,
        default=0.7,
        help="Brightness multiplier 0.1 - 1.0 (default: 0.7)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Stream synthetic test vinyl without Spotify",
    )
    parser.add_argument("--client-id", default=None, help="Spotify Client ID")
    parser.add_argument("--client-secret", default=None, help="Spotify Client Secret")
    parser.add_argument("--redirect-uri", default=None, help="Spotify Redirect URI")

    args = parser.parse_args()
    try:
        run_streamer(args)
    except KeyboardInterrupt:
        print("\nExiting streamer.")


if __name__ == "__main__":
    main()
