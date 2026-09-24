#!/usr/bin/env python3
"""Wyoming-to-native-Nemotron STT bridge (stdlib + numpy + ctypes).

Replaces only the Whisper-facing side of the voice-ID proxy: Wyoming
audio-start/chunk/stop is tee'd into the NeMo-Speech.cpp C ABI
(libnemo_speech_asr_c) for recognition and into the existing speaker-ID
buffer. One native stream per Wyoming utterance. No partial transcripts
leave this process; Home Assistant receives exactly one Transcript per
turn, identical in shape to the current proxy output.

Contract:
  audio-start -> reset PCM buffer, open native stream
  audio-chunk -> stream_push immediately, append speaker-ID buffer
  audio-stop  -> stream_finish, drain final, speaker ID, garbage guard,
                 optional speaker prefix, single Transcript event

All native calls run in worker threads so the asyncio event loop never
blocks. Any native failure fails one turn (empty transcript, which HA
abandons silently) and never kills the service.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore[assignment]

try:
    from proxy import (
        VoiceIdClassifier,
        format_transcript_with_speaker,
        is_garbage_transcript,
    )
except ImportError:
    VoiceIdClassifier = None  # type: ignore[assignment]

    def is_garbage_transcript(text: str) -> bool:
        return not text.strip()

    def format_transcript_with_speaker(transcript: str, speaker: str | None) -> str:
        if speaker:
            body = transcript.strip().rstrip(".!?")
            return f"speaker {speaker} {body}"
        return transcript


LOG = logging.getLogger("nemotron-bridge")
SPEAKER_COUNTS = {"accepted": 0, "unknown": 0, "ambiguous": 0}
SPEAKER_LOCK = threading.Lock()

OK = 0


def record_speaker_result(
    speaker: str | None, scores: dict, classifier: object
) -> None:
    outcome = "accepted"
    if speaker is None:
        ordered = sorted(scores.values(), reverse=True)
        top = ordered[0] if ordered else -1.0
        margin = top - ordered[1] if len(ordered) > 1 else 0.0
        outcome = (
            "ambiguous"
            if top >= float(getattr(classifier, "threshold", 1.0))
            and margin < float(getattr(classifier, "min_margin", 1.0))
            else "unknown"
        )
    with SPEAKER_LOCK:
        SPEAKER_COUNTS[outcome] += 1


def start_metrics_server(port: int = 9100) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/metrics":
                self.send_error(404)
                return
            with SPEAKER_LOCK:
                body = "".join(
                    f'jarvis_speaker_id_total{{outcome="{outcome}"}} {count}\n'
                    for outcome, count in SPEAKER_COUNTS.items()
                ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            pass

    threading.Thread(
        target=ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever,
        daemon=True,
    ).start()


# Closed-vocabulary ASR normalization. Only exact (variant, device-word)
# bigrams rewrite; bare "gov" and words like "governor"/"government" never
# match, so ordinary speech cannot be corrupted. Applied to the raw final
# before the garbage guard; both forms stay in the turn log.
_VARIANT_TO_CANONICAL = {"govy": "govee", "govi": "govee", "gov": "govee"}
_DEVICE_WORDS = frozenset(
    {"light", "lights", "bulb", "bulbs", "lamp", "lamps", "strip", "strips"}
)


def normalize_vocab(text: str) -> str:
    tokens = text.split()
    lowered = [t.lower().strip(".,!?;:") for t in tokens]
    out = list(tokens)
    for i in range(len(tokens) - 1):
        variant = lowered[i]
        device = lowered[i + 1]
        if variant in _VARIANT_TO_CANONICAL and device in _DEVICE_WORDS:
            canon = _VARIANT_TO_CANONICAL[variant]
            word = out[i]
            out[i] = canon.capitalize() if word[:1].isupper() else canon
    return " ".join(out)


DEFAULT_BOOST = 2.5
DEFAULT_RIGHT_CTX = 1
DEFAULT_LANGUAGE = "en-US"


class NativeConfig:
    """Environment-driven native recognizer configuration (pure)."""

    def __init__(self) -> None:
        self.lib_path = os.environ.get("NEMO_LIB", "")
        self.model_path = os.environ.get("NEMO_MODEL", "")
        self.gpu = int(os.environ.get("NEMO_GPU", "0"))
        self.language = os.environ.get("NEMO_LANGUAGE", DEFAULT_LANGUAGE)
        self.boost = float(os.environ.get("NEMO_BOOST", str(DEFAULT_BOOST)))
        self.interim = os.environ.get("NEMO_INTERIM", "1") == "1"
        self.right_ctx = int(os.environ.get("NEMO_RIGHT_CTX", str(DEFAULT_RIGHT_CTX)))
        phrases = os.environ.get("NEMO_BOOST_PHRASES", "")
        self.phrases = [p.strip() for p in phrases.split(",") if p.strip()]
        arena = os.environ.get("NEMO_ARENA_SLOTS", "")
        self.arena_slots = int(arena) if arena.strip() else None

    def validate(self) -> None:
        if not self.lib_path:
            raise ValueError("NEMO_LIB must point at libnemo_speech_asr_c.so")
        if not self.model_path:
            raise ValueError("NEMO_MODEL must point at the ASR GGUF")
        if not 0 < self.boost <= 5.0:
            raise ValueError("NEMO_BOOST must be in (0, 5]")
        if self.arena_slots is not None and self.arena_slots < 1:
            raise ValueError("NEMO_ARENA_SLOTS must be >= 1 when set")


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


def info_event(model: str) -> bytes:
    attribution = {"name": "NVIDIA", "url": "https://github.com/NVIDIA/NeMo-Speech.cpp"}
    return encode_event(
        "info",
        {
            "asr": [
                {
                    "name": "nemotron-native",
                    "description": "Nemotron streaming STT via native C API",
                    "attribution": attribution,
                    "installed": True,
                    "models": [
                        {
                            "name": model,
                            "description": "Nemotron streaming ASR model",
                            "attribution": attribution,
                            "installed": True,
                            "languages": ["en"],
                        }
                    ],
                }
            ]
        },
    )


class _BackendConfig(ctypes.Structure):
    _fields_ = [("size", ctypes.c_size_t), ("gpu", ctypes.c_int32)]


class _ModelConfig(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_size_t),
        ("path", ctypes.c_char_p),
        ("name", ctypes.c_char_p),
    ]


class _StreamingConfig(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_size_t),
        ("chunk_size", ctypes.c_float),
        ("ctc_left_padding", ctypes.c_float),
        ("ctc_right_padding", ctypes.c_float),
        ("rnnt_right_context", ctypes.c_int32),
    ]


class _BatchingConfig(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_size_t),
        ("enable", ctypes.c_bool),
        ("max_batch_size", ctypes.c_int32),
        ("max_queue_delay_us", ctypes.c_int32),
        ("max_queue_depth", ctypes.c_int32),
        ("ingress_cohort_delay_us", ctypes.c_int32),
        ("state_arena_slots", ctypes.c_int32),
    ]


class _RecognizerConfig(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_size_t),
        ("backend", ctypes.c_void_p),
        ("model", ctypes.c_void_p),
        ("streaming", ctypes.c_void_p),
        ("decoder", ctypes.c_void_p),
        ("vad", ctypes.c_void_p),
        ("endpointing", ctypes.c_void_p),
        ("postproc", ctypes.c_void_p),
        ("diar", ctypes.c_void_p),
        ("batching", ctypes.c_void_p),
    ]


class _SpeechContext(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_size_t),
        ("phrases", ctypes.POINTER(ctypes.c_char_p)),
        ("phrase_count", ctypes.c_size_t),
        ("boost", ctypes.c_float),
    ]


class _RecognitionOptions(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_size_t),
        ("request_id", ctypes.c_char_p),
        ("language_code", ctypes.c_char_p),
        ("interim_results", ctypes.c_bool),
        ("enable_word_time_offsets", ctypes.c_bool),
        ("enable_automatic_punctuation", ctypes.c_bool),
        ("verbatim_transcripts", ctypes.c_bool),
        ("profanity_filter", ctypes.c_bool),
        ("stop_history_eou_ms", ctypes.c_int32),
        ("speech_contexts", ctypes.c_void_p),
        ("speech_context_count", ctypes.c_size_t),
        ("max_alternatives", ctypes.c_int32),
        ("enable_speaker_diarization", ctypes.c_bool),
        ("max_speaker_count", ctypes.c_int32),
    ]


class NativeRecognizer:
    """ctypes binding over libnemo_speech_asr_c (blocking calls)."""

    def __init__(self, config: NativeConfig) -> None:
        config.validate()
        if np is None:
            raise RuntimeError("numpy is required for PCM conversion")
        self._config = config
        self._lib = ctypes.CDLL(config.lib_path)
        self._setup_signatures()
        self._phrase_bufs = [p.encode() for p in config.phrases]
        # One single-phrase context per phrase: some engine builds only
        # ever see count<=1 exercised, so avoid packing N phrases into one.
        self._phrase_ptrs = []
        self._contexts = []
        for buf in self._phrase_bufs:
            arr = (ctypes.c_char_p * 1)(buf)
            ctx = _SpeechContext(
                size=ctypes.sizeof(_SpeechContext),
                phrases=ctypes.cast(arr, ctypes.POINTER(ctypes.c_char_p)),
                phrase_count=1,
                boost=config.boost,
            )
            self._phrase_ptrs.append(arr)
            self._contexts.append(ctx)
        if self._contexts:
            ctx_array = (_SpeechContext * len(self._contexts))(*self._contexts)
        else:
            ctx_array = None
        self._ctx_array = ctx_array
        self._model_path_buf = ctypes.create_string_buffer(config.model_path.encode())
        self._language_buf = ctypes.create_string_buffer(config.language.encode())
        self._backend = _BackendConfig(
            size=ctypes.sizeof(_BackendConfig), gpu=config.gpu
        )
        self._model = _ModelConfig(
            size=ctypes.sizeof(_ModelConfig),
            path=self._model_path_buf.value,
            name=None,
        )
        self._streaming = _StreamingConfig(
            size=ctypes.sizeof(_StreamingConfig),
            chunk_size=0.16,
            ctc_left_padding=1.92,
            ctc_right_padding=1.92,
            rnnt_right_context=config.right_ctx,
        )
        self._batching: _BatchingConfig | None = None
        if config.arena_slots is not None:
            self._batching = _BatchingConfig(
                size=ctypes.sizeof(_BatchingConfig),
                enable=True,
                max_batch_size=1024,
                max_queue_delay_us=5000,
                max_queue_depth=2048,
                ingress_cohort_delay_us=20000,
                state_arena_slots=config.arena_slots,
            )
        self._recognizer_cfg = _RecognizerConfig(
            size=ctypes.sizeof(_RecognizerConfig),
            backend=ctypes.addressof(self._backend),
            model=ctypes.addressof(self._model),
            streaming=ctypes.addressof(self._streaming),
            decoder=None,
            vad=None,
            endpointing=None,
            postproc=None,
            diar=None,
            batching=ctypes.addressof(self._batching) if self._batching else None,
        )
        self._recognizer: ctypes.c_void_p | None = None
        self._open_streams: set[int] = set()
        self._create()

    def _setup_signatures(self) -> None:
        lib = self._lib
        lib.nemo_speech_asr_create.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        lib.nemo_speech_asr_create.restype = ctypes.c_int
        lib.nemo_speech_asr_destroy.argtypes = [ctypes.c_void_p]
        lib.nemo_speech_asr_destroy.restype = None
        lib.nemo_speech_asr_recognition_options_default.argtypes = []
        lib.nemo_speech_asr_streaming_recognize.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        lib.nemo_speech_asr_streaming_recognize.restype = ctypes.c_int
        lib.nemo_speech_asr_stream_push_f32.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_size_t,
            ctypes.c_int32,
        ]
        lib.nemo_speech_asr_stream_push_f32.restype = ctypes.c_int
        lib.nemo_speech_asr_stream_finish.argtypes = [ctypes.c_void_p]
        lib.nemo_speech_asr_stream_finish.restype = ctypes.c_int
        lib.nemo_speech_asr_stream_next.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        lib.nemo_speech_asr_stream_next.restype = ctypes.c_int
        lib.nemo_speech_asr_stream_close.argtypes = [ctypes.c_void_p]
        lib.nemo_speech_asr_stream_close.restype = None
        lib.nemo_speech_asr_result_transcript.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        lib.nemo_speech_asr_result_transcript.restype = ctypes.c_char_p
        lib.nemo_speech_asr_result_is_final.argtypes = [ctypes.c_void_p]
        lib.nemo_speech_asr_result_is_final.restype = ctypes.c_bool
        lib.nemo_speech_asr_result_destroy.argtypes = [ctypes.c_void_p]
        lib.nemo_speech_asr_result_destroy.restype = None
        lib.nemo_speech_asr_last_error.argtypes = []
        lib.nemo_speech_asr_last_error.restype = ctypes.c_char_p

    def _last_error(self) -> str:
        try:
            msg = self._lib.nemo_speech_asr_last_error()
            return msg.decode() if msg else "unknown native error"
        except Exception:
            return "unknown native error"

    def _create(self) -> None:
        handle = ctypes.c_void_p()
        status = self._lib.nemo_speech_asr_create(
            ctypes.byref(self._recognizer_cfg), ctypes.byref(handle)
        )
        if status != OK or not handle.value:
            raise RuntimeError(f"nemo_speech_asr_create failed: {self._last_error()}")
        self._recognizer = handle

    def open_stream(self) -> int:
        opts = _RecognitionOptions(
            size=ctypes.sizeof(_RecognitionOptions),
            request_id=None,
            language_code=self._language_buf.value,
            interim_results=self._config.interim,  # finals only unless debugging
            enable_word_time_offsets=False,
            enable_automatic_punctuation=True,
            verbatim_transcripts=False,
            profanity_filter=False,
            stop_history_eou_ms=0,
            speech_contexts=ctypes.addressof(self._ctx_array)
            if self._ctx_array is not None
            else None,
            speech_context_count=len(self._contexts),
            max_alternatives=0,
            enable_speaker_diarization=False,
            max_speaker_count=0,
        )
        # Keep buffers alive for the duration of the call.
        self._last_opts = (
            opts,
            self._language_buf,
            self._ctx_array,
            self._phrase_ptrs,
            self._contexts,
            self._phrase_bufs,
        )
        handle = ctypes.c_void_p()
        status = self._lib.nemo_speech_asr_streaming_recognize(
            self._recognizer, ctypes.byref(opts), ctypes.byref(handle)
        )
        if status != OK or not handle.value:
            raise RuntimeError(f"streaming_recognize failed: {self._last_error()}")
        stream_id = handle.value
        self._open_streams.add(stream_id)
        return stream_id

    def push(self, stream_id: int, pcm_bytes: bytes, rate: int) -> None:
        if np is None:
            raise RuntimeError("numpy is required for PCM conversion")
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        status = self._lib.nemo_speech_asr_stream_push_f32(
            ctypes.c_void_p(stream_id),
            samples.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            samples.size,
            rate,
        )
        if status != OK:
            raise RuntimeError(f"push failed: {self._last_error()}")
        self._drain(stream_id, finals_only=False)

    def _drain(self, stream_id: int, finals_only: bool) -> str:
        final = ""
        for _ in range(1024):
            out = ctypes.c_void_p()
            status = self._lib.nemo_speech_asr_stream_next(
                ctypes.c_void_p(stream_id), ctypes.byref(out)
            )
            if status != OK or not out.value:
                break
            try:
                if self._lib.nemo_speech_asr_result_is_final(out):
                    text = self._lib.nemo_speech_asr_result_transcript(out, 0)
                    if text:
                        final = text.decode()
                elif finals_only:
                    break
            finally:
                self._lib.nemo_speech_asr_result_destroy(out)
        return final

    def finish(self, stream_id: int) -> str:
        status = self._lib.nemo_speech_asr_stream_finish(ctypes.c_void_p(stream_id))
        if status != OK:
            raise RuntimeError(f"finish failed: {self._last_error()}")
        final = ""
        for _ in range(1024):
            out = ctypes.c_void_p()
            status = self._lib.nemo_speech_asr_stream_next(
                ctypes.c_void_p(stream_id), ctypes.byref(out)
            )
            if status != OK or not out.value:
                break
            try:
                if self._lib.nemo_speech_asr_result_is_final(out):
                    text = self._lib.nemo_speech_asr_result_transcript(out, 0)
                    if text:
                        final = text.decode()
            finally:
                self._lib.nemo_speech_asr_result_destroy(out)
        return final

    def close_stream(self, stream_id: int) -> None:
        try:
            self._lib.nemo_speech_asr_stream_close(ctypes.c_void_p(stream_id))
        finally:
            self._open_streams.discard(stream_id)

    def open_stream_count(self) -> int:
        return len(self._open_streams)

    def destroy(self) -> None:
        for stream_id in list(self._open_streams):
            self.close_stream(stream_id)
        if self._recognizer is not None:
            self._lib.nemo_speech_asr_destroy(self._recognizer)
            self._recognizer = None


class TurnTimings:
    def __init__(self) -> None:
        self.t2_stop = 0.0
        self.t3_final = 0.0
        self.t4_sent = 0.0

    def as_dict(self) -> dict:
        return {
            "asr_finalization_ms": round((self.t3_final - self.t2_stop) * 1000, 1),
            "proxy_overhead_ms": round((self.t4_sent - self.t3_final) * 1000, 1),
            "stt_post_speech_ms": round((self.t4_sent - self.t2_stop) * 1000, 1),
        }


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    recognizer: NativeRecognizer,
    classifier: object | None,
    model_name: str,
) -> None:
    peer = writer.get_extra_info("peername")
    pcm = bytearray()
    stream_id: int | None = None
    native_error: str | None = None
    timings = TurnTimings()
    try:
        while True:
            event = await read_event(reader)
            if event is None:
                break
            kind = event["type"]
            if kind == "describe":
                writer.write(info_event(model_name))
                await writer.drain()
            elif kind == "transcribe":
                data = event["data"]
                if isinstance(data, dict) and data.get("language"):
                    LOG.info(
                        "transcribe language %s (native model is fixed)",
                        data.get("language"),
                    )
            elif kind == "audio-start":
                data = event["data"]
                rate = int(data.get("rate", 16000))
                width = int(data.get("width", 2))
                channels = int(data.get("channels", 1))
                pcm.clear()
                native_error = None
                if stream_id is not None:
                    await asyncio.to_thread(recognizer.close_stream, stream_id)
                    stream_id = None
                if width != 2 or channels != 1:
                    LOG.warning(
                        "unsupported format width=%s channels=%s", width, channels
                    )
                    continue
                try:
                    stream_id = await asyncio.to_thread(recognizer.open_stream)
                except Exception as e:
                    LOG.exception("open_stream failed for %s", peer)
                    native_error = str(e) or "open_stream failed"
                    stream_id = None
            elif kind == "audio-chunk":
                payload = event["payload"]
                if payload and stream_id is not None:
                    pcm += payload
                    try:
                        await asyncio.to_thread(
                            recognizer.push, stream_id, bytes(payload), rate
                        )
                    except Exception as e:
                        LOG.exception("push failed for %s", peer)
                        native_error = str(e) or "push failed"
                        await asyncio.to_thread(recognizer.close_stream, stream_id)
                        stream_id = None
                elif payload:
                    pcm += payload
            elif kind == "audio-stop":
                timings.t2_stop = time.monotonic()
                text = ""
                finish_ms = 0.0
                speaker_ms = 0.0
                if native_error is not None:
                    writer.write(
                        encode_event(
                            "error",
                            {
                                "text": f"ASR recognition failed: {native_error}",
                                "code": "recognition_failed",
                            },
                        )
                    )
                    await writer.drain()
                    pcm.clear()
                    continue
                if stream_id is not None:
                    t_finish = time.monotonic()

                    async def do_finish(sid: int) -> str:
                        return await asyncio.to_thread(recognizer.finish, sid)

                    stage_ms: dict[str, float] = {}

                    async def do_speaker(data: bytes) -> object | None:
                        if classifier is None or not data:
                            return None
                        t0 = time.monotonic()
                        try:
                            speaker, scores = await asyncio.to_thread(
                                classifier.identify_pcm, data, rate
                            )
                            record_speaker_result(speaker, scores, classifier)
                            stage_ms["speaker"] = (time.monotonic() - t0) * 1000.0
                            return speaker
                        except Exception:
                            LOG.exception("speaker ID failed for %s", peer)
                            return None

                    try:
                        text, speaker = await asyncio.gather(
                            do_finish(stream_id), do_speaker(bytes(pcm))
                        )
                        finish_ms = (time.monotonic() - t_finish) * 1000.0
                        speaker_ms = stage_ms.get("speaker", 0.0)
                    except Exception as e:
                        LOG.exception("finish failed for %s", peer)
                        native_error = str(e) or "finish failed"
                        text, speaker = "", None
                        speaker_ms = 0.0
                    finally:
                        await asyncio.to_thread(recognizer.close_stream, stream_id)
                        stream_id = None

                    if native_error is not None:
                        writer.write(
                            encode_event(
                                "error",
                                {
                                    "text": f"ASR recognition failed: {native_error}",
                                    "code": "recognition_failed",
                                },
                            )
                        )
                        await writer.drain()
                        pcm.clear()
                        continue
                else:
                    speaker = None
                    if classifier is not None and bytes(pcm):
                        t_speaker = time.monotonic()
                        try:
                            speaker, scores = classifier.identify_pcm(bytes(pcm), rate)
                            record_speaker_result(speaker, scores, classifier)
                        except Exception:
                            LOG.exception("speaker ID failed for %s", peer)
                            speaker = None
                        speaker_ms = (time.monotonic() - t_speaker) * 1000.0
                timings.t3_final = time.monotonic()
                raw_text = text
                normalized = normalize_vocab(text)
                if normalized != raw_text:
                    LOG.info("normalized %r -> %r", raw_text, normalized)
                text = normalized
                if is_garbage_transcript(text):
                    LOG.warning("dropping hallucinated transcript %r", text)
                    text = ""
                else:
                    text = format_transcript_with_speaker(text, speaker)
                writer.write(encode_event("transcript", {"text": text}))
                await writer.drain()
                timings.t4_sent = time.monotonic()
                LOG.info(
                    "turn peer=%s chars=%d speaker=%s finish_ms=%.1f "
                    "speaker_ms=%.1f %s",
                    peer,
                    len(text),
                    speaker,
                    finish_ms,
                    speaker_ms,
                    json.dumps(timings.as_dict()),
                )
                pcm.clear()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        if stream_id is not None:
            try:
                await asyncio.to_thread(recognizer.close_stream, stream_id)
            except Exception:
                pass
        try:
            writer.close()
        except Exception:
            pass


async def serve(
    port: int, recognizer: NativeRecognizer, classifier: object | None, model_name: str
) -> None:
    start_metrics_server()
    server = await asyncio.start_server(
        lambda r, w: handle_client(r, w, recognizer, classifier, model_name),
        "0.0.0.0",
        port,
    )
    LOG.info("nemotron-bridge listening on %d", port)
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=10300)
    parser.add_argument("--model-name", default="nemotron-speech-streaming-en-0.6b")
    parser.add_argument("--profiles", default="/app/profiles.json")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    config = NativeConfig()
    recognizer = NativeRecognizer(config)
    classifier = None
    if VoiceIdClassifier is not None:
        try:
            classifier = VoiceIdClassifier(
                model_path="/data/voice-id/model.onnx",
                profiles_path=args.profiles,
            )
        except Exception:
            LOG.exception("voice-ID init failed; continuing without speaker tags")
    LOG.info(
        "native recognizer ready model=%s gpu=%d boost=%.1f phrases=%d arena=%s",
        args.model_name,
        config.gpu,
        config.boost,
        len(config.phrases),
        config.arena_slots,
    )
    asyncio.run(serve(args.port, recognizer, classifier, args.model_name))


if __name__ == "__main__":
    main()
