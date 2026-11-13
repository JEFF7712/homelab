"""Health checks wait for a restarting Home Assistant instead of failing."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from scripts.home_assistant import client as ha
from scripts.home_assistant.client import (
    HomeAssistantAuthError,
    HomeAssistantClient,
    HomeAssistantClientError,
)

CONFIG = {"version": "2026.11.1", "location_name": "Home", "state": "RUNNING"}


class ReadinessTest(unittest.TestCase):
    def setUp(self) -> None:
        clock = iter(range(0, 100000, 10))
        for name, value in (
            ("sleep", lambda seconds: None),
            ("monotonic", lambda: next(clock)),
        ):
            patcher = patch.object(ha.time, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = HomeAssistantClient(base_url="http://ha.test:8123", token="t")

    def respond(self, *answers: object) -> list[str]:
        calls: list[str] = []
        queue = list(answers)

        def request(method: str, path: str, payload: object = None) -> object:
            calls.append(path)
            answer = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(answer, Exception):
                raise answer
            return answer

        patcher = patch.object(self.client, "http_request", request)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_waits_through_a_restart(self) -> None:
        calls = self.respond(
            HomeAssistantClientError("Network error accessing /api/config: refused"),
            {**CONFIG, "state": "NOT_RUNNING"},
            CONFIG,
        )
        with redirect_stderr(io.StringIO()) as err:
            health = self.client.check_health()
        self.assertEqual(health["status"], "ok")
        self.assertEqual(len(calls), 3)
        self.assertIn("Waiting for Home Assistant", err.getvalue())

    def test_gives_up_after_the_timeout(self) -> None:
        self.respond(HomeAssistantClientError("Network error: refused"))
        with (
            redirect_stderr(io.StringIO()),
            self.assertRaisesRegex(HomeAssistantClientError, "not ready"),
        ):
            self.client.check_health(wait=60)

    def test_auth_failures_are_not_retried(self) -> None:
        calls = self.respond(HomeAssistantAuthError("401"))
        with self.assertRaises(HomeAssistantAuthError):
            self.client.check_health()
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
