"""Duck an external PipeWire music stream while Jarvis listens or speaks.

Music plays through the host Sendspin player rather than this satellite's own
mpv, so wake-word ducking has to lower that stream's sink-input volume. Volume
changes run on one worker thread, latest request wins, so a slow or missing
pactl never blocks the audio loop and duck/unduck can never apply out of order.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from collections.abc import Callable, Iterable
from typing import Any

_LOGGER = logging.getLogger(__name__)

MATCH_PROPERTIES = ("application.name", "node.name", "media.name")
PACTL_TIMEOUT_SECONDS = 2.0

Runner = Callable[[list[str]], str]


def _run_pactl(args: list[str]) -> str:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=PACTL_TIMEOUT_SECONDS,
        check=True,
    ).stdout


def matching_sink_inputs(
    sink_inputs: Iterable[dict[str, Any]], match: str
) -> list[int]:
    """Return sink-input indices whose identifying properties contain match."""
    needle = match.lower()
    indices = []
    for sink_input in sink_inputs:
        properties = sink_input.get("properties") or {}
        if any(
            needle in str(properties.get(key, "")).lower() for key in MATCH_PROPERTIES
        ):
            indices.append(int(sink_input["index"]))
    return indices


class StreamDucker:
    """Set matching sink inputs to a ducked or full volume in the background."""

    def __init__(
        self,
        match: str,
        ducked_percent: int = 50,
        pactl: str = "pactl",
        runner: Runner = _run_pactl,
    ) -> None:
        self._match = match
        self._ducked_percent = ducked_percent
        self._pactl = pactl
        self._runner = runner
        self._condition = threading.Condition()
        self._wanted: int | None = None
        self._thread: threading.Thread | None = None

    def duck(self) -> None:
        self._request(self._ducked_percent)

    def unduck(self) -> None:
        self._request(100)

    def _request(self, percent: int) -> None:
        with self._condition:
            self._wanted = percent
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._worker, name="stream-ducker", daemon=True
                )
                self._thread.start()
            self._condition.notify()

    def _worker(self) -> None:
        while True:
            with self._condition:
                while self._wanted is None:
                    self._condition.wait()
                percent, self._wanted = self._wanted, None
            try:
                self.apply(percent)
            # pactl missing/failing/timing out, or unexpected JSON from it.
            except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
                _LOGGER.warning(
                    "Could not set %s stream volume to %d%%: %s",
                    self._match,
                    percent,
                    type(exc).__name__,
                )

    def apply(self, percent: int) -> list[int]:
        """Synchronously set every matching stream to percent; return their indices."""
        listing = json.loads(
            self._runner([self._pactl, "-f", "json", "list", "sink-inputs"]) or "[]"
        )
        indices = matching_sink_inputs(listing, self._match)
        for index in indices:
            self._runner(
                [self._pactl, "set-sink-input-volume", str(index), f"{percent}%"]
            )
        return indices
