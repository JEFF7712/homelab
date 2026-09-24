"""Offline tests for the Nemotron native Wyoming bridge.

Executes gitops/voice/nemotron-bridge/bridge.py with a fake `proxy`
module (speaker-ID, garbage guard) and a fake native recognizer, then
drives handle_client over loopback TCP. No GPU, network, or third-party
Wyoming libraries required.
"""

from __future__ import annotations

import asyncio
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BRIDGE_PATH = REPO_ROOT / "gitops" / "voice" / "nemotron-bridge" / "bridge.py"


class _FakeArray:
    def __init__(self, raw: bytes) -> None:
        import struct

        self._values = [v / 32768.0 for v in struct.unpack(f"<{len(raw) // 2}h", raw)]
        self.size = len(self._values)

    def astype(self, _dtype: object) -> _FakeArray:
        return self

    def __truediv__(self, _other: object) -> _FakeArray:
        return self

    @property
    def ctypes(self) -> _FakeArray:
        return self

    def data_as(self, _ctype: object) -> _FakeArray:
        return self


def make_proxy() -> types.ModuleType:
    module = types.ModuleType("proxy")

    class VoiceIdClassifier:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.calls: list[bytes] = []

        def identify_pcm(self, pcm: bytes, rate: int = 16000) -> tuple:
            self.calls.append(pcm)
            if len(pcm) < 100:
                return None, {}
            return "Rupan", {"Rupan": 0.9}

    def is_garbage_transcript(text: str) -> bool:
        return text.strip().lower() in {"", "stu", "control"}

    def format_transcript_with_speaker(transcript: str, speaker: object) -> str:
        if speaker and transcript.lower().startswith("play "):
            return f"speaker {speaker} {transcript.strip().rstrip('.!?')}"
        return transcript

    module.VoiceIdClassifier = VoiceIdClassifier  # type: ignore[attr-defined]
    module.is_garbage_transcript = is_garbage_transcript  # type: ignore[attr-defined]
    module.format_transcript_with_speaker = format_transcript_with_speaker  # type: ignore[attr-defined]
    return module


def load_bridge() -> dict:
    saved_proxy = sys.modules.get("proxy")
    saved_numpy = sys.modules.get("numpy")
    sys.modules["proxy"] = make_proxy()
    stubbed_numpy = False
    if "numpy" not in sys.modules:
        try:
            import numpy  # noqa: F401
        except ImportError:
            stub = types.ModuleType("numpy")
            stub.frombuffer = lambda buf, dtype=None: _FakeArray(bytes(buf))  # type: ignore[attr-defined]
            sys.modules["numpy"] = stub
            stubbed_numpy = True
    try:
        code = BRIDGE_PATH.read_text()
        module: dict = {"__name__": "nemotron_bridge_under_test"}
        exec(compile(code, str(BRIDGE_PATH), "exec"), module)
    finally:
        # Restore the import table so co-executed suites (voice-ID, guard)
        # observe the real environment, not our fakes.
        if saved_proxy is not None:
            sys.modules["proxy"] = saved_proxy
        else:
            sys.modules.pop("proxy", None)
        if not stubbed_numpy:
            pass
        elif saved_numpy is not None:
            sys.modules["numpy"] = saved_numpy
        else:
            sys.modules.pop("numpy", None)
    return module


BRIDGE = load_bridge()


class FakeNative:
    """Scripted native recognizer fake with lifecycle tracking."""

    def __init__(self, transcripts: list[str] | None = None, fail_on: str = "") -> None:
        self.transcripts = list(transcripts or ["hello world"])
        self.fail_on = fail_on
        self.pushed: list[bytes] = []
        self.opened = 0
        self.closed = 0
        self.finished = 0
        self._live: set[int] = set()
        self._next_id = 1

    def open_stream(self) -> int:
        if self.fail_on == "open":
            raise RuntimeError("boom-open")
        self.opened += 1
        stream_id = self._next_id
        self._next_id += 1
        self._live.add(stream_id)
        return stream_id

    def push(self, stream_id: int, pcm: bytes, rate: int) -> None:
        if self.fail_on == "push":
            raise RuntimeError("boom-push")
        assert stream_id in self._live
        self.pushed.append(pcm)

    def finish(self, stream_id: int) -> str:
        if self.fail_on == "finish":
            raise RuntimeError("boom-finish")
        assert stream_id in self._live
        self.finished += 1
        if self.transcripts:
            return self.transcripts.pop(0)
        return ""

    def close_stream(self, stream_id: int) -> None:
        self.closed += 1
        self._live.discard(stream_id)

    def open_stream_count(self) -> int:
        return len(self._live)


