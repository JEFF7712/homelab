"""LedFx DDP/UDP stream receiver and renderer with seamless visualizer fallback."""

from __future__ import annotations

import socket
import threading
import time

import numpy as np
from PIL import Image

from modes.base import ModeRenderer, RenderContext
from modes.visualizer import VisualizerRenderer


class LedFxReceiver:
    """Thread-safe background UDP server for LedFx DDP (port 4048) and raw pixel streams."""

    def __init__(self, host: str = "0.0.0.0", port: int = 4048, size: int = 64) -> None:
        self.host = host
        self.port = port
        self.size = size
        self.expected_bytes = size * size * 3  # 12288 bytes for 64x64
        self._buffer = bytearray(self.expected_bytes)
        self._current_frame: Image.Image | None = None
        self._last_packet_time = 0.0
        self._lock = threading.Lock()
        self._running = False
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True, name="ledfx-udp")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass

    def has_recent_frame(self, timeout: float = 3.0) -> bool:
        with self._lock:
            return (self._current_frame is not None) and (time.monotonic() - self._last_packet_time < timeout)

    def get_frame(self) -> Image.Image | None:
        with self._lock:
            return self._current_frame

    def _listen_loop(self) -> None:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind((self.host, self.port))
            self._sock.settimeout(1.0)
            print(f"[LedFx] Listening for pixel stream on UDP {self.host}:{self.port}", flush=True)
        except OSError as e:
            print(f"[LedFx] UDP bind error ({self.host}:{self.port}): {e}", flush=True)
            return

        while self._running:
            try:
                data, _ = self._sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                break

            if data:
                self.process_packet(data)

    def process_packet(self, data: bytes) -> None:
        now = time.monotonic()
        # 1. DDP protocol header (10 bytes: flags, seq, type, id, offset, len)
        if len(data) >= 10 and (data[0] & 0x40):
            offset = int.from_bytes(data[4:8], "big")
            length = int.from_bytes(data[8:10], "big")
            payload = data[10:10 + length]
            with self._lock:
                end = min(offset + len(payload), self.expected_bytes)
                if offset < self.expected_bytes:
                    self._buffer[offset:end] = payload[:end - offset]
                self._last_packet_time = now
                is_push = bool(data[0] & 0x01)
                if is_push or end >= self.expected_bytes:
                    arr = np.frombuffer(self._buffer, dtype=np.uint8).reshape((self.size, self.size, 3))
                    self._current_frame = Image.fromarray(arr, mode="RGB")
        # 2. Exact raw frame (12288 bytes)
        elif len(data) == self.expected_bytes:
            with self._lock:
                self._last_packet_time = now
                arr = np.frombuffer(data, dtype=np.uint8).reshape((self.size, self.size, 3))
                self._current_frame = Image.fromarray(arr, mode="RGB")
        # 3. WLED DRGB (0x02, wait, RGB...)
        elif len(data) > 2 and data[0] == 0x02 and len(data) - 2 <= self.expected_bytes:
            payload = data[2:]
            with self._lock:
                self._last_packet_time = now
                end = min(len(payload), self.expected_bytes)
                self._buffer[:end] = payload[:end]
                arr = np.frombuffer(self._buffer, dtype=np.uint8).reshape((self.size, self.size, 3))
                self._current_frame = Image.fromarray(arr, mode="RGB")


class LedFxRenderer(ModeRenderer):
    """Renders real-time LedFx frames; falls back to VisualizerRenderer when LedFx is idle."""

    def __init__(
        self,
        receiver: LedFxReceiver | None = None,
        fallback_renderer: ModeRenderer | None = None,
        stream_timeout: float = 3.0,
    ) -> None:
        self.receiver = receiver or LedFxReceiver()
        self.fallback = fallback_renderer or VisualizerRenderer()
        self.stream_timeout = stream_timeout

    def render(self, ctx: RenderContext, size: int = 64) -> Image.Image:
        if self.receiver.has_recent_frame(timeout=self.stream_timeout):
            frame = self.receiver.get_frame()
            if frame is not None:
                if frame.size != (size, size):
                    frame = frame.resize((size, size), Image.Resampling.NEAREST)
                return frame

        # Seamless fallback to procedural music visualizer
        return self.fallback.render(ctx, size=size)
