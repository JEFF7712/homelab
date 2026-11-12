from __future__ import annotations

import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "gateway", ROOT / "gitops/voice/gateway/gateway.py"
)
assert spec and spec.loader
gateway = importlib.util.module_from_spec(spec)
sys.modules["gateway"] = gateway
spec.loader.exec_module(gateway)
spec = importlib.util.spec_from_file_location(
    "jarvis_display", ROOT / "gitops/voice/display/server.py"
)
assert spec and spec.loader
display = importlib.util.module_from_spec(spec)
spec.loader.exec_module(display)


class DisplayTest(unittest.IsolatedAsyncioTestCase):
    async def exchange(
        self, events: list[dict], failure: bool = False
    ) -> tuple[list[dict], object]:
        server = await asyncio.start_server(display.handle, "127.0.0.1", 0)
        assert server.sockets
        try:
            with patch.object(
                display,
                "publish",
                side_effect=RuntimeError("offline") if failure else None,
            ) as publish:
                reader, writer = await asyncio.open_connection(
                    "127.0.0.1", server.sockets[0].getsockname()[1]
                )
                for event in events:
                    writer.write(gateway.encode_event(event))
                await writer.drain()
                replies = []
                while event := await gateway.read_event(reader):
                    replies.append(event)
                await gateway.safe_close(writer)
                return replies, publish
        finally:
            server.close()
            await server.wait_closed()

    async def test_reply_published_before_silent_audio(self) -> None:
        events, publish = await self.exchange(
            [{"type": "synthesize", "data": {"text": "Done."}}]
        )
        publish.assert_called_once_with("Done.")
        self.assertEqual(
            [e["type"] for e in events], ["audio-start", "audio-chunk", "audio-stop"]
        )
        self.assertEqual(events[1]["payload"], bytes(1600))

    async def test_streamed_reply_assembled_once(self) -> None:
        _, publish = await self.exchange(
            [
                {"type": "synthesize-start"},
                {"type": "synthesize-chunk", "data": {"text": "Hello "}},
                {"type": "synthesize-chunk", "data": {"text": "there."}},
                {"type": "synthesize-stop"},
            ]
        )
        publish.assert_called_once_with("Hello there.")

    async def test_display_failure_is_not_silent_success(self) -> None:
        events, _ = await self.exchange(
            [{"type": "synthesize", "data": {"text": "Done."}}], failure=True
        )
        self.assertEqual([e["type"] for e in events], ["error"])

    async def test_oversized_reply_is_rejected_without_publish(self) -> None:
        events, publish = await self.exchange(
            [{"type": "synthesize", "data": {"text": "x" * 10001}}]
        )
        publish.assert_not_called()
        self.assertEqual(events[0]["type"], "error")


class ResponseRefreshTest(unittest.TestCase):
    def test_satellite_republishes_repeated_cached_replies(self) -> None:
        import ast
        import json
        from unittest.mock import MagicMock
        from urllib.request import Request

        source = ast.parse((ROOT / "gitops/voice/satellite/satellite.py").read_text())
        function = next(
            node
            for node in source.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_publish_display_text"
        )
        send = MagicMock()
        namespace = {"Request": Request, "urlopen": send, "json": json}
        exec(
            compile(
                ast.Module(body=[function], type_ignores=[]),
                "satellite-display",
                "exec",
            ),
            namespace,
        )
        namespace["_publish_display_text"]("Done.")
        namespace["_publish_display_text"]("Done.")
        self.assertEqual(send.call_count, 2)
        self.assertEqual(json.loads(send.call_args.args[0].data), {"text": "Done."})

    def test_http_response_endpoint_publishes_text(self) -> None:
        import json
        from http.server import ThreadingHTTPServer
        from threading import Thread
        from urllib.request import Request, urlopen

        http = ThreadingHTTPServer(("127.0.0.1", 0), display.ResponseHandler)
        thread = Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(display, "publish") as publish:
                request = Request(
                    f"http://127.0.0.1:{http.server_port}/response",
                    data=json.dumps({"text": "Done."}).encode(),
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    self.assertEqual(response.status, 204)
                publish.assert_called_once_with("Done.")
        finally:
            http.shutdown()
            http.server_close()
            thread.join(timeout=2)


class SilentSatelliteTest(unittest.TestCase):
    def test_text_only_mode_never_plays_cached_speech(self) -> None:
        import ast
        import os
        from unittest.mock import MagicMock

        source = ast.parse((ROOT / "gitops/voice/satellite/satellite.py").read_text())
        cls = next(
            node
            for node in source.body
            if isinstance(node, ast.ClassDef) and node.name == "VoiceSatelliteProtocol"
        )
        method = next(
            node
            for node in cls.body
            if isinstance(node, ast.FunctionDef) and node.name == "play_tts"
        )
        namespace = {"os": os}
        exec(
            compile(
                ast.Module(body=[method], type_ignores=[]), "satellite-silent", "exec"
            ),
            namespace,
        )
        satellite = MagicMock()
        satellite._tts_url = "http://ha/cached-spoken-done.mp3"
        satellite._tts_played = False
        with patch.dict(os.environ, {"JARVIS_TEXT_ONLY": "1"}):
            namespace["play_tts"](satellite)
        satellite.state.tts_player.play.assert_not_called()
        satellite._tts_finished.assert_not_called()
