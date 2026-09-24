"""Protocol-aware Wyoming gateway with ordered backend failover, voice mapping, and metrics."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread

LOG = logging.getLogger("wyoming-gateway")

MAX_HEADER_LENGTH = 65536
MAX_DATA_LENGTH = 65536
MAX_PAYLOAD_LENGTH = 10 * 1024 * 1024
MAX_TEXT_LENGTH = 10000
MAX_AUDIO_BUFFER_BYTES = 2 * 1024 * 1024

DEFAULT_HISTOGRAM_BUCKETS = (0.05, 0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0)
DEFAULT_VOICE_MAP = {"piper": {"jarvis": "en_GB-alan-medium"}}


def encode_event(event: dict) -> bytes:
    data = json.dumps(event.get("data") or {}).encode() if event.get("data") else b""
    payload = event.get("payload") or b""
    header = {"type": event.get("type")}
    if data:
        header["data_length"] = len(data)
    if payload:
        header["payload_length"] = len(payload)
    return json.dumps(header, separators=(",", ":")).encode() + b"\n" + data + payload


async def safe_close(writer: asyncio.StreamWriter | None) -> None:
    if writer is None:
        return
    try:
        if not writer.is_closing():
            writer.close()
        await writer.wait_closed()
    except (ConnectionError, BrokenPipeError, OSError, asyncio.CancelledError):
        pass


async def read_event(reader: asyncio.StreamReader) -> dict | None:
    try:
        line = await reader.readline()
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        return None
    if not line:
        return None
    if len(line) > MAX_HEADER_LENGTH:
        raise ValueError("event header exceeds maximum length")
    try:
        header = json.loads(line)
    except json.JSONDecodeError as err:
        raise ValueError("malformed event header") from err
    if not isinstance(header, dict) or "type" not in header:
        raise ValueError("invalid event header format")

    data_length = int(header.get("data_length") or 0)
    payload_length = int(header.get("payload_length") or 0)
    if data_length < 0 or data_length > MAX_DATA_LENGTH:
        raise ValueError("data_length out of bounds")
    if payload_length < 0 or payload_length > MAX_PAYLOAD_LENGTH:
        raise ValueError("payload_length out of bounds")

    data = json.loads(await reader.readexactly(data_length)) if data_length else {}
    payload = await reader.readexactly(payload_length) if payload_length else b""
    return {"type": header.get("type"), "data": data, "payload": payload}


def info_event(mode: str) -> bytes:
    if mode == "tts":
        info = {
            "tts": [
                {
                    "name": "wyoming-tts-gateway",
                    "description": "Jarvis Wyoming TTS Gateway",
                    "attribution": {
                        "name": "Homelab",
                        "url": "https://github.com/rupan/homelab",
                    },
                    "installed": True,
                    "version": "1.0.0",
                    "languages": ["en"],
                    "voices": [
                        {
                            "name": "jarvis",
                            "description": "Jarvis voice (Chatterbox / Piper fallback)",
                            "attribution": {
                                "name": "Homelab",
                                "url": "https://github.com/rupan/homelab",
                            },
                            "installed": True,
                            "languages": ["en"],
                        }
                    ],
                    "supports_synthesize_streaming": True,
                }
            ]
        }
    else:
        info = {
            "asr": [
                {
                    "name": "wyoming-stt-gateway",
                    "description": "Jarvis Wyoming STT Gateway",
                    "attribution": {
                        "name": "Homelab",
                        "url": "https://github.com/rupan/homelab",
                    },
                    "installed": True,
                    "version": "1.0.0",
                    "languages": ["en"],
                    "models": [
                        {
                            "name": "nemotron",
                            "description": "Nemotron Speech / Whisper fallback",
                            "attribution": {
                                "name": "Homelab",
                                "url": "https://github.com/rupan/homelab",
                            },
                            "installed": True,
                            "languages": ["en"],
                        }
                    ],
                }
            ]
        }
    return encode_event({"type": "info", "data": info})


def map_event_for_backend(
    backend_name: str, event: dict, voice_map: dict[str, dict[str, str]]
) -> dict:
    kind = event.get("type")
    if kind not in {"synthesize", "synthesize-start", "synthesize-stop"}:
        return event

    backend_mappings = voice_map.get(backend_name, {})
    if not backend_mappings:
        return event

    data = dict(event.get("data") or {})
    voice = data.get("voice")
    if isinstance(voice, dict):
        mapped_voice = dict(voice)
        name = mapped_voice.get("name", "jarvis")
        if name in backend_mappings:
            mapped_voice["name"] = backend_mappings[name]
        elif "jarvis" in backend_mappings and (not name or name == "jarvis"):
            mapped_voice["name"] = backend_mappings["jarvis"]
        data["voice"] = mapped_voice
    elif "jarvis" in backend_mappings:
        data["voice"] = {"name": backend_mappings["jarvis"]}

    return {"type": kind, "data": data, "payload": event.get("payload") or b""}


@dataclass
class Backend:
    name: str
    host: str
    port: int
    failures: int = 0
    open_until: float = 0.0
    healthy: bool = False
    half_open_in_flight: bool = False

    def is_available(self, now: float) -> bool:
        return self.healthy and self.open_until <= now

    def is_usable(self, now: float) -> bool:
        # Usable if currently healthy and cooldown expired, or eligible for a trial
        if not self.healthy:
            return False
        if self.open_until <= now:
            return True
        return self.failures > 0 and not self.half_open_in_flight


class Metrics:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self._lock = Lock()
        self.counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self.sums: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[float, int]] = {}
        self.histograms: dict[
            tuple[str, tuple[tuple[str, str], ...]], dict[float, int]
        ] = {}
        self.active_requests: int = 0

    @staticmethod
    def _labels(labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(labels.items()))

    def inc(self, name: str, delta: float = 1.0, **labels: str) -> None:
        key = (name, self._labels(labels))
        with self._lock:
            self.counters[key] = self.counters.get(key, 0.0) + delta

    def observe(
        self,
        name: str,
        value: float,
        buckets: tuple[float, ...] = DEFAULT_HISTOGRAM_BUCKETS,
        **labels: str,
    ) -> None:
        key = (name, self._labels(labels))
        with self._lock:
            total, count = self.sums.get(key, (0.0, 0))
            self.sums[key] = (total + value, count + 1)

            if key not in self.histograms:
                self.histograms[key] = {b: 0 for b in buckets}
            h = self.histograms[key]
            for b in buckets:
                if value <= b:
                    h[b] = h.get(b, 0) + 1

    def render(self, backends: list[Backend]) -> bytes:
        lines: list[str] = []
        now = time.monotonic()
        with self._lock:
            for (name, labels), value in sorted(self.counters.items()):
                lines.append(f"{name}{format_labels(labels)} {value}")
            for (name, labels), h in sorted(self.histograms.items()):
                for le, bcount in sorted(h.items()):
                    le_labels = labels + (("le", str(le)),)
                    lines.append(f"{name}_bucket{format_labels(le_labels)} {bcount}")
                inf_labels = labels + (("le", "+Inf"),)
                total_count = self.sums.get((name, labels), (0.0, 0))[1]
                lines.append(f"{name}_bucket{format_labels(inf_labels)} {total_count}")
            for (name, labels), (total, count) in sorted(self.sums.items()):
                lines.append(f"{name}_sum{format_labels(labels)} {total}")
                lines.append(f"{name}_count{format_labels(labels)} {count}")
            lines.append(
                f'jarvis_gateway_active_requests{{mode="{self.mode}"}} {self.active_requests}'
            )

        usable_count = 0
        for backend in backends:
            labels = format_labels((("backend", backend.name), ("mode", self.mode)))
            available = backend.is_available(now)
            if available:
                usable_count += 1
            lines.append(f"jarvis_gateway_backend_available{labels} {int(available)}")
        lines.append(
            f'jarvis_gateway_usable_backends{{mode="{self.mode}"}} {usable_count}'
        )
        return ("\n".join(lines) + "\n").encode()


def format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    body = ",".join(f'{key}="{value}"' for key, value in labels)
    return "{" + body + "}"


@dataclass
class TurnMetrics:
    mode: str
    backend: str
    metrics: Metrics
    vad_end: float | None = None
    synthesis_start: float | None = None
    first_audio: float | None = None
    audio_bytes: int = 0
    audio_rate: int = 0
    audio_width: int = 0
    audio_channels: int = 0

    def client_event(self, event: dict) -> None:
        kind = event.get("type")
        if self.mode == "stt" and kind == "audio-stop":
            self.vad_end = time.monotonic()
        if self.mode == "tts" and kind in {"synthesize", "synthesize-start"}:
            self.synthesis_start = time.monotonic()

    def backend_event(self, event: dict) -> None:
        kind = event.get("type")
        now = time.monotonic()
        labels = {"backend": self.backend}
        if self.mode == "stt" and kind == "transcript" and self.vad_end is not None:
            self.metrics.observe(
                "jarvis_stt_vad_to_final_seconds", now - self.vad_end, **labels
            )
        if self.mode == "tts":
            if kind == "audio-start":
                self.first_audio = now
                data = event.get("data") or {}
                self.audio_rate = int(data.get("rate") or 0)
                self.audio_width = int(data.get("width") or 0)
                self.audio_channels = int(data.get("channels") or 0)
                if self.synthesis_start is not None:
                    self.metrics.observe(
                        "jarvis_tts_time_to_first_audio_seconds",
                        now - self.synthesis_start,
                        **labels,
                    )
            elif kind == "audio-chunk":
                payload = event.get("payload") or b""
                self.audio_bytes += len(payload)
            elif kind == "error":
                self.metrics.inc(
                    "jarvis_tts_synthesis_failures_total", backend=self.backend
                )
            elif kind == "audio-stop" and self.synthesis_start is not None:
                denominator = self.audio_rate * self.audio_width * self.audio_channels
                audio_seconds = self.audio_bytes / denominator if denominator else 0.0
                wall = now - self.synthesis_start
                if audio_seconds:
                    self.metrics.observe(
                        "jarvis_tts_synthesis_realtime_factor",
                        wall / audio_seconds,
                        **labels,
                    )
                    self.metrics.observe(
                        "jarvis_tts_generated_audio_seconds", audio_seconds, **labels
                    )
                self.metrics.inc(
                    "jarvis_tts_synthesis_success_total", backend=self.backend
                )


@dataclass
class Gateway:
    mode: str
    backends: list[Backend]
    metrics: Metrics
    failure_threshold: int = 2
    cooldown_seconds: float = 30.0
    connect_timeout: float = 1.0
    response_timeout: float = 5.0
    voice_map: dict[str, dict[str, str]] = field(
        default_factory=lambda: dict(DEFAULT_VOICE_MAP)
    )
    max_concurrency: int = 16
    _selection_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _concurrency_sem: asyncio.Semaphore = field(init=False)

    def __post_init__(self) -> None:
        self._concurrency_sem = asyncio.Semaphore(self.max_concurrency)

    def record_success(self, backend: Backend) -> None:
        backend.failures = 0
        backend.open_until = 0.0
        backend.half_open_in_flight = False
        backend.healthy = True

    def record_failure(self, backend: Backend) -> None:
        backend.failures += 1
        backend.half_open_in_flight = False
        now = time.monotonic()
        if backend.failures >= self.failure_threshold:
            backend.open_until = now + self.cooldown_seconds
        self.metrics.inc(
            "jarvis_gateway_backend_failures_total",
            mode=self.mode,
            backend=backend.name,
        )

    async def probe(self, backend: Backend) -> None:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(backend.host, backend.port),
                self.connect_timeout,
            )
            writer.close()
            await writer.wait_closed()
            backend.healthy = True
            # TCP acceptance alone does NOT reset failures or open_until.
        except (OSError, TimeoutError):
            backend.healthy = False

    async def health_loop(self) -> None:
        while True:
            await asyncio.gather(
                *(self.probe(backend) for backend in self.backends),
                return_exceptions=True,
            )
            await asyncio.sleep(5)

    async def connect_backend(
        self,
        exclude: set[str] | None = None,
    ) -> tuple[Backend, asyncio.StreamReader, asyncio.StreamWriter]:
        async with self._selection_lock:
            now = time.monotonic()
            for backend in self.backends:
                if exclude and backend.name in exclude:
                    continue
                if backend.open_until > now:
                    continue
                if backend.failures >= self.failure_threshold:
                    if backend.half_open_in_flight:
                        continue
                    backend.half_open_in_flight = True
                try:
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(backend.host, backend.port),
                        self.connect_timeout,
                    )
                    backend.healthy = True
                    self.metrics.inc(
                        "jarvis_gateway_connections_total",
                        mode=self.mode,
                        backend=backend.name,
                    )
                    if exclude:
                        self.metrics.inc(
                            "jarvis_gateway_fallbacks_total",
                            mode=self.mode,
                            backend=backend.name,
                        )
                    return backend, reader, writer
                except (OSError, TimeoutError):
                    self.record_failure(backend)
            raise ConnectionError("no Wyoming backend is available")

    async def handle_tts(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        """Buffer one small synthesis request so pre-audio failure can retry."""
        first = await read_event(client_reader)
        if first is None:
            client_writer.close()
            await client_writer.wait_closed()
            return

        if first.get("type") == "describe":
            client_writer.write(info_event("tts"))
            await client_writer.drain()
            client_writer.close()
            await client_writer.wait_closed()
            return

        buffered = [first]
        if first.get("type") == "synthesize-start":
            while True:
                event = await read_event(client_reader)
                if event is None:
                    break
                buffered.append(event)
                if event.get("type") == "synthesize-stop":
                    break

        if first.get("type") not in {"synthesize", "synthesize-start"}:
            try:
                backend, backend_reader, backend_writer = await self.connect_backend()
            except ConnectionError:
                self.metrics.inc(
                    "jarvis_gateway_rejected_connections_total", mode=self.mode
                )
                await safe_close(client_writer)
                return

            try:
                for event in buffered:
                    mapped = map_event_for_backend(backend.name, event, self.voice_map)
                    backend_writer.write(encode_event(mapped))
                await backend_writer.drain()
                while (event := await read_event(backend_reader)) is not None:
                    client_writer.write(encode_event(event))
                    await client_writer.drain()
                    if event.get("type") in {"info", "audio-stop", "error"}:
                        break
                self.record_success(backend)
            except Exception:
                self.record_failure(backend)
                self.metrics.inc(
                    "jarvis_gateway_stream_failures_total",
                    mode=self.mode,
                    backend=backend.name,
                )
            finally:
                await safe_close(backend_writer)
                await safe_close(client_writer)
            return

        # Check max text length
        for ev in buffered:
            if ev.get("type") == "synthesize":
                text = (ev.get("data") or {}).get("text", "")
                if len(text) > MAX_TEXT_LENGTH:
                    client_writer.write(
                        encode_event(
                            {
                                "type": "error",
                                "data": {
                                    "text": "text exceeds maximum length",
                                    "code": "text_too_long",
                                },
                            }
                        )
                    )
                    await client_writer.drain()
                    client_writer.close()
                    await client_writer.wait_closed()
                    return

        attempted: set[str] = set()
        audio_started = False
        try:
            while True:
                try:
                    (
                        backend,
                        backend_reader,
                        backend_writer,
                    ) = await self.connect_backend(attempted)
                except ConnectionError:
                    if not attempted:
                        self.metrics.inc(
                            "jarvis_gateway_rejected_connections_total", mode=self.mode
                        )
                        try:
                            client_writer.write(
                                encode_event(
                                    {
                                        "type": "error",
                                        "data": {
                                            "text": "no Wyoming backend is available",
                                            "code": "backend_unavailable",
                                        },
                                    }
                                )
                            )
                            await client_writer.drain()
                        except Exception:
                            pass
                    break
                attempted.add(backend.name)
                turn = TurnMetrics(self.mode, backend.name, self.metrics)
                for ev in buffered:
                    turn.client_event(ev)

                for event in buffered:
                    mapped = map_event_for_backend(backend.name, event, self.voice_map)
                    backend_writer.write(encode_event(mapped))
                await backend_writer.drain()

                retry = False
                while True:
                    try:
                        event = await asyncio.wait_for(
                            read_event(backend_reader), timeout=self.response_timeout
                        )
                    except (
                        TimeoutError,
                        asyncio.TimeoutError,
                        OSError,
                        ConnectionError,
                        asyncio.IncompleteReadError,
                        ValueError,
                    ):
                        event = None

                    if event is None:
                        # Premature EOF or timeout
                        if not audio_started:
                            self.record_failure(backend)
                            self.metrics.inc(
                                "jarvis_tts_synthesis_failures_total",
                                backend=backend.name,
                            )
                            retry = True
                        else:
                            self.metrics.inc(
                                "jarvis_gateway_stream_failures_total",
                                mode=self.mode,
                                backend=backend.name,
                            )
                        break

                    turn.backend_event(event)

                    if event.get("type") == "audio-start":
                        audio_started = True

                    if event.get("type") == "error" and not audio_started:
                        self.record_failure(backend)
                        retry = True
                        break

                    try:
                        client_writer.write(encode_event(event))
                        await client_writer.drain()
                    except (
                        ConnectionError,
                        BrokenPipeError,
                        ConnectionResetError,
                        OSError,
                    ):
                        self.metrics.inc(
                            "jarvis_gateway_stream_failures_total",
                            mode=self.mode,
                            backend=backend.name,
                        )
                        break

                    if event.get("type") == "audio-stop":
                        self.record_success(backend)
                        break
                    if event.get("type") == "error":
                        self.record_failure(backend)
                        break

                await safe_close(backend_writer)
                if not retry or audio_started or len(attempted) >= len(self.backends):
                    break
        except (OSError, ConnectionError, asyncio.IncompleteReadError, ValueError):
            self.metrics.inc("jarvis_gateway_stream_failures_total", mode=self.mode)
        finally:
            await safe_close(client_writer)

    async def handle_stt(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        """Bounded STT audio buffering with fallback replay on error, EOF, or timeout."""
        buffered_events: list[dict] = []
        buffered_bytes = 0
        attempted: set[str] = set()

        backend: Backend | None = None
        backend_reader: asyncio.StreamReader | None = None
        backend_writer: asyncio.StreamWriter | None = None

        turn: TurnMetrics | None = None

        try:
            # First event: could be describe or transcribe/audio-start
            first = await read_event(client_reader)
            if first is None:
                client_writer.close()
                await client_writer.wait_closed()
                return

            if first.get("type") == "describe":
                client_writer.write(info_event("stt"))
                await client_writer.drain()
                client_writer.close()
                await client_writer.wait_closed()
                return

            buffered_events.append(first)

            try:
                backend, backend_reader, backend_writer = await self.connect_backend(
                    attempted
                )
            except ConnectionError:
                self.metrics.inc(
                    "jarvis_gateway_rejected_connections_total", mode=self.mode
                )
                client_writer.close()
                await client_writer.wait_closed()
                return

            attempted.add(backend.name)
            turn = TurnMetrics(self.mode, backend.name, self.metrics)
            turn.client_event(first)
            backend_writer.write(encode_event(first))
            await backend_writer.drain()

            # Read client audio stream up to audio-stop
            while True:
                event = await read_event(client_reader)
                if event is None:
                    break
                kind = event.get("type")
                if kind == "audio-chunk":
                    payload = event.get("payload") or b""
                    buffered_bytes += len(payload)
                    if buffered_bytes > MAX_AUDIO_BUFFER_BYTES:
                        client_writer.write(
                            encode_event(
                                {
                                    "type": "error",
                                    "data": {
                                        "text": "audio exceeds buffer limit",
                                        "code": "audio_too_long",
                                    },
                                }
                            )
                        )
                        await client_writer.drain()
                        break

                buffered_events.append(event)
                turn.client_event(event)

                if backend_writer and not backend_writer.is_closing():
                    try:
                        backend_writer.write(encode_event(event))
                        await backend_writer.drain()
                    except Exception:
                        pass

                if kind == "audio-stop":
                    break

            # Now wait for backend response
            transcript_received = False
            if backend_reader and backend and backend_writer:
                try:
                    resp = await asyncio.wait_for(
                        read_event(backend_reader), timeout=self.response_timeout
                    )
                    if resp is not None and resp.get("type") == "transcript":
                        turn.backend_event(resp)
                        self.record_success(backend)
                        client_writer.write(encode_event(resp))
                        await client_writer.drain()
                        transcript_received = True
                    else:
                        self.record_failure(backend)
                except (
                    TimeoutError,
                    asyncio.TimeoutError,
                    OSError,
                    ConnectionError,
                    ValueError,
                ):
                    self.record_failure(backend)

                backend_writer.close()
                await backend_writer.wait_closed()

            # If primary failed and fallback is available, replay buffered events
            if not transcript_received and len(attempted) < len(self.backends):
                try:
                    fallback, fb_reader, fb_writer = await self.connect_backend(
                        attempted
                    )
                    attempted.add(fallback.name)
                    fb_turn = TurnMetrics(self.mode, fallback.name, self.metrics)
                    for ev in buffered_events:
                        fb_turn.client_event(ev)
                        fb_writer.write(encode_event(ev))
                    await fb_writer.drain()

                    resp = await asyncio.wait_for(
                        read_event(fb_reader), timeout=self.response_timeout
                    )
                    if resp is not None and resp.get("type") == "transcript":
                        fb_turn.backend_event(resp)
                        self.record_success(fallback)
                        client_writer.write(encode_event(resp))
                        await client_writer.drain()
                        transcript_received = True
                    else:
                        self.record_failure(fallback)
                    await safe_close(fb_writer)
                except Exception:
                    pass

        except (OSError, ConnectionError, asyncio.IncompleteReadError, ValueError):
            self.metrics.inc("jarvis_gateway_stream_failures_total", mode=self.mode)
        finally:
            await safe_close(backend_writer)
            await safe_close(client_writer)

    async def handle(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        try:
            await asyncio.wait_for(self._concurrency_sem.acquire(), timeout=0.5)
        except (TimeoutError, asyncio.TimeoutError):
            self.metrics.inc(
                "jarvis_gateway_rejected_connections_total", mode=self.mode
            )
            try:
                client_writer.write(
                    encode_event(
                        {
                            "type": "error",
                            "data": {
                                "text": "gateway concurrency limit reached",
                                "code": "overloaded",
                            },
                        }
                    )
                )
                await client_writer.drain()
            except Exception:
                pass
            await safe_close(client_writer)
            return

        self.metrics.active_requests += 1
        try:
            if self.mode == "tts":
                await self.handle_tts(client_reader, client_writer)
            else:
                await self.handle_stt(client_reader, client_writer)
        finally:
            self.metrics.active_requests -= 1
            self._concurrency_sem.release()


def parse_backends(value: str) -> list[Backend]:
    result = []
    for item in value.split(","):
        if not item.strip():
            continue
        name, address = item.split("=", 1)
        host, port = address.rsplit(":", 1)
        result.append(Backend(name.strip(), host.strip(), int(port)))
    if not result:
        raise ValueError("at least one backend is required")
    return result


def parse_voice_map(value: str | None) -> dict[str, dict[str, str]]:
    if not value:
        return dict(DEFAULT_VOICE_MAP)
    try:
        data = json.loads(value)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return dict(DEFAULT_VOICE_MAP)


def serve_metrics(metrics: Metrics, backends: list[Backend], port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            now = time.monotonic()
            if self.path == "/metrics":
                body = metrics.render(backends)
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path in {"/healthz", "/livez"}:
                body = b"ok\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/readyz":
                usable = any(backend.is_usable(now) for backend in backends)
                if usable:
                    body = b"ready\n"
                    status = 200
                else:
                    body = b"no usable backends\n"
                    status = 503
                self.send_response(status)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

        def log_message(self, *_args: object) -> None:
            pass

    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("stt", "tts"), required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--metrics-port", type=int, default=8000)
    args = parser.parse_args()

    backends = parse_backends(os.environ["WYOMING_BACKENDS"])
    voice_map = parse_voice_map(os.environ.get("WYOMING_VOICE_MAP"))
    metrics = Metrics(args.mode)
    gateway = Gateway(args.mode, backends, metrics, voice_map=voice_map)

    Thread(
        target=serve_metrics, args=(metrics, backends, args.metrics_port), daemon=True
    ).start()
    asyncio.create_task(gateway.health_loop())
    server = await asyncio.start_server(gateway.handle, "0.0.0.0", args.port)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(amain())