class FakeClassifier:
    def __init__(self) -> None:
        self.calls: list[bytes] = []

    def identify_pcm(self, pcm: bytes, rate: int = 16000) -> tuple:
        self.calls.append(pcm)
        return "Rupan", {"Rupan": 0.9}


def decode_frames(buf: bytes) -> list[dict]:
    """Minimal Wyoming frame decoder for assertions."""
    import json

    events: list[dict] = []
    while buf:
        line, sep, rest = buf.partition(b"\n")
        if not sep:
            break
        try:
            header = json.loads(line)
        except ValueError:
            break
        data_len = int(header.get("data_length") or 0)
        payload_len = int(header.get("payload_length") or 0)
        if len(rest) < data_len + payload_len:
            break
        data = json.loads(rest[:data_len]) if data_len else {}
        events.append({"type": header.get("type"), "data": data})
        buf = rest[data_len + payload_len :]
    return events


async def drive(
    script: list[tuple],
    native: FakeNative,
    classifier: FakeClassifier | None,
    abrupt_close: bool = False,
) -> tuple[bytes, FakeNative]:
    """Serve handle_client on loopback, play script, return raw replies."""
    got = bytearray()
    done = asyncio.Event()

    async def on_connect(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            await BRIDGE["handle_client"](reader, writer, native, classifier, "m")
        finally:
            done.set()

    server = await asyncio.start_server(on_connect, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    for kind, data, payload in script:
        writer.write(BRIDGE["encode_event"](kind, data, payload or b""))
    await writer.drain()
    if abrupt_close:
        writer.close()
        await asyncio.wait_for(done.wait(), timeout=10)
    else:
        for _ in range(200):
            try:
                chunk = await asyncio.wait_for(reader.read(65536), timeout=0.05)
                if chunk:
                    got += chunk
            except asyncio.TimeoutError:
                pass
            if b'"transcript"' in got or b'"error"' in got:
                await asyncio.sleep(0.3)
                try:
                    while True:
                        chunk = await asyncio.wait_for(reader.read(65536), timeout=0.2)
                        if not chunk:
                            break
                        got += chunk
                except asyncio.TimeoutError:
                    pass
                break
        writer.close()
        await asyncio.wait_for(done.wait(), timeout=10)
    server.close()
    return bytes(got), native


def turn(
    pcm: bytes, rate: int = 16000, width: int = 2, channels: int = 1
) -> list[tuple]:
    fmt = {"rate": rate, "width": width, "channels": channels}
    return [
        ("audio-start", dict(fmt), b""),
        ("audio-chunk", dict(fmt), pcm),
        ("audio-stop", {}, b""),
    ]


def transcripts_of(raw: bytes) -> list[dict]:
    return [e for e in decode_frames(raw) if e["type"] == "transcript"]


class BridgeSessionTest(unittest.TestCase):
    def test_full_turn_emits_single_transcript(self) -> None:
        native = FakeNative(transcripts=["turn on the lights"])
        raw, _ = asyncio.run(drive(turn(b"\x00\x02" * 1600), native, FakeClassifier()))
        self.assertEqual(native.opened, 1)
        self.assertEqual(native.finished, 1)
        self.assertEqual(native.open_stream_count(), 0)
        self.assertEqual(len(native.pushed), 1)
        found = transcripts_of(raw)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["data"]["text"], "turn on the lights")

    def test_native_config_validation(self) -> None:
        import os

        old = dict(os.environ)
        try:
            os.environ["NEMO_LIB"] = ""
            os.environ["NEMO_MODEL"] = ""
            with self.assertRaises(ValueError):
                BRIDGE["NativeConfig"]().validate()
            os.environ["NEMO_LIB"] = "/x.so"
            os.environ["NEMO_MODEL"] = "/m.gguf"
            os.environ["NEMO_BOOST"] = "99"
            with self.assertRaises(ValueError):
                BRIDGE["NativeConfig"]().validate()
            os.environ["NEMO_BOOST"] = "2.5"
            BRIDGE["NativeConfig"]().validate()
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_info_event_shape(self) -> None:
        raw = BRIDGE["info_event"]("my-model")
        self.assertIn(b"my-model", raw)
        self.assertIn(b"nemotron-native", raw)

    def test_framing_round_trip_with_payload(self) -> None:
        payload = b"\x01\x02" * 800
        frame = BRIDGE["encode_event"]("audio-chunk", {"rate": 16000}, payload)

        async def run() -> dict | None:
            reader = asyncio.StreamReader()
            reader.feed_data(frame)
            reader.feed_eof()
            return await BRIDGE["read_event"](reader)

        event = asyncio.run(run())
        assert event is not None
        self.assertEqual(event["type"], "audio-chunk")
        self.assertEqual(event["payload"], payload)


class BridgeVocabNormalizationTest(unittest.TestCase):
    def test_positive_pairs(self) -> None:
        norm = BRIDGE["normalize_vocab"]
        self.assertEqual(norm("turn on all govy lights"), "turn on all govee lights")
        self.assertEqual(norm("turn off the govi lights"), "turn off the govee lights")
        self.assertEqual(norm("all gov lights to red"), "all govee lights to red")
        self.assertEqual(
            norm("set the govy light to fifty"), "set the govee light to fifty"
        )
        self.assertEqual(norm("govi bulbs downstairs"), "govee bulbs downstairs")
        self.assertEqual(norm("gov bulb on"), "govee bulb on")
        self.assertEqual(norm("the govy lamp"), "the govee lamp")
        self.assertEqual(norm("govy light strip"), "govee light strip")

    def test_case_preserved(self) -> None:
        norm = BRIDGE["normalize_vocab"]
        self.assertEqual(norm("Govy lights on"), "Govee lights on")

    def test_negative_no_rewrite(self) -> None:
        norm = BRIDGE["normalize_vocab"]
        self.assertEqual(norm("the governor spoke"), "the governor spoke")
        self.assertEqual(norm("the government said so"), "the government said so")
        self.assertEqual(norm("the gov said hello"), "the gov said hello")
        self.assertEqual(norm("gov"), "gov")
        self.assertEqual(norm("vote for gov"), "vote for gov")
        self.assertEqual(norm("turn on the lights"), "turn on the lights")
        self.assertEqual(norm("play some Kanye"), "play some Kanye")

    def test_end_to_end_normalized_transcript(self) -> None:
        native = FakeNative(transcripts=["turn on all govy lights"])
        raw, _ = asyncio.run(drive(turn(b"\x00\x01" * 1600), native, FakeClassifier()))
        found = transcripts_of(raw)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["data"]["text"], "turn on all govee lights")


