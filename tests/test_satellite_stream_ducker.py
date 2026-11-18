"""Offline tests for ducking the host Sendspin music stream from the satellite."""

from __future__ import annotations

import importlib.util
import json
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "satellite_stream_ducker", ROOT / "gitops/voice/satellite/stream_ducker.py"
)
assert SPEC and SPEC.loader
stream_ducker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stream_ducker)

SINK_INPUTS = [
    {"index": 7, "properties": {"application.name": "Sendspin", "media.role": "music"}},
    {"index": 9, "properties": {"application.name": "mpv", "media.name": "tts.wav"}},
    {"index": 11, "properties": {"node.name": "sendspin"}},
    {"index": 12, "properties": {}},
]


class FakePactl:
    def __init__(self, listing: list[dict] | None = None) -> None:
        self.listing = SINK_INPUTS if listing is None else listing
        self.calls: list[list[str]] = []
        self.done = threading.Event()

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if args[1:] == ["-f", "json", "list", "sink-inputs"]:
            return json.dumps(self.listing)
        self.done.set()
        return ""


class StreamDuckerTest(unittest.TestCase):
    def test_only_named_music_streams_are_ducked(self) -> None:
        self.assertEqual(
            stream_ducker.matching_sink_inputs(SINK_INPUTS, "sendspin"), [7, 11]
        )

    def test_apply_sets_each_matching_stream(self) -> None:
        pactl = FakePactl()
        ducker = stream_ducker.StreamDucker("sendspin", runner=pactl)
        self.assertEqual(ducker.apply(50), [7, 11])
        self.assertEqual(
            pactl.calls[1:],
            [
                ["pactl", "set-sink-input-volume", "7", "50%"],
                ["pactl", "set-sink-input-volume", "11", "50%"],
            ],
        )

    def test_duck_then_unduck_ends_at_full_volume_without_blocking(self) -> None:
        gate = threading.Event()
        pactl = FakePactl([SINK_INPUTS[0]])

        def slow(args: list[str]) -> str:
            gate.wait(2)
            return pactl(args)

        ducker = stream_ducker.StreamDucker("sendspin", runner=slow)
        started = time.monotonic()
        ducker.duck()
        ducker.unduck()
        self.assertLess(time.monotonic() - started, 0.5)
        gate.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            volumes = [c[-1] for c in pactl.calls if "set-sink-input-volume" in c]
            if volumes and volumes[-1] == "100%":
                break
            time.sleep(0.01)
        self.assertEqual(volumes[-1], "100%")

    def test_missing_pactl_is_logged_not_raised(self) -> None:
        def broken(args: list[str]) -> str:
            raise FileNotFoundError("pactl")

        ducker = stream_ducker.StreamDucker("sendspin", runner=broken)
        with self.assertLogs(stream_ducker._LOGGER, "WARNING"):
            ducker.duck()
            deadline = time.monotonic() + 2
            while ducker._wanted is not None and time.monotonic() < deadline:
                time.sleep(0.01)
            time.sleep(0.05)


class StreamNameContractTest(unittest.TestCase):
    def test_satellite_match_names_the_host_sendspin_stream(self) -> None:
        import re

        import yaml

        module = (ROOT / "flake/modules/sendspin-player.nix").read_text()
        stream_name = re.search(r'streamName = "([^"]+)";', module).group(1)
        manifests = yaml.safe_load_all(
            (ROOT / "gitops/voice/satellite.yaml").read_text()
        )
        deployment = next(m for m in manifests if m["kind"] == "Deployment")
        env = {
            item["name"]: item.get("value")
            for item in deployment["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        props = [{"index": 1, "properties": {"application.name": stream_name}}]
        self.assertEqual(
            stream_ducker.matching_sink_inputs(props, env["MUSIC_STREAM_MATCH"]), [1]
        )


if __name__ == "__main__":
    unittest.main()
