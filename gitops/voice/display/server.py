"""Publish Assist replies on the face, retaining the existing Wyoming identity.

HA expects audio from its configured TTS engine. Return 50ms of silence after
publishing the text successfully; no model, speech generation or audio fallback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.request import Request, urlopen

from gateway import encode_event, info_event, read_event, safe_close

LOG = logging.getLogger(__name__)
MAX_TEXT_LENGTH = 10000


def publish(text: str) -> None:
    payload = json.dumps(
        {
            "state": uuid.uuid4().hex,
            "attributes": {"friendly_name": "Jarvis response", "text": text},
        }
    ).encode()
    request = Request(
        os.environ["HA_URL"].rstrip("/") + "/api/states/sensor.jarvis_response",
        data=payload,
        headers={
            "Authorization": "Bearer " + os.environ["HA_TOKEN"],
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        if response.status not in (200, 201):
            raise RuntimeError("Home Assistant rejected display update")


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        text = ""
        while event := await asyncio.wait_for(read_event(reader), timeout=15):
            kind = event["type"]
            if kind == "describe":
                writer.write(info_event("tts"))
                await writer.drain()
                return
            if kind not in {
                "synthesize",
                "synthesize-start",
                "synthesize-chunk",
                "synthesize-stop",
            }:
                raise ValueError("unsupported display request")
            piece = event["data"].get("text", "")
            if not isinstance(piece, str):
                raise ValueError("text must be a string")
            text += piece
            if len(text) > MAX_TEXT_LENGTH:
                raise ValueError("display response exceeds maximum length")
            if kind in {"synthesize", "synthesize-stop"}:
                await asyncio.to_thread(publish, text)
                writer.write(
                    encode_event(
                        {
                            "type": "audio-start",
                            "data": {"rate": 16000, "width": 2, "channels": 1},
                        }
                    )
                )
                writer.write(
                    encode_event(
                        {
                            "type": "audio-chunk",
                            "data": {"rate": 16000, "width": 2, "channels": 1},
                            "payload": bytes(1600),
                        }
                    )
                )
                writer.write(encode_event({"type": "audio-stop"}))
                await writer.drain()
                return
    except Exception:
        LOG.exception("Could not deliver reply to display")
        writer.write(
            encode_event(
                {
                    "type": "error",
                    "data": {
                        "code": "display-failed",
                        "text": "Could not update Jarvis display",
                    },
                }
            )
        )
        await writer.drain()
    finally:
        await safe_close(writer)


class ResponseHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if self.path != "/response" or not 0 < length <= 65536:
                self.send_error(400)
                return
            text = json.loads(self.rfile.read(length))["text"]
            if not isinstance(text, str) or len(text) > MAX_TEXT_LENGTH:
                self.send_error(400)
                return
            publish(text)
            self.send_response(204)
            self.end_headers()
        except Exception:
            LOG.exception("Could not publish satellite response")
            self.send_error(502)


async def main() -> None:
    http = ThreadingHTTPServer(("0.0.0.0", 10202), ResponseHandler)
    Thread(target=http.serve_forever, daemon=True).start()
    server = await asyncio.start_server(handle, "0.0.0.0", 10201)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
