from __future__ import annotations

import io
import json
import sys
import types
import unittest
from contextlib import ExitStack, redirect_stdout
from unittest.mock import AsyncMock, MagicMock, Mock, mock_open, patch

from scripts.music_assistant_health import REMOTE_CHECK


class MusicAssistantHealthTest(unittest.TestCase):
    def test_waits_for_entities_removed_during_home_assistant_restart(self) -> None:
        responses = []
        for status in (404, 404, 200, 200):
            response = MagicMock(status=status)
            response.__aenter__ = AsyncMock(return_value=response)
            response.__aexit__ = AsyncMock(return_value=False)
            response.json = AsyncMock(return_value={"state": "idle"})
            if status == 404:
                response.raise_for_status.side_effect = AssertionError(
                    "Missing entities must be allowed to reappear during --wait"
                )
            responses.append(response)
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.get.side_effect = responses
        client = Mock(
            connect=AsyncMock(),
            disconnect=AsyncMock(),
            send_command=AsyncMock(
                side_effect=[{"available": False}, {"available": True}]
            ),
        )
        aiohttp = types.ModuleType("aiohttp")
        aiohttp.ClientSession = Mock(return_value=session)  # type: ignore[attr-defined]
        ma = types.ModuleType("music_assistant_client")
        ma.MusicAssistantClient = Mock(return_value=client)  # type: ignore[attr-defined]
        config = json.dumps(
            {
                "data": {
                    "entries": [
                        {
                            "domain": "music_assistant",
                            "data": {"url": "http://ma", "token": "test-token"},
                        }
                    ]
                }
            }
        )
        output = io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(
                patch.dict(
                    sys.modules, {"aiohttp": aiohttp, "music_assistant_client": ma}
                )
            )
            stack.enter_context(patch("builtins.open", mock_open(read_data=config)))
            stack.enter_context(patch("sys.stdin", io.StringIO("ha-test-token")))
            stack.enter_context(patch("sys.argv", ["health", "10"]))
            sleep = stack.enter_context(patch("asyncio.sleep", new_callable=AsyncMock))
            stack.enter_context(redirect_stdout(output))
            with self.assertRaises(SystemExit) as raised:
                exec(compile(REMOTE_CHECK, "<remote-health-check>", "exec"), {})
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(session.get.call_count, 4)
        sleep.assert_awaited_once_with(2)
        client.disconnect.assert_awaited_once()
        self.assertEqual(
            json.loads(output.getvalue()),
            {"music_assistant_available": True, "source": "idle", "imported": "idle"},
        )
        self.assertNotIn("test-token", output.getvalue())


if __name__ == "__main__":
    unittest.main()