class BridgeFaultTest(unittest.TestCase):
    def test_failed_finish_isolates_turn(self) -> None:
        native = FakeNative(transcripts=["second turn ok"], fail_on="finish")
        raw, _ = asyncio.run(drive(turn(b"\x00\x01" * 1600), native, FakeClassifier()))
        errors = [e for e in decode_frames(raw) if e["type"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["data"]["code"], "recognition_failed")
        self.assertEqual(len(transcripts_of(raw)), 0)
        self.assertEqual(native.open_stream_count(), 0)
        native.fail_on = ""
        raw, _ = asyncio.run(drive(turn(b"\x00\x01" * 1600), native, FakeClassifier()))
        found = transcripts_of(raw)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["data"]["text"], "second turn ok")

    def test_failed_open_stream_emits_error(self) -> None:
        native = FakeNative(transcripts=["recovered"], fail_on="open")
        raw, _ = asyncio.run(drive(turn(b"\x00\x01" * 1600), native, FakeClassifier()))
        errors = [e for e in decode_frames(raw) if e["type"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["data"]["code"], "recognition_failed")
        self.assertEqual(len(transcripts_of(raw)), 0)
        native.fail_on = ""
        raw, _ = asyncio.run(drive(turn(b"\x00\x01" * 1600), native, FakeClassifier()))
        found = transcripts_of(raw)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["data"]["text"], "recovered")

    def test_garbage_transcript_dropped(self) -> None:
        native = FakeNative(transcripts=["Stu"])
        raw, _ = asyncio.run(drive(turn(b"\x00\x01" * 1600), native, FakeClassifier()))
        found = transcripts_of(raw)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["data"]["text"], "")

    def test_speaker_prefix_for_music(self) -> None:
        native = FakeNative(transcripts=["play some Kanye"])
        raw, _ = asyncio.run(drive(turn(b"\x00\x01" * 1600), native, FakeClassifier()))
        found = transcripts_of(raw)
        self.assertTrue(found[0]["data"]["text"].startswith("speaker Rupan play"))

    def test_disconnect_closes_stream(self) -> None:
        native = FakeNative(transcripts=["never"])
        script = [
            ("audio-start", {"rate": 16000, "width": 2, "channels": 1}, b""),
            (
                "audio-chunk",
                {"rate": 16000, "width": 2, "channels": 1},
                b"\x00\x01" * 800,
            ),
        ]
        asyncio.run(drive(script, native, FakeClassifier(), abrupt_close=True))
        self.assertEqual(native.open_stream_count(), 0)
        self.assertEqual(native.finished, 0)

    def test_unsupported_format_fails_turn_not_service(self) -> None:
        native = FakeNative(transcripts=["should not appear"])
        script = turn(b"\x00\x01" * 800, channels=2)
        raw, _ = asyncio.run(drive(script, native, FakeClassifier()))
        found = transcripts_of(raw)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["data"]["text"], "")
        self.assertEqual(native.opened, 0)


