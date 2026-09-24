from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
import unittest
from pathlib import Path

PATH = Path(__file__).parents[1] / "gitops/voice/gateway/gateway.py"
SPEC = importlib.util.spec_from_file_location("voice_gateway", PATH)
assert SPEC and SPEC.loader
GATEWAY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = GATEWAY
SPEC.loader.exec_module(GATEWAY)


class GatewayProtocolTest(unittest.TestCase):
    def test_event_round_trip(self) -> None:
        async def run() -> dict:
            reader = asyncio.StreamReader()
            event = {"type": "audio-chunk", "data": {"rate": 16000}, "payload": b"pcm"}
            reader.feed_data(GATEWAY.encode_event(event))
            reader.feed_eof()
            return await GATEWAY.read_event(reader)

        self.assertEqual(
            asyncio.run(run()),
            {"type": "audio-chunk", "data": {"rate": 16000}, "payload": b"pcm"},
        )

    def test_backend_order_is_preserved(self) -> None:
        backends = GATEWAY.parse_backends("primary=one:1,fallback=two:2")
        self.assertEqual(
            [backend.name for backend in backends], ["primary", "fallback"]
        )

    def test_stt_native_latency_metric(self) -> None:
        metrics = GATEWAY.Metrics("stt")
        turn = GATEWAY.TurnMetrics("stt", "nemotron", metrics)
        turn.client_event({"type": "audio-stop", "data": {}, "payload": b""})
        turn.backend_event({"type": "transcript", "data": {}, "payload": b""})
        rendered = metrics.render([]).decode()
        self.assertIn("jarvis_stt_vad_to_final_seconds_count", rendered)

    def test_tts_native_metrics(self) -> None:
        metrics = GATEWAY.Metrics("tts")
        turn = GATEWAY.TurnMetrics("tts", "chatterbox", metrics)
        turn.client_event({"type": "synthesize", "data": {}, "payload": b""})
        turn.backend_event(
            {
                "type": "audio-start",
                "data": {"rate": 24000, "width": 2, "channels": 1},
                "payload": b"",
            }
        )
        turn.backend_event({"type": "audio-chunk", "data": {}, "payload": b"x" * 48000})
        turn.backend_event({"type": "audio-stop", "data": {}, "payload": b""})
        rendered = metrics.render([]).decode()
        self.assertIn("jarvis_tts_time_to_first_audio_seconds_count", rendered)
        self.assertIn("jarvis_tts_synthesis_realtime_factor_count", rendered)

    def test_tts_retries_piper_when_primary_fails_before_audio(self) -> None:
        async def run() -> tuple[list[dict], str]:
            async def failing(reader, writer):
                await GATEWAY.read_event(reader)
                writer.close()
                await writer.wait_closed()

            async def fallback(reader, writer):
                await GATEWAY.read_event(reader)
                writer.write(
                    GATEWAY.encode_event(
                        {
                            "type": "audio-start",
                            "data": {"rate": 1, "width": 2, "channels": 1},
                        }
                    )
                )
                writer.write(
                    GATEWAY.encode_event(
                        {
                            "type": "audio-chunk",
                            "data": {"rate": 1, "width": 2, "channels": 1},
                            "payload": b"ok",
                        }
                    )
                )
                writer.write(GATEWAY.encode_event({"type": "audio-stop", "data": {}}))
                await writer.drain()
                writer.close()
                await writer.wait_closed()

            first = await asyncio.start_server(failing, "127.0.0.1", 0)
            second = await asyncio.start_server(fallback, "127.0.0.1", 0)
            metrics = GATEWAY.Metrics("tts")
            gateway = GATEWAY.Gateway(
                "tts",
                [
                    GATEWAY.Backend(
                        "chatterbox", "127.0.0.1", first.sockets[0].getsockname()[1]
                    ),
                    GATEWAY.Backend(
                        "piper", "127.0.0.1", second.sockets[0].getsockname()[1]
                    ),
                ],
                metrics,
                connect_timeout=0.2,
            )
            listener = await asyncio.start_server(gateway.handle, "127.0.0.1", 0)
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", listener.sockets[0].getsockname()[1]
            )
            writer.write(
                GATEWAY.encode_event({"type": "synthesize", "data": {"text": "Done."}})
            )
            await writer.drain()
            events = []
            while (event := await GATEWAY.read_event(reader)) is not None:
                events.append(event)
                if event["type"] == "audio-stop":
                    break
            rendered = metrics.render([]).decode()
            writer.close()
            await writer.wait_closed()
            listener.close()
            first.close()
            second.close()
            await listener.wait_closed()
            await first.wait_closed()
            await second.wait_closed()
            return events, rendered

        events, rendered = asyncio.run(run())
        self.assertEqual(
            [event["type"] for event in events],
            ["audio-start", "audio-chunk", "audio-stop"],
        )
        self.assertIn(
            'jarvis_gateway_fallbacks_total{backend="piper",mode="tts"} 1.0', rendered
        )

    def test_tts_does_not_splice_fallback_after_audio_started(self) -> None:
        async def run() -> tuple[list[dict], str]:
            async def partial(reader, writer):
                await GATEWAY.read_event(reader)
                fmt = {"rate": 1, "width": 2, "channels": 1}
                writer.write(GATEWAY.encode_event({"type": "audio-start", "data": fmt}))
                writer.write(
                    GATEWAY.encode_event(
                        {"type": "audio-chunk", "data": fmt, "payload": b"partial"}
                    )
                )
                await writer.drain()
                writer.close()
                await writer.wait_closed()

            fallback_called = False

            async def fallback(reader, writer):
                nonlocal fallback_called
                fallback_called = True
                writer.close()
                await writer.wait_closed()

            first = await asyncio.start_server(partial, "127.0.0.1", 0)
            second = await asyncio.start_server(fallback, "127.0.0.1", 0)
            metrics = GATEWAY.Metrics("tts")
            gateway = GATEWAY.Gateway(
                "tts",
                [
                    GATEWAY.Backend(
                        "chatterbox", "127.0.0.1", first.sockets[0].getsockname()[1]
                    ),
                    GATEWAY.Backend(
                        "piper", "127.0.0.1", second.sockets[0].getsockname()[1]
                    ),
                ],
                metrics,
                connect_timeout=0.2,
            )
            listener = await asyncio.start_server(gateway.handle, "127.0.0.1", 0)
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", listener.sockets[0].getsockname()[1]
            )
            writer.write(
                GATEWAY.encode_event({"type": "synthesize", "data": {"text": "Done."}})
            )
            await writer.drain()
            events = []
            while (event := await GATEWAY.read_event(reader)) is not None:
                events.append(event)
            writer.close()
            await writer.wait_closed()
            listener.close()
            first.close()
            second.close()
            await listener.wait_closed()
            await first.wait_closed()
            await second.wait_closed()
            return events, rendered(metrics), fallback_called

        def rendered(metrics):
            return metrics.render([]).decode()

        events, metrics_text, fallback_called = asyncio.run(run())
        self.assertEqual(
            [event["type"] for event in events], ["audio-start", "audio-chunk"]
        )
        self.assertFalse(fallback_called)
        self.assertNotIn("jarvis_gateway_fallbacks_total", metrics_text)

    def test_stable_gateway_discovery(self) -> None:
        async def run() -> tuple[dict, dict]:
            # TTS discovery
            metrics_tts = GATEWAY.Metrics("tts")
            gw_tts = GATEWAY.Gateway(
                "tts", [GATEWAY.Backend("dummy", "127.0.0.1", 9999)], metrics_tts
            )
            server_tts = await asyncio.start_server(gw_tts.handle, "127.0.0.1", 0)
            port_tts = server_tts.sockets[0].getsockname()[1]

            r, w = await asyncio.open_connection("127.0.0.1", port_tts)
            w.write(GATEWAY.encode_event({"type": "describe"}))
            await w.drain()
            tts_info = await GATEWAY.read_event(r)
            w.close()
            await w.wait_closed()
            server_tts.close()
            await server_tts.wait_closed()

            # STT discovery
            metrics_stt = GATEWAY.Metrics("stt")
            gw_stt = GATEWAY.Gateway(
                "stt", [GATEWAY.Backend("dummy", "127.0.0.1", 9999)], metrics_stt
            )
            server_stt = await asyncio.start_server(gw_stt.handle, "127.0.0.1", 0)
            port_stt = server_stt.sockets[0].getsockname()[1]

            r, w = await asyncio.open_connection("127.0.0.1", port_stt)
            w.write(GATEWAY.encode_event({"type": "describe"}))
            await w.drain()
            stt_info = await GATEWAY.read_event(r)
            w.close()
            await w.wait_closed()
            server_stt.close()
            await server_stt.wait_closed()

            return tts_info, stt_info

        tts_info, stt_info = asyncio.run(run())
        self.assertEqual(tts_info["type"], "info")
        tts_voices = tts_info["data"]["tts"][0]["voices"]
        self.assertEqual(tts_voices[0]["name"], "jarvis")
        self.assertTrue(tts_info["data"]["tts"][0]["supports_synthesize_streaming"])

        self.assertEqual(stt_info["type"], "info")
        self.assertEqual(stt_info["data"]["asr"][0]["models"][0]["name"], "nemotron")

    def test_tts_voice_mapping_rewrites_for_piper(self) -> None:
        async def run() -> tuple[dict, str]:
            piper_received: list[dict] = []

            async def mock_piper(reader, writer):
                while (ev := await GATEWAY.read_event(reader)) is not None:
                    piper_received.append(ev)
                    if ev["type"] in {"synthesize", "synthesize-stop"}:
                        writer.write(
                            GATEWAY.encode_event(
                                {
                                    "type": "audio-start",
                                    "data": {"rate": 22050, "width": 2, "channels": 1},
                                }
                            )
                        )
                        writer.write(
                            GATEWAY.encode_event(
                                {
                                    "type": "audio-chunk",
                                    "data": {},
                                    "payload": b"piperpcm",
                                }
                            )
                        )
                        writer.write(
                            GATEWAY.encode_event({"type": "audio-stop", "data": {}})
                        )
                        await writer.drain()
                        break
                writer.close()
                await writer.wait_closed()

            piper_server = await asyncio.start_server(mock_piper, "127.0.0.1", 0)
            piper_port = piper_server.sockets[0].getsockname()[1]

            metrics = GATEWAY.Metrics("tts")
            gw = GATEWAY.Gateway(
                "tts",
                [GATEWAY.Backend("piper", "127.0.0.1", piper_port)],
                metrics,
                voice_map={"piper": {"jarvis": "en_GB-alan-medium"}},
            )
            gw_server = await asyncio.start_server(gw.handle, "127.0.0.1", 0)
            gw_port = gw_server.sockets[0].getsockname()[1]

            r, w = await asyncio.open_connection("127.0.0.1", gw_port)
            # Send HA standard request with voice "jarvis"
            w.write(
                GATEWAY.encode_event(
                    {
                        "type": "synthesize",
                        "data": {"text": "Hello", "voice": {"name": "jarvis"}},
                    }
                )
            )
            await w.drain()

            resp_events = []
            while (ev := await GATEWAY.read_event(r)) is not None:
                resp_events.append(ev)
                if ev["type"] == "audio-stop":
                    break
            w.close()
            await w.wait_closed()
            gw_server.close()
            piper_server.close()
            await gw_server.wait_closed()
            await piper_server.wait_closed()

            rendered = metrics.render(gw.backends).decode()
            return piper_received[0], rendered

        first_piper_ev, rendered = asyncio.run(run())
        self.assertEqual(first_piper_ev["data"]["voice"]["name"], "en_GB-alan-medium")
        self.assertIn("jarvis_tts_time_to_first_audio_seconds_count", rendered)
        self.assertIn("jarvis_tts_synthesis_success_total", rendered)

    def test_stt_fallback_on_backend_error_and_timeout(self) -> None:
        async def run() -> tuple[dict, str]:
            async def failing_nemotron(reader, writer):
                while (ev := await GATEWAY.read_event(reader)) is not None:
                    if ev["type"] == "audio-stop":
                        # Emit explicit recognition error
                        writer.write(
                            GATEWAY.encode_event(
                                {
                                    "type": "error",
                                    "data": {
                                        "text": "ASR failed",
                                        "code": "recognition_failed",
                                    },
                                }
                            )
                        )
                        await writer.drain()
                        break
                writer.close()
                await writer.wait_closed()

            async def ok_whisper(reader, writer):
                saw_chunks = 0
                while (ev := await GATEWAY.read_event(reader)) is not None:
                    if ev["type"] == "audio-chunk":
                        saw_chunks += 1
                    elif ev["type"] == "audio-stop":
                        writer.write(
                            GATEWAY.encode_event(
                                {
                                    "type": "transcript",
                                    "data": {
                                        "text": f"whisper decoded {saw_chunks} chunks"
                                    },
                                }
                            )
                        )
                        await writer.drain()
                        break
                writer.close()
                await writer.wait_closed()

            nemo_srv = await asyncio.start_server(failing_nemotron, "127.0.0.1", 0)
            whisp_srv = await asyncio.start_server(ok_whisper, "127.0.0.1", 0)
            nemo_port = nemo_srv.sockets[0].getsockname()[1]
            whisp_port = whisp_srv.sockets[0].getsockname()[1]

            metrics = GATEWAY.Metrics("stt")
            gw = GATEWAY.Gateway(
                "stt",
                [
                    GATEWAY.Backend("nemotron", "127.0.0.1", nemo_port),
                    GATEWAY.Backend("local", "127.0.0.1", whisp_port),
                ],
                metrics,
                response_timeout=0.5,
            )
            gw_srv = await asyncio.start_server(gw.handle, "127.0.0.1", 0)
            gw_port = gw_srv.sockets[0].getsockname()[1]

            r, w = await asyncio.open_connection("127.0.0.1", gw_port)
            w.write(
                GATEWAY.encode_event({"type": "transcribe", "data": {"language": "en"}})
            )
            w.write(
                GATEWAY.encode_event(
                    {
                        "type": "audio-start",
                        "data": {"rate": 16000, "width": 2, "channels": 1},
                    }
                )
            )
            w.write(
                GATEWAY.encode_event(
                    {"type": "audio-chunk", "data": {}, "payload": b"\x00\x01" * 800}
                )
            )
            w.write(
                GATEWAY.encode_event(
                    {"type": "audio-chunk", "data": {}, "payload": b"\x00\x02" * 800}
                )
            )
            w.write(GATEWAY.encode_event({"type": "audio-stop", "data": {}}))
            await w.drain()

            resp = await GATEWAY.read_event(r)
            w.close()
            await w.wait_closed()
            gw_srv.close()
            nemo_srv.close()
            whisp_srv.close()
            await gw_srv.wait_closed()
            await nemo_srv.wait_closed()
            await whisp_srv.wait_closed()

            rendered = metrics.render(gw.backends).decode()
            return resp, rendered

        resp, rendered = asyncio.run(run())
        self.assertIsNotNone(resp)
        self.assertEqual(resp["type"], "transcript")
        self.assertEqual(resp["data"]["text"], "whisper decoded 2 chunks")
        self.assertIn(
            'jarvis_gateway_fallbacks_total{backend="local",mode="stt"} 1.0', rendered
        )
        self.assertIn(
            'jarvis_gateway_backend_failures_total{backend="nemotron",mode="stt"} 1.0',
            rendered,
        )

    def test_stt_genuine_empty_speech_not_retried(self) -> None:
        async def run() -> tuple[dict, bool]:
            fallback_called = False

            async def ok_nemotron(reader, writer):
                while (ev := await GATEWAY.read_event(reader)) is not None:
                    if ev["type"] == "audio-stop":
                        # Return empty speech transcript (e.g. silence or hallucination filtered)
                        writer.write(
                            GATEWAY.encode_event(
                                {"type": "transcript", "data": {"text": ""}}
                            )
                        )
                        await writer.drain()
                        break
                writer.close()
                await writer.wait_closed()

            async def mock_fallback(reader, writer):
                nonlocal fallback_called
                fallback_called = True
                writer.close()
                await writer.wait_closed()

            nemo_srv = await asyncio.start_server(ok_nemotron, "127.0.0.1", 0)
            whisp_srv = await asyncio.start_server(mock_fallback, "127.0.0.1", 0)

            metrics = GATEWAY.Metrics("stt")
            gw = GATEWAY.Gateway(
                "stt",
                [
                    GATEWAY.Backend(
                        "nemotron", "127.0.0.1", nemo_srv.sockets[0].getsockname()[1]
                    ),
                    GATEWAY.Backend(
                        "local", "127.0.0.1", whisp_srv.sockets[0].getsockname()[1]
                    ),
                ],
                metrics,
            )
            gw_srv = await asyncio.start_server(gw.handle, "127.0.0.1", 0)

            r, w = await asyncio.open_connection(
                "127.0.0.1", gw_srv.sockets[0].getsockname()[1]
            )
            w.write(
                GATEWAY.encode_event({"type": "audio-start", "data": {"rate": 16000}})
            )
            w.write(GATEWAY.encode_event({"type": "audio-stop", "data": {}}))
            await w.drain()

            resp = await GATEWAY.read_event(r)
            w.close()
            await w.wait_closed()
            gw_srv.close()
            nemo_srv.close()
            whisp_srv.close()
            await gw_srv.wait_closed()
            await nemo_srv.wait_closed()
            await whisp_srv.wait_closed()

            return resp, fallback_called

        resp, fallback_called = asyncio.run(run())
        self.assertIsNotNone(resp)
        self.assertEqual(resp["type"], "transcript")
        self.assertEqual(resp["data"]["text"], "")
        self.assertFalse(fallback_called)

    def test_circuit_breaking_and_outcome_recovery(self) -> None:
        async def run() -> None:
            metrics = GATEWAY.Metrics("tts")
            backend = GATEWAY.Backend("cb", "127.0.0.1", 9999)
            gw = GATEWAY.Gateway(
                "tts", [backend], metrics, failure_threshold=2, cooldown_seconds=0.1
            )

            # Failure 1
            gw.record_failure(backend)
            self.assertEqual(backend.failures, 1)
            self.assertEqual(backend.open_until, 0.0)

            # Failure 2: reaches threshold -> circuit opens
            gw.record_failure(backend)
            self.assertEqual(backend.failures, 2)
            self.assertGreater(backend.open_until, time.monotonic())
            self.assertFalse(backend.is_available(time.monotonic()))

            # TCP probe does not reset failures
            backend.healthy = True
            self.assertEqual(backend.failures, 2)

            # Wait for cooldown to expire
            await asyncio.sleep(0.15)
            self.assertLessEqual(backend.open_until, time.monotonic())

            # Useful outcome success resets circuit
            gw.record_success(backend)
            self.assertEqual(backend.failures, 0)
            self.assertEqual(backend.open_until, 0.0)
            self.assertTrue(backend.is_available(time.monotonic()))

        asyncio.run(run())

    def test_readiness_and_liveness_render(self) -> None:
        metrics = GATEWAY.Metrics("tts")
        b1 = GATEWAY.Backend("primary", "127.0.0.1", 10201)
        b2 = GATEWAY.Backend("secondary", "127.0.0.1", 10200)

        # Both unhealthy
        b1.healthy = False
        b2.healthy = False
        rendered = metrics.render([b1, b2]).decode()
        self.assertIn('jarvis_gateway_usable_backends{mode="tts"} 0', rendered)
        self.assertIn(
            'jarvis_gateway_backend_available{backend="primary",mode="tts"} 0', rendered
        )

        # One becomes healthy
        b1.healthy = True
        rendered = metrics.render([b1, b2]).decode()
        self.assertIn('jarvis_gateway_usable_backends{mode="tts"} 1', rendered)
        self.assertIn(
            'jarvis_gateway_backend_available{backend="primary",mode="tts"} 1', rendered
        )

    def test_malformed_and_oversized_frames(self) -> None:
        async def run() -> None:
            # Test oversized header
            reader = asyncio.StreamReader()
            reader.feed_data(b" " * (GATEWAY.MAX_HEADER_LENGTH + 10) + b"\n")
            with self.assertRaises(ValueError):
                await GATEWAY.read_event(reader)

            # Test invalid JSON header
            reader = asyncio.StreamReader()
            reader.feed_data(b"not json\n")
            with self.assertRaises(ValueError):
                await GATEWAY.read_event(reader)

            # Test oversized payload length
            reader = asyncio.StreamReader()
            reader.feed_data(b'{"type": "test", "payload_length": 99999999}\n')
            with self.assertRaises(ValueError):
                await GATEWAY.read_event(reader)

    def test_stt_fallback_on_premature_eof(self) -> None:
        async def run() -> tuple[dict, str]:
            async def eof_primary(reader, writer):
                while (ev := await GATEWAY.read_event(reader)) is not None:
                    if ev["type"] == "audio-stop":
                        # Close abruptly
                        break
                writer.close()
                await writer.wait_closed()

            async def ok_whisper(reader, writer):
                while (ev := await GATEWAY.read_event(reader)) is not None:
                    if ev["type"] == "audio-stop":
                        writer.write(
                            GATEWAY.encode_event(
                                {
                                    "type": "transcript",
                                    "data": {"text": "recovered from eof"},
                                }
                            )
                        )
                        await writer.drain()
                        break
                writer.close()
                await writer.wait_closed()

            p_srv = await asyncio.start_server(eof_primary, "127.0.0.1", 0)
            fb_srv = await asyncio.start_server(ok_whisper, "127.0.0.1", 0)

            metrics = GATEWAY.Metrics("stt")
            gw = GATEWAY.Gateway(
                "stt",
                [
                    GATEWAY.Backend(
                        "primary", "127.0.0.1", p_srv.sockets[0].getsockname()[1]
                    ),
                    GATEWAY.Backend(
                        "fallback", "127.0.0.1", fb_srv.sockets[0].getsockname()[1]
                    ),
                ],
                metrics,
                response_timeout=0.5,
            )
            gw_srv = await asyncio.start_server(gw.handle, "127.0.0.1", 0)

            r, w = await asyncio.open_connection(
                "127.0.0.1", gw_srv.sockets[0].getsockname()[1]
            )
            w.write(
                GATEWAY.encode_event({"type": "audio-start", "data": {"rate": 16000}})
            )
            w.write(
                GATEWAY.encode_event(
                    {"type": "audio-chunk", "data": {}, "payload": b"\x00\x01" * 800}
                )
            )
            w.write(GATEWAY.encode_event({"type": "audio-stop", "data": {}}))
            await w.drain()

            resp = await GATEWAY.read_event(r)
            w.close()
            await w.wait_closed()
            gw_srv.close()
            p_srv.close()
            fb_srv.close()
            await gw_srv.wait_closed()
            await p_srv.wait_closed()
            await fb_srv.wait_closed()

            rendered = metrics.render(gw.backends).decode()
            return resp, rendered

        resp, rendered = asyncio.run(run())
        self.assertIsNotNone(resp)
        self.assertEqual(resp["data"]["text"], "recovered from eof")
        self.assertIn(
            'jarvis_gateway_fallbacks_total{backend="fallback",mode="stt"} 1.0',
            rendered,
        )

    def test_stt_fallback_on_response_timeout(self) -> None:
        async def run() -> tuple[dict, str]:
            async def hanging_primary(reader, writer):
                while (ev := await GATEWAY.read_event(reader)) is not None:
                    if ev["type"] == "audio-stop":
                        # Hang without responding
                        await asyncio.sleep(5)
                        break
                writer.close()
                await writer.wait_closed()

            async def ok_whisper(reader, writer):
                while (ev := await GATEWAY.read_event(reader)) is not None:
                    if ev["type"] == "audio-stop":
                        writer.write(
                            GATEWAY.encode_event(
                                {
                                    "type": "transcript",
                                    "data": {"text": "recovered from timeout"},
                                }
                            )
                        )
                        await writer.drain()
                        break
                writer.close()
                await writer.wait_closed()

            p_srv = await asyncio.start_server(hanging_primary, "127.0.0.1", 0)
            fb_srv = await asyncio.start_server(ok_whisper, "127.0.0.1", 0)

            metrics = GATEWAY.Metrics("stt")
            gw = GATEWAY.Gateway(
                "stt",
                [
                    GATEWAY.Backend(
                        "primary", "127.0.0.1", p_srv.sockets[0].getsockname()[1]
                    ),
                    GATEWAY.Backend(
                        "fallback", "127.0.0.1", fb_srv.sockets[0].getsockname()[1]
                    ),
                ],
                metrics,
                response_timeout=0.1,  # Fast timeout for test
            )
            gw_srv = await asyncio.start_server(gw.handle, "127.0.0.1", 0)

            r, w = await asyncio.open_connection(
                "127.0.0.1", gw_srv.sockets[0].getsockname()[1]
            )
            w.write(
                GATEWAY.encode_event({"type": "audio-start", "data": {"rate": 16000}})
            )
            w.write(GATEWAY.encode_event({"type": "audio-stop", "data": {}}))
            await w.drain()

            resp = await GATEWAY.read_event(r)
            w.close()
            await w.wait_closed()
            gw_srv.close()
            p_srv.close()
            fb_srv.close()
            await gw_srv.wait_closed()
            await p_srv.wait_closed()
            await fb_srv.wait_closed()

            rendered = metrics.render(gw.backends).decode()
            return resp, rendered

        resp, rendered = asyncio.run(run())
        self.assertIsNotNone(resp)
        self.assertEqual(resp["data"]["text"], "recovered from timeout")
        self.assertIn(
            'jarvis_gateway_fallbacks_total{backend="fallback",mode="stt"} 1.0',
            rendered,
        )

    def test_all_backends_down_behavior(self) -> None:
        async def run() -> tuple[dict | None, str]:
            metrics = GATEWAY.Metrics("tts")
            # Point at non-listening ports
            gw = GATEWAY.Gateway(
                "tts",
                [
                    GATEWAY.Backend("cb", "127.0.0.1", 49991),
                    GATEWAY.Backend("piper", "127.0.0.1", 49992),
                ],
                metrics,
                connect_timeout=0.05,
            )
            gw_srv = await asyncio.start_server(gw.handle, "127.0.0.1", 0)
            r, w = await asyncio.open_connection(
                "127.0.0.1", gw_srv.sockets[0].getsockname()[1]
            )
            w.write(
                GATEWAY.encode_event({"type": "synthesize", "data": {"text": "hello"}})
            )
            await w.drain()
            resp = await GATEWAY.read_event(r)
            w.close()
            await w.wait_closed()
            gw_srv.close()
            await gw_srv.wait_closed()
            rendered = metrics.render(gw.backends).decode()
            return resp, rendered

        resp, rendered = asyncio.run(run())
        self.assertIn("jarvis_gateway_rejected_connections_total", rendered)
        self.assertIn('jarvis_gateway_usable_backends{mode="tts"} 0', rendered)

    def test_oversized_text_rejected(self) -> None:
        async def run() -> dict:
            metrics = GATEWAY.Metrics("tts")
            gw = GATEWAY.Gateway(
                "tts", [GATEWAY.Backend("dummy", "127.0.0.1", 9999)], metrics
            )
            gw_srv = await asyncio.start_server(gw.handle, "127.0.0.1", 0)

            r, w = await asyncio.open_connection(
                "127.0.0.1", gw_srv.sockets[0].getsockname()[1]
            )
            huge_text = "a" * (GATEWAY.MAX_TEXT_LENGTH + 50)
            w.write(
                GATEWAY.encode_event(
                    {"type": "synthesize", "data": {"text": huge_text}}
                )
            )
            await w.drain()

            resp = await GATEWAY.read_event(r)
            w.close()
            await w.wait_closed()
            gw_srv.close()
            await gw_srv.wait_closed()
            return resp

        resp = asyncio.run(run())
        self.assertEqual(resp["type"], "error")
        self.assertEqual(resp["data"]["code"], "text_too_long")

    def test_client_disconnect_cancellation(self) -> None:
        async def run() -> None:
            backend_cancelled = asyncio.Event()

            async def mock_tts(reader, writer):
                try:
                    await GATEWAY.read_event(reader)
                    writer.write(
                        GATEWAY.encode_event(
                            {
                                "type": "audio-start",
                                "data": {"rate": 22050, "width": 2, "channels": 1},
                            }
                        )
                    )
                    await writer.drain()
                    while True:
                        await asyncio.sleep(0.01)
                        writer.write(
                            GATEWAY.encode_event(
                                {
                                    "type": "audio-chunk",
                                    "data": {},
                                    "payload": b"x" * 100,
                                }
                            )
                        )
                        await writer.drain()
                except (
                    asyncio.CancelledError,
                    ConnectionResetError,
                    BrokenPipeError,
                    OSError,
                ):
                    backend_cancelled.set()
                finally:
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception:
                        pass

            b_srv = await asyncio.start_server(mock_tts, "127.0.0.1", 0)
            metrics = GATEWAY.Metrics("tts")
            gw = GATEWAY.Gateway(
                "tts",
                [GATEWAY.Backend("cb", "127.0.0.1", b_srv.sockets[0].getsockname()[1])],
                metrics,
                response_timeout=0.2,
            )
            gw_srv = await asyncio.start_server(gw.handle, "127.0.0.1", 0)

            r, w = await asyncio.open_connection(
                "127.0.0.1", gw_srv.sockets[0].getsockname()[1]
            )
            w.write(
                GATEWAY.encode_event({"type": "synthesize", "data": {"text": "hello"}})
            )
            await w.drain()

            ev = await GATEWAY.read_event(r)
            self.assertEqual(ev["type"], "audio-start")
            w.close()
            await w.wait_closed()

            # Wait briefly to let cancellation propagate to backend
            await asyncio.sleep(0.05)
            gw_srv.close()
            b_srv.close()
            await gw_srv.wait_closed()
            await b_srv.wait_closed()

        asyncio.run(run())


