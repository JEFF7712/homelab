"""Nonblocking sound events and bounded visual delay for bedroom fireworks."""

from __future__ import annotations

import json
import socket
import time
import uuid
from collections import deque

from PIL import Image


class FireworksAudio:
    def __init__(self, host: str | None, port: int = 8891) -> None:
        self.address = (socket.gethostbyname(host), port) if host else None
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setblocking(False)
        self.session = uuid.uuid4().hex
        self.seq = 0
        self.last_sent = -1.0
        self.active = False
        self.frames: deque[tuple[float, Image.Image]] = deque(maxlen=90)
        self.sent = 0
        self.errors = 0

    def update(
        self, active: bool, volume: float, events: list[dict], now: float
    ) -> None:
        active = active and self.address is not None and volume > 0
        changed = active != self.active
        self.active = active
        if not active:
            self.frames.clear()
            events = []
        if not self.address or (
            not changed and not events and now - self.last_sent < 0.2
        ):
            return
        self.seq += 1
        packet = dict(
            session=self.session,
            seq=self.seq,
            sent_at=time.time(),
            active=active,
            volume=volume,
            events=events[:16] if active else [],
        )
        try:
            self.socket.sendto(
                json.dumps(packet, allow_nan=False).encode(), self.address
            )
            self.sent += 1
        except (OSError, ValueError):
            self.errors += 1
        self.last_sent = now

    def delay(self, frame: Image.Image, now: float, seconds: float) -> Image.Image:
        if not self.active or seconds <= 0:
            self.frames.clear()
            return frame
        self.frames.append((now, frame))
        target = now - min(seconds, 1.0)
        while len(self.frames) > 1 and self.frames[1][0] <= target:
            self.frames.popleft()
        if self.frames[0][0] > target:
            return Image.new("RGB", frame.size)
        return self.frames[0][1]

    def close(self) -> None:
        self.update(False, 0.0, [], time.monotonic())
        self.socket.close()
