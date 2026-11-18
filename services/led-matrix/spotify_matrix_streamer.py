#!/usr/bin/env python3
"""Stream spinning Spotify album art to a Pico W over acknowledged USB or TCP."""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import select
import socket
import struct
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
from PIL import Image, ImageDraw, ImageEnhance, ImageOps
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


_BAYER_4X4 = np.array(
    [
        [0, 8, 2, 10],
        [12, 4, 14, 6],
        [3, 11, 1, 9],
        [15, 7, 13, 5],
    ],
    dtype=np.uint8,
)
_BAYER_64X64 = np.tile(_BAYER_4X4, (16, 16))
_R5_FROM_Q4 = np.array(
    [((q << 1) | (q >> 3)) << 11 for q in range(16)], dtype=np.uint16
)
_G6_FROM_Q4 = np.array([((q << 2) | (q >> 2)) << 5 for q in range(16)], dtype=np.uint16)
_B5_FROM_Q4 = np.array([(q << 1) | (q >> 3) for q in range(16)], dtype=np.uint16)

_DITHER_LUT_CACHE: dict[float, np.ndarray] = {}
_LINEAR_565_CACHE: dict[float, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
_RGB888_LUT_CACHE: dict[float, np.ndarray] = {}


def _dither_lut_4bit(brightness: float) -> np.ndarray:
    b_key = max(0.0, min(1.0, round(float(brightness), 4)))
    cached = _DITHER_LUT_CACHE.get(b_key)
    if cached is not None:
        return cached

    lut = np.zeros(256, dtype=np.uint8)
    if b_key > 0.0:
        for c in range(1, 256):
            scaled = (c / 255.0) * b_key
            raw_fp = 240.0 * (scaled**1.35)
            if raw_fp < 7.0:
                lut[c] = 0
            elif raw_fp < 16.0:
                # Keep intentional dim 1px strokes solid rather than dotted
                lut[c] = 16
            else:
                lut[c] = min(240, round(raw_fp))
    _DITHER_LUT_CACHE[b_key] = lut
    return lut


def _linear_565_luts(brightness: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    b_key = max(0.0, min(1.0, round(float(brightness), 4)))
    cached = _LINEAR_565_CACHE.get(b_key)
    if cached is not None:
        return cached
    vals = np.clip(np.arange(256, dtype=np.float32) * b_key, 0, 255).astype(np.uint16)
    luts = (
        ((vals >> 3) & 0x1F) << 11,
        ((vals >> 2) & 0x3F) << 5,
        (vals >> 3) & 0x1F,
    )
    _LINEAR_565_CACHE[b_key] = luts
    return luts


def _rgb888_lut(brightness: float) -> np.ndarray:
    b_key = max(0.0, min(1.0, round(float(brightness), 4)))
    cached = _RGB888_LUT_CACHE.get(b_key)
    if cached is not None:
        return cached
    lut = np.clip(np.round(np.arange(256, dtype=np.float32) * b_key), 0, 255).astype(
        np.uint8
    )
    _RGB888_LUT_CACHE[b_key] = lut
    return lut


def frame_to_rgb565(
    image: Image.Image, brightness: float = 1.0, *, dither: bool = True
) -> bytes:
    if image.size != (64, 64):
        raise ValueError("Matrix frames must be 64x64")
    if brightness <= 0.0:
        return bytes(8192)
    rgb = image if image.mode == "RGB" else image.convert("RGB")
    arr = np.asarray(rgb, dtype=np.uint8)
    if dither:
        lut = _dither_lut_4bit(brightness)
        fp = lut[arr]
        q_r = (fp[..., 0] + _BAYER_64X64) >> 4
        q_g = (fp[..., 1] + _BAYER_64X64) >> 4
        q_b = (fp[..., 2] + _BAYER_64X64) >> 4
        packed = _R5_FROM_Q4[q_r] | _G6_FROM_Q4[q_g] | _B5_FROM_Q4[q_b]
    else:
        r_lut, g_lut, b_lut = _linear_565_luts(brightness)
        packed = r_lut[arr[..., 0]] | g_lut[arr[..., 1]] | b_lut[arr[..., 2]]
    return packed.astype("<u2", copy=False).tobytes()


def frame_to_rgb888(image: Image.Image, brightness: float = 1.0) -> bytes:
    if image.size != (64, 64):
        raise ValueError("Matrix frames must be 64x64")
    if brightness <= 0.0:
        return bytes(12288)
    rgb = image if image.mode == "RGB" else image.convert("RGB")
    b_key = max(0.0, min(1.0, round(float(brightness), 4)))
    if b_key == 1.0:
        return rgb.tobytes()
    lut = _rgb888_lut(b_key)
    return lut[np.asarray(rgb, dtype=np.uint8)].tobytes()


def encode_r889_frame(
    image: Image.Image,
    brightness: float = 1.0,
    *,
    prev_rgb: bytes | None = None,
) -> tuple[bytes, bytes, int]:
    """Encode an unscaled 24-bit frame with hardware OE brightness and delta+RLE."""
    if image.size != (64, 64):
        raise ValueError("Matrix frames must be 64x64")
    clamped_b = max(0.0, min(1.0, float(brightness)))
    hw_bright = max(0, min(255, round(clamped_b * 255.0)))
    rgb_bytes = (image if image.mode == "RGB" else image.convert("RGB")).tobytes()

    if prev_rgb is None:
        return (
            b"F" + struct.pack("<BH", hw_bright, 12288) + rgb_bytes,
            rgb_bytes,
            hw_bright,
        )

    if rgb_bytes == prev_rgb:
        return b"R" + struct.pack("<BH", hw_bright, 0), rgb_bytes, hw_bright

    curr = np.frombuffer(rgb_bytes, dtype=np.uint8).reshape(4096, 3)
    prev = np.frombuffer(prev_rgb, dtype=np.uint8).reshape(4096, 3)
    curr_key = (
        (curr[:, 0].astype(np.uint32) << 16)
        | (curr[:, 1].astype(np.uint32) << 8)
        | curr[:, 2].astype(np.uint32)
    )
    prev_key = (
        (prev[:, 0].astype(np.uint32) << 16)
        | (prev[:, 1].astype(np.uint32) << 8)
        | prev[:, 2].astype(np.uint32)
    )
    unchanged = curr_key == prev_key
    span_edges = [
        0,
        *(np.flatnonzero(unchanged[1:] != unchanged[:-1]) + 1).tolist(),
        4096,
    ]

    out = bytearray()

    def flush_literal(px_start: int, px_end: int) -> None:
        pos = px_start
        while pos < px_end:
            chunk = min(64, px_end - pos)
            out.append(0x80 | (chunk - 1))
            out.extend(rgb_bytes[pos * 3 : (pos + chunk) * 3])
            pos += chunk

    for idx in range(len(span_edges) - 1):
        s = span_edges[idx]
        e = span_edges[idx + 1]
        if bool(unchanged[s]):
            rem = e - s
            while rem > 0:
                chunk = min(128, rem)
                out.append(chunk - 1)
                rem -= chunk
        else:
            seg_keys = curr_key[s:e]
            run_edges = [
                0,
                *(np.flatnonzero(seg_keys[1:] != seg_keys[:-1]) + 1).tolist(),
                e - s,
            ]
            lit_start: int | None = None
            for r_idx in range(len(run_edges) - 1):
                rs = run_edges[r_idx]
                re = run_edges[r_idx + 1]
                rlen = re - rs
                if rlen >= 2:
                    if lit_start is not None:
                        flush_literal(s + lit_start, s + rs)
                        lit_start = None
                    px_off = (s + rs) * 3
                    color3 = rgb_bytes[px_off : px_off + 3]
                    rem = rlen
                    while rem > 0:
                        chunk = min(64, rem)
                        out.append(0xC0 | (chunk - 1))
                        out.extend(color3)
                        rem -= chunk
                elif lit_start is None:
                    lit_start = rs
            if lit_start is not None:
                flush_literal(s + lit_start, e)

    if len(out) >= 12288:
        return (
            b"F" + struct.pack("<BH", hw_bright, 12288) + rgb_bytes,
            rgb_bytes,
            hw_bright,
        )
    return (
        b"R" + struct.pack("<BH", hw_bright, len(out)) + bytes(out),
        rgb_bytes,
        hw_bright,
    )


def decode_r889_frame(
    packet: bytes, canvas: bytearray | None = None
) -> tuple[bytearray, int]:
    """Reference R889 decoder matching the RP2040 C firmware state machine."""
    if len(packet) < 4:
        raise ValueError("Truncated R889 header")
    ftype = packet[0:1]
    hw_bright, plen = struct.unpack("<BH", packet[1:4])
    payload = packet[4:]
    if len(payload) != plen:
        raise ValueError("Mismatched R889 payload length")
    if ftype == b"F":
        if plen != 12288:
            raise ValueError("Full R889 frame must be 12288 bytes")
        return bytearray(payload), hw_bright
    if ftype != b"R" or canvas is None or len(canvas) != 12288:
        raise ValueError("Invalid R889 delta state")
    if plen == 0:
        return canvas, hw_bright
    px = 0
    pos = 0
    while pos < plen and px < 4096:
        op = payload[pos]
        pos += 1
        if (op & 0x80) == 0:
            count = op + 1
            px += count
        elif (op & 0x40) != 0:
            count = (op & 0x3F) + 1
            color3 = payload[pos : pos + 3]
            pos += 3
            canvas[px * 3 : (px + count) * 3] = color3 * count
            px += count
        else:
            count = (op & 0x3F) + 1
            nbytes = count * 3
            canvas[px * 3 : (px + count) * 3] = payload[pos : pos + nbytes]
            pos += nbytes
            px += count
    if pos != plen or px != 4096:
        raise ValueError("Corrupt R889 delta stream")
    return canvas, hw_bright


class USBMatrixStreamer:
    def __init__(
        self,
        port: str | None = None,
        *,
        pixel_format: str = "rgb565",
        keepalive_interval: float | None = None,
    ) -> None:
        if pixel_format not in ("rgb565", "rgb888", "r889"):
            raise ValueError("pixel_format must be 'rgb565', 'rgb888', or 'r889'")
        self.port = port
        self.pixel_format = pixel_format
        self.keepalive_interval = keepalive_interval
        self.serial: serial.Serial | None = None
        self.frames = 0
        self.skipped_frames = 0
        self.failures = 0
        self.last_error = 0.0
        self._last_payload: bytes | None = None
        self._last_r889_rgb: bytes | None = None
        self._last_r889_bright = -1
        self._last_send_time = 0.0

    def send_frame(self, image: Image.Image, brightness: float = 1.0) -> bool:
        if image.size != (64, 64):
            raise ValueError("Matrix frames must be 64x64")
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
                self._last_payload = None
                self._last_r889_rgb = None
                self._last_r889_bright = -1
            now = time.monotonic()
            if self.pixel_format == "r889":
                r889_body, rgb_bytes, hw_bright = encode_r889_frame(
                    image, brightness, prev_rgb=self._last_r889_rgb
                )
                if (
                    self.keepalive_interval is not None
                    and self._last_r889_rgb == rgb_bytes
                    and self._last_r889_bright == hw_bright
                    and (now - self._last_send_time) < self.keepalive_interval
                ):
                    self.skipped_frames += 1
                    return True
                packet = b"S889" + r889_body
                if self.serial.write(packet) != len(packet):
                    raise OSError("Incomplete USB frame write")
                if self.serial.read(1) != b"A":
                    raise OSError("Pico did not acknowledge the USB frame")
                self._last_r889_rgb = rgb_bytes
                self._last_r889_bright = hw_bright
                self._last_send_time = time.monotonic()
                self.frames += 1
                return True

            if self.pixel_format == "rgb888":
                header = b"S888"
                raw_bytes = frame_to_rgb888(image, brightness)
            else:
                header = b"S565"
                raw_bytes = frame_to_rgb565(image, brightness)
            if (
                self.keepalive_interval is not None
                and self._last_payload == raw_bytes
                and (now - self._last_send_time) < self.keepalive_interval
            ):
                self.skipped_frames += 1
                return True
            packet = header + raw_bytes
            if self.serial.write(packet) != len(packet):
                raise OSError("Incomplete USB frame write")
            if self.serial.read(1) != b"A":
                raise OSError("Pico did not acknowledge the USB frame")
            self._last_payload = raw_bytes
            self._last_send_time = time.monotonic()
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
        self._last_payload = None
        self._last_r889_rgb = None
        self._last_r889_bright = -1


class TCPMatrixStreamer:
    """Send R565, R888, or compressed R889 frames with pipelining and deduplication."""

    def __init__(
        self,
        host: str,
        port: int = 9999,
        *,
        max_in_flight: int = 1,
        keepalive_interval: float | None = None,
    ) -> None:
        if not 1 <= max_in_flight <= 4:
            raise ValueError("max_in_flight must be between 1 and 4")
        if keepalive_interval is not None and keepalive_interval <= 0:
            raise ValueError("keepalive_interval must be positive")
        self.host = host
        self.port = port
        self.max_in_flight = max_in_flight
        self.keepalive_interval = keepalive_interval
        self.sock: socket.socket | None = None
        self.protocol = "R565"
        self._pending_frames = 0
        self._oldest_pending_since = 0.0
        self._last_payload: bytes | None = None
        self._last_r889_rgb: bytes | None = None
        self._last_r889_bright = -1
        self._last_send_time = 0.0
        self.frames = 0
        self.skipped_frames = 0
        self.failures = 0
        self.last_error = 0.0
        self.last_ack = 0.0

    def _receive_acknowledgements(self, *, wait: bool = False) -> bool:
        if self.sock is None or not self._pending_frames:
            return False
        readable, _, _ = select.select([self.sock], [], [], 1.0 if wait else 0)
        if not readable:
            if wait or (
                self._oldest_pending_since
                and (time.monotonic() - self._oldest_pending_since) >= 1.0
            ):
                raise TimeoutError("Pico did not acknowledge displayed frames")
            return False
        replies = self.sock.recv(self._pending_frames)
        if not replies or replies != b"A" * len(replies):
            raise ConnectionError(
                "Pico disconnected or returned an invalid frame acknowledgement"
            )
        self._pending_frames -= len(replies)
        now = time.monotonic()
        self._oldest_pending_since = now if self._pending_frames else 0.0
        self.frames += len(replies)
        self.last_ack = now
        return True

    def send_frame(self, image: Image.Image, brightness: float = 1.0) -> bool:
        if image.size != (64, 64):
            raise ValueError("Matrix frames must be 64x64")
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
                if ready not in (b"R565", b"R888", b"R889"):
                    raise ConnectionError("Pico returned an invalid ready handshake")
                self.protocol = ready.decode("ascii")
                self._last_payload = None
                self._last_r889_rgb = None
                self._last_r889_bright = -1

            while self._receive_acknowledgements():
                pass
            now = time.monotonic()

            if self.protocol == "R889":
                wire_bytes, rgb_bytes, hw_bright = encode_r889_frame(
                    image, brightness, prev_rgb=self._last_r889_rgb
                )
                if (
                    self.keepalive_interval is not None
                    and self._last_r889_rgb == rgb_bytes
                    and self._last_r889_bright == hw_bright
                    and (now - self._last_send_time) < self.keepalive_interval
                ):
                    self.skipped_frames += 1
                    return True
                while self._pending_frames >= self.max_in_flight:
                    self._receive_acknowledgements(wait=True)
                self.sock.sendall(wire_bytes)
                send_t = time.monotonic()
                if self._pending_frames == 0:
                    self._oldest_pending_since = send_t
                self._pending_frames += 1
                self._last_r889_rgb = rgb_bytes
                self._last_r889_bright = hw_bright
                self._last_send_time = send_t
                self._receive_acknowledgements(wait=self.max_in_flight == 1)
                return True

            raw_bytes = (
                frame_to_rgb888(image, brightness)
                if self.protocol == "R888"
                else frame_to_rgb565(image, brightness)
            )
            effective_keepalive = (
                min(self.keepalive_interval, 0.35)
                if (self.keepalive_interval is not None and self.protocol == "R565")
                else self.keepalive_interval
            )
            if (
                effective_keepalive is not None
                and self._last_payload == raw_bytes
                and (now - self._last_send_time) < effective_keepalive
            ):
                self.skipped_frames += 1
                return True
            while self._pending_frames >= self.max_in_flight:
                self._receive_acknowledgements(wait=True)
            self.sock.sendall(raw_bytes)
            send_t = time.monotonic()
            if self._pending_frames == 0:
                self._oldest_pending_since = send_t
            self._pending_frames += 1
            self._last_payload = raw_bytes
            self._last_send_time = send_t
            self._receive_acknowledgements(wait=self.max_in_flight == 1)
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
        self._pending_frames = 0
        self._oldest_pending_since = 0.0
        self._last_payload = None
        self._last_r889_rgb = None
        self._last_r889_bright = -1


# --- Album Art Rendering (Spinning Record) ---
# Keep in sync with modes/spotify_vinyl.py: the panel's four-bit color depth
# mutes album art, so both vinyl paths apply the same fixed lift.
VINYL_SATURATION = 1.25
VINYL_CONTRAST = 1.12

_VINYL_GEOMETRY_CACHE: dict[int, tuple[int, int, Image.Image, Image.Image]] = {}
_VINYL_ART_CACHE: list[tuple[Image.Image, int, Image.Image]] = []
_IDLE_FRAME_CACHE: dict[int, Image.Image] = {}


def _vinyl_geometry(size: int) -> tuple[int, int, Image.Image, Image.Image]:
    cached = _VINYL_GEOMETRY_CACHE.get(size)
    if cached is not None:
        return cached

    margin = max(2, size // 32)
    disc_size = size - margin * 2

    disc_mask = Image.new("L", (disc_size, disc_size), 0)
    mask_draw = ImageDraw.Draw(disc_mask)
    mask_draw.ellipse((0, 0, disc_size - 1, disc_size - 1), fill=255)

    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
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

    result = (margin, disc_size, disc_mask, overlay)
    _VINYL_GEOMETRY_CACHE[size] = result
    return result


def _prepare_vinyl_art(art: Image.Image, disc_size: int) -> Image.Image:
    for cached_art, cached_size, prepared in _VINYL_ART_CACHE:
        if cached_art is art and cached_size == disc_size:
            return prepared
    art_square = ImageOps.fit(
        art, (disc_size, disc_size), method=Image.Resampling.LANCZOS
    )
    if VINYL_SATURATION != 1.0:
        art_square = ImageEnhance.Color(art_square).enhance(VINYL_SATURATION)
    if VINYL_CONTRAST != 1.0:
        art_square = ImageEnhance.Contrast(art_square).enhance(VINYL_CONTRAST)
    prepared = art_square.convert("RGBA")
    _VINYL_ART_CACHE.append((art, disc_size, prepared))
    if len(_VINYL_ART_CACHE) > 4:
        _VINYL_ART_CACHE.pop(0)
    return prepared


def render_record(art: Image.Image | None, angle: float, size: int = 64) -> Image.Image:
    if art is None:
        return Image.new("RGB", (size, size), (0, 0, 0))

    margin, disc_size, disc_mask, overlay = _vinyl_geometry(size)
    art_square = _prepare_vinyl_art(art, disc_size)
    rotated = art_square.rotate(angle, resample=Image.Resampling.BICUBIC)

    frame = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    frame.paste(rotated, (margin, margin), disc_mask)
    return Image.alpha_composite(frame, overlay).convert("RGB")


def render_idle(size: int = 64) -> Image.Image:
    cached = _IDLE_FRAME_CACHE.get(size)
    if cached is not None:
        return cached
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
    _IDLE_FRAME_CACHE[size] = frame
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


class SpotifyRateLimited(RuntimeError):
    def __init__(self, retry_after: float = 60.0) -> None:
        super().__init__(f"Rate limited; retry after {retry_after:g} seconds")
        self.retry_after = retry_after


class SpotifyClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        token_cache: Path,
        refresh_token: str | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.token_cache = token_cache
        self.refresh_token = refresh_token or os.environ.get("SPOTIFY_REFRESH_TOKEN")
        self.token = self._load_token()

    def get_currently_playing(
        self,
        retry_auth: bool = True,
        raise_on_rate_limit: bool = False,
        raise_on_error: bool = False,
    ) -> dict[str, Any] | None:
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
                return self.get_currently_playing(
                    retry_auth=False,
                    raise_on_rate_limit=raise_on_rate_limit,
                    raise_on_error=raise_on_error,
                )
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
                if raise_on_rate_limit:
                    raise SpotifyRateLimited(retry_after=float(max(retry, 1)))
                print(
                    f"[Spotify] Rate limited; waiting at least {max(retry, 1)} seconds",
                    flush=True,
                )
                time.sleep(max(retry, 1))
                return None
            resp.raise_for_status()
            return resp.json()
        except (SpotifyQuotaExceeded, SpotifyRateLimited):
            raise
        except Exception as e:
            if raise_on_error:
                raise
            print(f"[Spotify] Request error: {e}")
            return None

    def _valid_access_token(self) -> str:
        if not self.token:
            refresh = self.refresh_token or os.environ.get("SPOTIFY_REFRESH_TOKEN")
            if refresh:
                self.token = {"refresh_token": refresh, "expires_at": 0}
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
        if "refresh_token" not in token:
            if self.token and "refresh_token" in self.token:
                token["refresh_token"] = self.token["refresh_token"]
            elif self.refresh_token:
                token["refresh_token"] = self.refresh_token
        write_private_file(self.token_cache, json.dumps(token, indent=2) + "\n")
        self.token = token

    def _refresh_token(self) -> None:
        refresh_token = (
            self.token.get("refresh_token")
            if self.token
            else (self.refresh_token or os.environ.get("SPOTIFY_REFRESH_TOKEN"))
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

    streamer: USBMatrixStreamer | TCPMatrixStreamer = (
        USBMatrixStreamer(args.serial_port, keepalive_interval=4.0)
        if args.transport == "usb"
        else TCPMatrixStreamer(pico_ip, port, max_in_flight=4, keepalive_interval=4.0)
    )
    print(f"Streaming over {args.transport} at {fps} FPS", flush=True)

    # If test mode is enabled, stream synthetic spinning vinyl
    if args.test:
        print("Running in TEST mode (synthetic album art, spinning continuously)")
        test_art = render_test_album(64)
        angle = 0.0
        last_t = next_frame_at = time.monotonic()
        while True:
            t0 = time.monotonic()
            dt = t0 - last_t
            last_t = t0
            angle = (angle + dt * rotation_speed) % 360.0

            frame = render_record(test_art, angle, size=64)
            streamer.send_frame(frame, brightness=args.brightness)
            next_frame_at += frame_interval
            after = time.monotonic()
            if next_frame_at < after:
                next_frame_at = after
            else:
                time.sleep(next_frame_at - after)

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
                        # Prefer ~300px artwork over 640px for a 60x60 disc
                        suitable = [
                            img["url"]
                            for img in images
                            if img.get("url") and int(img.get("width") or 640) >= 200
                        ]
                        url = suitable[-1] if suitable else images[0].get("url")
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

    last_frame_t = next_frame_at = time.monotonic()

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

        next_frame_at += frame_interval
        after = time.monotonic()
        if next_frame_at < after:
            next_frame_at = after
        else:
            time.sleep(next_frame_at - after)


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