class VoiceNetworkPolicyScrapeCoverageTest(unittest.TestCase):
    def test_allow_prometheus_scrapes_covers_all_voice_metrics(self) -> None:
        import yaml

        voice_dir = Path(__file__).resolve().parent.parent / "gitops" / "voice"
        netpol_path = voice_dir / "network-policy.yaml"

        with netpol_path.open() as f:
            netpol_docs = list(yaml.safe_load_all(f))

        scrape_pol = next(
            (
                d
                for d in netpol_docs
                if d and d.get("metadata", {}).get("name") == "allow-prometheus-scrapes"
            ),
            None,
        )
        self.assertIsNotNone(
            scrape_pol, "allow-prometheus-scrapes network policy not found"
        )

        # Extract allowed ports
        allowed_ports = set()
        for ing in scrape_pol["spec"].get("ingress", []):
            for p in ing.get("ports", []):
                allowed_ports.add(p["port"])

        # Must include all 4 scrapable service ports: 8000, 8001, 8080, 9100
        self.assertIn(8000, allowed_ports)
        self.assertIn(8001, allowed_ports)
        self.assertIn(8080, allowed_ports)
        self.assertIn(9100, allowed_ports)

        # Extract allowed app labels in podSelector
        allowed_apps = set()
        for expr in scrape_pol["spec"]["podSelector"].get("matchExpressions", []):
            if expr.get("key") == "app" and expr.get("operator") == "In":
                allowed_apps.update(expr.get("values", []))

        # Scan all manifests in gitops/voice for ServiceMonitors and Services
        service_monitors = []
        services = {}

        for yml_file in voice_dir.glob("*.yaml"):
            with yml_file.open() as f:
                try:
                    docs = list(yaml.safe_load_all(f))
                except Exception:
                    continue
                for doc in docs:
                    if not doc or not isinstance(doc, dict):
                        continue
                    kind = doc.get("kind")
                    if kind == "ServiceMonitor":
                        service_monitors.append((yml_file.name, doc))
                    elif kind == "Service":
                        name = doc.get("metadata", {}).get("name")
                        if name:
                            services[name] = doc

        self.assertGreaterEqual(len(service_monitors), 5)

        for filename, sm in service_monitors:
            sm_name = sm.get("metadata", {}).get("name")
            target_labels = (
                sm.get("spec", {}).get("selector", {}).get("matchLabels", {})
            )
            matching_services = [
                s
                for s in services.values()
                if all(
                    s.get("metadata", {}).get("labels", {}).get(k) == v
                    for k, v in target_labels.items()
                )
            ]
            self.assertTrue(
                matching_services,
                f"No service found matching ServiceMonitor {sm_name} in {filename}",
            )

            sm_endpoints = sm.get("spec", {}).get("endpoints", [])
            for ep in sm_endpoints:
                ep_port = ep.get("port")
                for svc in matching_services:
                    svc_selector = svc.get("spec", {}).get("selector", {})
                    app_label = svc_selector.get("app")
                    if app_label:
                        self.assertIn(
                            app_label,
                            allowed_apps,
                            f"Pod app label '{app_label}' for Service '{svc['metadata']['name']}' not in allow-prometheus-scrapes policy",
                        )
                    for port_spec in svc.get("spec", {}).get("ports", []):
                        if (
                            port_spec.get("name") == ep_port
                            or port_spec.get("port") == ep_port
                        ):
                            target_port = port_spec.get(
                                "targetPort", port_spec.get("port")
                            )
                            port_num = port_spec.get("port")
                            if isinstance(target_port, int):
                                self.assertIn(
                                    target_port,
                                    allowed_ports,
                                    f"Port {target_port} for Service '{svc['metadata']['name']}' not allowed in policy",
                                )
                            elif isinstance(port_num, int):
                                self.assertIn(
                                    port_num,
                                    allowed_ports,
                                    f"Port {port_num} for Service '{svc['metadata']['name']}' not allowed in policy",
                                )


if __name__ == "__main__":
    unittest.main()