class NativePackagingAndContractTest(unittest.TestCase):
    def test_proxy_module_packaged_and_in_sync(self) -> None:
        voice_id_proxy = REPO_ROOT / "gitops" / "voice" / "voice-id" / "proxy.py"
        nemotron_proxy = REPO_ROOT / "gitops" / "voice" / "nemotron-bridge" / "proxy.py"
        self.assertTrue(voice_id_proxy.is_file(), "voice-id/proxy.py missing")
        self.assertTrue(nemotron_proxy.is_file(), "nemotron-bridge/proxy.py missing")
        self.assertEqual(
            nemotron_proxy.read_bytes(),
            voice_id_proxy.read_bytes(),
            "nemotron-bridge/proxy.py must stay in sync with voice-id/proxy.py",
        )

    def test_packaging_uses_upstream_not_replacement_recognizer(self):
        folder = REPO_ROOT / "gitops/voice/nemotron-bridge"
        self.assertFalse((folder / "nemo_speech_asr_c.c").exists())
        dockerfile = (folder / "Dockerfile").read_text()
        self.assertIn("https://github.com/NVIDIA/NeMo-Speech.cpp.git", dockerfile)
        self.assertIn("07003daa7eefea542076310722ccaa89709ee3c3", dockerfile)
        self.assertIn("cmake --install build", dockerfile)
        self.assertIn("verify_contract.py --abi-only", dockerfile)

    def test_verifier_rejects_missing_library_and_model(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "verify_contract",
            REPO_ROOT / "gitops/voice/nemotron-bridge/verify_contract.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertFalse(module.verify_library_symbols("/missing/libnemo.so"))
        self.assertFalse(module.verify_model_file("/missing/model.gguf"))


if __name__ == "__main__":
    unittest.main()
