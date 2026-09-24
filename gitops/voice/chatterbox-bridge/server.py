#!/usr/bin/env python3
"""Wyoming TTS server for Chatterbox Turbo (stdlib + lazy torch).

Speaks the subset of the Wyoming protocol Home Assistant needs: per
connection, `describe` answers with one `info` event advertising the
`jarvis` voice, and each `synthesize` event answers with `audio-start`,
one or more `audio-chunk` events carrying 16-bit mono PCM, and
`audio-stop`. Streaming `synthesize-start/chunk/stop` sequences are
tolerated by buffering the text and synthesizing once on `stop`.

The Chatterbox model (torch, chatterbox-tts) is imported lazily inside
ChatterboxEngine.load so unit tests and `--help` never need the GPU
stack. Turns are serialized behind one asyncio lock; synthesis failures emit
a Wyoming error event so the upstream gateway can retry Piper before audio.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import struct
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread

LOG = logging.getLogger("chatterbox-bridge")

PROGRAM_NAME = "chatterbox-turbo"
DEFAULT_VOICE = "jarvis"
DEFAULT_LANGUAGE = "en"
TURBO_REPO = "ResembleAI/chatterbox-turbo"

_TAG_RE = re.compile(r"\[[^\[\]]{1,32}\]")
WATERMARK_AVAILABLE = True


try:
    import numpy as np
except ImportError:
    np = None


def strip_paralinguistic_tags(text: str) -> str:
    """Remove [laugh]-style tags HA replies must never vocalize."""
    cleaned = _TAG_RE.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip(" \t")


def normalize_peak(samples, target: float = 0.89, max_gain: float = 10.0):
    """Lift quiet Turbo output toward full scale (vectorized or pure).

    Turbo ships unnormalized audio (often -11 dB peak); played at the
    satellite's volume that is nearly inaudible next to Piper. Gain is
    capped so near-silence never becomes amplified noise.
    """
    if np is not None and isinstance(samples, np.ndarray):
        if samples.size == 0:
            return samples
        peak = float(np.max(np.abs(samples)))
        if peak <= 0.0:
            return samples
        gain = min(target / peak, max_gain)
        return samples * gain

    values = [float(v) for v in samples]
    peak = max((abs(v) for v in values), default=0.0)
    if peak <= 0.0:
        return values
    gain = min(target / peak, max_gain)
    return [v * gain for v in values]


def samples_to_pcm16(samples) -> bytes:
    """Convert float samples in [-1, 1] to little-endian int16 PCM (vectorized or pure)."""
    if np is not None and isinstance(samples, np.ndarray):
        if samples.size == 0:
            return b""
        scaled = np.rint(samples.astype(np.float32) * 32767.0)
        clipped = np.clip(scaled, -32768.0, 32767.0).astype("<i2")
        return clipped.tobytes()

    if np is not None:
        try:
            arr = np.asarray(samples, dtype=np.float32)
            if arr.size == 0:
                return b""
            scaled = np.rint(arr * 32767.0)
            clipped = np.clip(scaled, -32768.0, 32767.0).astype("<i2")
            return clipped.tobytes()
        except Exception:
            pass

    out = bytearray()
    for value in samples:
        scaled = int(round(float(value) * 32767.0))
        if scaled > 32767:
            scaled = 32767
        elif scaled < -32768:
            scaled = -32768
        out += struct.pack("<h", scaled)
    return bytes(out)


def chunk_pcm(pcm: bytes, chunk_bytes: int):
    for offset in range(0, len(pcm), chunk_bytes):
        yield pcm[offset : offset + chunk_bytes]


@dataclass
class TtsConfig:
    """Environment-driven server configuration (pure)."""

    port: int = 10201
    voice_name: str = DEFAULT_VOICE
    device: str = "cuda"
    model_dir: str = ""
    reference_wav: str = ""
    allow_tags: bool = False
    chunk_ms: int = 250
    temperature: float = 0.8
    top_k: int = 1000
    top_p: float = 0.95
    repetition_penalty: float = 1.2
    require_watermark: bool = False
    max_queue_depth: int = 2
    allow_cpu_fallback: bool = False
    cache_capacity: int = 64

    @classmethod
    def from_env(cls) -> TtsConfig:
        get = os.environ.get
        return cls(
            port=int(get("CHATTERBOX_PORT", "10201")),
            voice_name=get("CHATTERBOX_VOICE", DEFAULT_VOICE),
            device=get("CHATTERBOX_DEVICE", "cuda"),
            model_dir=get("CHATTERBOX_MODEL_DIR", ""),
            reference_wav=get("CHATTERBOX_REFERENCE", ""),
            allow_tags=get("CHATTERBOX_ALLOW_TAGS", "0") == "1",
            chunk_ms=int(get("CHATTERBOX_CHUNK_MS", "250")),
            temperature=float(get("CHATTERBOX_TEMPERATURE", "0.8")),
            top_k=int(get("CHATTERBOX_TOP_K", "1000")),
            top_p=float(get("CHATTERBOX_TOP_P", "0.95")),
            repetition_penalty=float(get("CHATTERBOX_REPETITION_PENALTY", "1.2")),
            require_watermark=get("CHATTERBOX_REQUIRE_WATERMARK", "0") == "1",
            max_queue_depth=int(get("CHATTERBOX_MAX_QUEUE", "2")),
            allow_cpu_fallback=get("CHATTERBOX_ALLOW_CPU_FALLBACK", "0") == "1",
            cache_capacity=int(get("CHATTERBOX_CACHE_CAPACITY", "64")),
        )

    def validate(self) -> None:
        if not 1 <= self.port <= 65535:
            raise ValueError("CHATTERBOX_PORT must be a TCP port")
        if not self.voice_name.strip():
            raise ValueError("CHATTERBOX_VOICE must be nonempty")
        if self.device not in ("cuda", "cpu"):
            raise ValueError("CHATTERBOX_DEVICE must be cuda or cpu")
        if self.chunk_ms < 20 or self.chunk_ms > 2000:
            raise ValueError("CHATTERBOX_CHUNK_MS must be in [20, 2000]")
        if not 0.0 < self.temperature <= 2.0:
            raise ValueError("CHATTERBOX_TEMPERATURE must be in (0, 2]")
        if self.top_k < 1:
            raise ValueError("CHATTERBOX_TOP_K must be >= 1")
        if not 0.0 < self.top_p <= 1.0:
            raise ValueError("CHATTERBOX_TOP_P must be in (0, 1]")
        if not 1.0 <= self.repetition_penalty <= 2.0:
            raise ValueError("CHATTERBOX_REPETITION_PENALTY must be in [1, 2]")
        if self.max_queue_depth < 1:
            raise ValueError("CHATTERBOX_MAX_QUEUE must be >= 1")
        if self.cache_capacity < 0:
            raise ValueError("CHATTERBOX_CACHE_CAPACITY must be >= 0")

    def chunk_bytes(self, rate: int) -> int:
        frames = max(1, rate * self.chunk_ms // 1000)
        return frames * 2


def resolve_reference(path: str) -> str | None:
    """Return the reference clip path when usable, else None (builtin voice)."""
    candidate = (path or "").strip()
    if not candidate:
        return None
    if not os.path.isfile(candidate):
        LOG.warning("reference clip %s missing, using builtin voice", candidate)
        return None
    if os.path.getsize(candidate) == 0:
        LOG.warning("reference clip %s is empty, using builtin voice", candidate)
        return None
    return candidate


def ensure_watermarker(required: bool = False) -> None:
    """No-op patch when resemble-perth cannot build its watermark object.

    Its package imports but leaves PerthImplicitWatermarker as None on
    some runtimes; Turbo construction then crashes. Local assistant
    speech needs no provenance watermark, so fall back to passthrough.
    """
    global WATERMARK_AVAILABLE
    try:
        import perth
    except ImportError:
        WATERMARK_AVAILABLE = False
        if required:
            raise RuntimeError(
                "watermarking is required but perth package is not installed"
            )
        LOG.warning("perth package not installed, shipping unwatermarked audio")
        return

    if getattr(perth, "PerthImplicitWatermarker", None) is None:
        WATERMARK_AVAILABLE = False
        if required:
            raise RuntimeError(
                "watermarking is required but PerthImplicitWatermarker is unavailable"
            )
        LOG.warning("perth watermark unavailable, shipping unwatermarked audio")

        class _Passthrough:
            def apply_watermark(self, wav, sample_rate=None):
                return wav

        perth.PerthImplicitWatermarker = _Passthrough


def validate_cache_paths() -> None:
    """Fail early if model libraries would write into read-only filesystems, or if models are unreadable."""
    writable_paths = {
        "HF_HOME": os.environ.get("HF_HOME", "/runtime/cache/huggingface"),
        "TORCH_HOME": os.environ.get("TORCH_HOME", "/runtime/cache/torch"),
        "TRITON_CACHE_DIR": os.environ.get("TRITON_CACHE_DIR", "/runtime/cache/triton"),
        "XDG_CACHE_HOME": os.environ.get("XDG_CACHE_HOME", "/runtime/cache/xdg"),
    }
    for name, path in writable_paths.items():
        try:
            os.makedirs(path, exist_ok=True)
            if not os.access(path, os.W_OK):
                raise PermissionError(path)
        except OSError as exc:
            raise RuntimeError(f"{name} cache is not writable: {path}") from exc

    # Immutable model storage: must be readable; read-only mounts are expected and supported.
    readable_paths = {
        "HF_HUB_CACHE": os.environ.get("HF_HUB_CACHE", "/models/hub"),
        "TRANSFORMERS_CACHE": os.environ.get("TRANSFORMERS_CACHE", "/models/hub"),
    }
    for name, path in readable_paths.items():
        if os.path.exists(path):
            if not os.access(path, os.R_OK):
                raise RuntimeError(f"{name} model storage is not readable: {path}")


class ChatterboxEngine:
    """Thin wrapper over ChatterboxTurboTTS (imports torch lazily)."""

    def __init__(self, config: TtsConfig) -> None:
        self._config = config
        self._model = None
        self._sample_rate = 24000
        self.device_used: str = config.device
        self.is_healthy: bool = True

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def load(self) -> None:
        import torch

        config = self._config
        device = config.device
        if device == "cuda" and not torch.cuda.is_available():
            if not config.allow_cpu_fallback:
                self.is_healthy = False
                raise RuntimeError(
                    "CUDA requested for Chatterbox Turbo but torch.cuda is unavailable; CPU fallback is disabled"
                )
            LOG.warning(
                "CUDA unavailable, explicit CPU fallback permitted by configuration"
            )
            device = "cpu"
        self.device_used = device

        ensure_watermarker(self._config.require_watermark)
        from chatterbox.tts_turbo import ChatterboxTurboTTS

        if config.model_dir:
            LOG.info("loading Turbo weights from %s", config.model_dir)
            self._model = ChatterboxTurboTTS.from_local(config.model_dir, device=device)
        else:
            LOG.info("loading Turbo weights from %s", TURBO_REPO)
            self._model = ChatterboxTurboTTS.from_pretrained(device=device)
        self._sample_rate = int(self._model.sr)
        reference = resolve_reference(config.reference_wav)
        if reference is not None:
            try:
                self._model.prepare_conditionals(reference)
                LOG.info("voice conditionals prepared from %s", reference)
            except Exception:
                LOG.warning(
                    "reference clip %s rejected, using builtin voice",
                    reference,
                    exc_info=True,
                )

    def synthesize(self, text: str) -> tuple[bytes, int]:
        assert self._model is not None, "engine not loaded"
        config = self._config
        wav = self._model.generate(
            text,
            temperature=config.temperature,
            top_k=config.top_k,
            top_p=config.top_p,
            repetition_penalty=config.repetition_penalty,
        )
        arr = wav.squeeze(0).detach().cpu().numpy()
        return samples_to_pcm16(normalize_peak(arr)), self._sample_rate


class TtsCache:
    """Bounded LRU cache for synthesized PCM responses."""

    def __init__(self, capacity: int = 64) -> None:
        self.capacity = capacity
        self._cache: dict[tuple[str, str], tuple[bytes, int]] = {}
        self._order: list[tuple[str, str]] = []
        self._lock = Lock()

    def get(self, key: tuple[str, str]) -> tuple[bytes, int] | None:
        if self.capacity <= 0:
            return None
        with self._lock:
            if key in self._cache:
                self._order.remove(key)
                self._order.append(key)
                return self._cache[key]
            return None

    def put(self, key: tuple[str, str], val: tuple[bytes, int]) -> None:
        if self.capacity <= 0:
            return
        with self._lock:
            if key in self._cache:
                self._order.remove(key)
            elif len(self._cache) >= self.capacity:
                oldest = self._order.pop(0)
                self._cache.pop(oldest, None)
            self._cache[key] = val
            self._order.append(key)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._order.clear()


HISTOGRAM_BUCKETS = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0)


class RuntimeMetrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self.counters: dict[str, int] = {}
        self.histograms: dict[str, dict] = {}
        self.gauges: dict[str, int | float] = {
            "jarvis_tts_unwatermarked_audio": 0,
            "jarvis_tts_cpu_fallback": 0,
        }

    def inc(self, name: str, delta: int = 1) -> None:
        with self._lock:
            self.counters[name] = self.counters.get(name, 0) + delta

    def set_gauge(self, name: str, value: int | float) -> None:
        with self._lock:
            self.gauges[name] = value

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            if name not in self.histograms:
                self.histograms[name] = {
                    "sum": 0.0,
                    "count": 0,
                    "buckets": {b: 0 for b in HISTOGRAM_BUCKETS},
                }
            h = self.histograms[name]
            h["sum"] += value
            h["count"] += 1
            for b in HISTOGRAM_BUCKETS:
                if value <= b:
                    h["buckets"][b] += 1

    def render(self) -> bytes:
        with self._lock:
            lines = []
            for name, value in sorted(self.counters.items()):
                lines.append(f"{name} {value}")
            for name, h in sorted(self.histograms.items()):
                b_dict = h["buckets"]
                for b in HISTOGRAM_BUCKETS:
                    lines.append(f'{name}_bucket{{le="{b}"}} {b_dict[b]}')
                lines.append(f'{name}_bucket{{le="+Inf"}} {h["count"]}')
                lines.append(f"{name}_sum {h['sum']}")
                lines.append(f"{name}_count {h['count']}")
            for name, value in sorted(self.gauges.items()):
                lines.append(f"{name} {value}")
            return ("\n".join(lines) + "\n").encode()


def encode_event(type_: str, data: dict | None = None, payload: bytes = b"") -> bytes:
    header: dict = {"type": type_}
    data_bytes = json.dumps(data or {}).encode() if data else b""
    if data_bytes:
        header["data_length"] = len(data_bytes)
    if payload:
        header["payload_length"] = len(payload)
    return json.dumps(header).encode() + b"\n" + data_bytes + payload


async def read_event(reader: asyncio.StreamReader) -> dict | None:
    try:
        line = await reader.readline()
    except (asyncio.IncompleteReadError, ConnectionError):
        return None
    if not line:
        return None
    try:
        header = json.loads(line)
    except ValueError:
        return None
    data_length = int(header.get("data_length") or 0)
    payload_length = int(header.get("payload_length") or 0)
    data: dict = {}
    try:
        if data_length:
            raw = await reader.readexactly(data_length)
            parsed = json.loads(raw.decode())
            if isinstance(parsed, dict):
                data = parsed
        payload = b""
        if payload_length:
            payload = await reader.readexactly(payload_length)
    except (asyncio.IncompleteReadError, ConnectionError, ValueError):
        return None
    return {"type": header.get("type"), "data": data, "payload": payload}


def info_event(config: TtsConfig, version: str = "0.1.7") -> bytes:
    attribution = {
        "name": "Resemble AI",
        "url": "https://github.com/resemble-ai/chatterbox",
    }
    return encode_event(
        "info",
        {
            "tts": [
                {
                    "name": PROGRAM_NAME,
                    "description": "Chatterbox Turbo TTS",
                    "attribution": attribution,
                    "installed": True,
                    "version": version,
                    "languages": [DEFAULT_LANGUAGE],
                    "voices": [
                        {
                            "name": config.voice_name,
                            "description": "Jarvis voice (cloned reference or builtin)",
                            "attribution": attribution,
                            "installed": True,
                            "languages": [DEFAULT_LANGUAGE],
                        }
                    ],
                    "supports_synthesize_streaming": False,
                }
            ]
        },
    )


@dataclass
class TtsServer:
    config: TtsConfig
    engine: object
    metrics: RuntimeMetrics | None = None
    cache: TtsCache | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _pending_queue: int = field(default=0, init=False)

    def _prepare_text(self, text: str) -> str:
        if not self.config.allow_tags:
            text = strip_paralinguistic_tags(text)
        return text.strip()

    async def _send_audio(
        self, writer: asyncio.StreamWriter, pcm: bytes, rate: int
    ) -> None:
        # HA reads the format off every audio-chunk (AudioChunk.from_event
        # requires rate/width/channels keys); bare chunks KeyError.
        fmt = {"rate": rate, "width": 2, "channels": 1}
        writer.write(encode_event("audio-start", dict(fmt)))
        for piece in chunk_pcm(pcm, self.config.chunk_bytes(rate)):
            writer.write(encode_event("audio-chunk", dict(fmt), piece))
        writer.write(encode_event("audio-stop", {}))
        await writer.drain()

    async def _send_silence(self, writer: asyncio.StreamWriter) -> None:
        writer.write(
            encode_event("audio-start", {"rate": 24000, "width": 2, "channels": 1})
        )
        writer.write(encode_event("audio-stop", {}))
        await writer.drain()

    async def _send_error(self, writer: asyncio.StreamWriter, error: Exception) -> None:
        writer.write(
            encode_event("error", {"text": f"Chatterbox synthesis failed: {error}"})
        )
        await writer.drain()

    async def synthesize_turn(
        self, writer: asyncio.StreamWriter, text: str, voice: str | None
    ) -> None:
        prepared = self._prepare_text(text)
        if voice and voice != self.config.voice_name:
            LOG.info("unknown voice %r, using %r", voice, self.config.voice_name)
        effective_voice = voice or self.config.voice_name
        if not prepared:
            await self._send_silence(writer)
            return

        if self._pending_queue >= self.config.max_queue_depth:
            LOG.warning(
                "Chatterbox queue full (%d pending >= limit %d), rejecting request",
                self._pending_queue,
                self.config.max_queue_depth,
            )
            if self.metrics:
                self.metrics.inc("jarvis_tts_queue_rejections_total")
            await self._send_error(
                writer,
                RuntimeError(f"queue full: {self._pending_queue} requests pending"),
            )
            return

        if self.cache:
            cache_key = (prepared, effective_voice)
            cached = self.cache.get(cache_key)
            if cached is not None:
                if self.metrics:
                    self.metrics.inc("jarvis_tts_cache_hits_total")
                pcm, rate = cached
                await self._send_audio(writer, pcm, rate)
                return

        self._pending_queue += 1
        try:
            started = asyncio.get_running_loop().time()
            async with self._lock:
                if writer.is_closing():
                    LOG.info(
                        "Client disconnected before synthesis started; abandoning request"
                    )
                    if self.metrics:
                        self.metrics.inc("jarvis_tts_abandoned_requests_total")
                    return
                pcm, rate = await asyncio.to_thread(self.engine.synthesize, prepared)
            if self.cache:
                self.cache.put((prepared, effective_voice), (pcm, rate))
            if self.metrics:
                self.metrics.inc("jarvis_tts_synthesis_success_total")
                self.metrics.observe(
                    "jarvis_tts_time_to_first_audio_seconds",
                    asyncio.get_running_loop().time() - started,
                )
                self.metrics.observe(
                    "jarvis_tts_generated_audio_seconds",
                    len(pcm) / (rate * 2),
                )
        except Exception as error:
            LOG.warning("synthesis failed", exc_info=True)
            if self.metrics:
                self.metrics.inc("jarvis_tts_synthesis_failures_total")
            await self._send_error(writer, error)
            return
        finally:
            self._pending_queue -= 1
        await self._send_audio(writer, pcm, rate)

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        pending_text: list[str] = []
        try:
            while True:
                event = await read_event(reader)
                if event is None:
                    break
                kind = event["type"]
                data = event["data"]
                if kind == "describe":
                    writer.write(info_event(self.config))
                    await writer.drain()
                elif kind == "synthesize":
                    voice = (data.get("voice") or {}).get("name")
                    await self.synthesize_turn(writer, data.get("text", ""), voice)
                elif kind == "synthesize-chunk":
                    pending_text.append(str(data.get("text", "")))
                elif kind == "synthesize-stop":
                    voice = (data.get("voice") or {}).get("name")
                    await self.synthesize_turn(writer, "".join(pending_text), voice)
                    pending_text = []
                elif kind == "synthesize-start":
                    pending_text = []
        finally:
            try:
                writer.close()
            except (ConnectionError, RuntimeError):
                pass


async def amain() -> None:
    parser = argparse.ArgumentParser(description="Chatterbox Turbo Wyoming TTS server")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    config = TtsConfig.from_env()
    if args.port is not None:
        config.port = args.port
    config.validate()
    validate_cache_paths()
    logging.basicConfig(level=logging.INFO)
    engine = ChatterboxEngine(config)
    await asyncio.to_thread(engine.load)
    metrics = RuntimeMetrics()
    metrics.gauges["jarvis_tts_unwatermarked_audio"] = int(not WATERMARK_AVAILABLE)
    metrics.gauges["jarvis_tts_cpu_fallback"] = int(
        getattr(engine, "device_used", "") == "cpu"
    )
    cache = TtsCache(config.cache_capacity)
    server = TtsServer(config, engine, metrics, cache)

    class MetricsHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                if getattr(engine, "is_healthy", True):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"OK\n")
                else:
                    self.send_response(503)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"Service Unavailable\n")
                return
            if self.path != "/metrics":
                self.send_response(404)
                self.end_headers()
                return
            body = metrics.render()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            pass

    metrics_server = ThreadingHTTPServer(
        ("0.0.0.0", int(os.environ.get("CHATTERBOX_METRICS_PORT", "8001"))),
        MetricsHandler,
    )
    Thread(target=metrics_server.serve_forever, daemon=True).start()
    tcp = await asyncio.start_server(server.handle_client, "0.0.0.0", config.port)
    LOG.info("listening on %d", config.port)
    async with tcp:
        await tcp.serve_forever()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
