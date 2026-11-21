from __future__ import annotations

import importlib.util
import json
import math
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from array import array
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPT = Path(__file__).resolve().parents[1] / "flake/modules/matrix-audio.py"
spec = importlib.util.spec_from_file_location("matrix_audio", SCRIPT)
assert spec and spec.loader
audio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audio)


def packet(**changes: object) -> bytes:
    value = dict(
        session="test",
        seq=1,
        active=True,
        volume=0.25,
        sent_at=100.0,
        events=[dict(kind="boom", strength=0.7)],
    )
    value.update(changes)
    return json.dumps(value).encode()


class SessionTests(unittest.TestCase):
    def test_events_require_a_fresh_lease_and_stop_on_off_or_mute(self) -> None:
        session = audio.Session()
        self.assertEqual(len(session.receive(packet(), 1, 100)), 1)
        self.assertTrue(session.enabled(1.7))
        self.assertFalse(session.enabled(1.9))
        self.assertEqual(session.receive(packet(seq=2, active=False), 2, 100), [])
        self.assertFalse(session.enabled(2))
        session.receive(packet(seq=3, volume=0), 3, 100)
        self.assertFalse(session.enabled(3))

    def test_bad_or_stale_packets_do_not_extend_the_lease(self) -> None:
        session = audio.Session()
        session.receive(packet(), 1, 100)
        for value in (
            b"{",
            b"null",
            packet(sent_at=90),
            packet(volume=math.nan),
            packet(active="yes"),
            packet(events=[dict(kind="arbitrary-file", strength=1)]),
            packet(events=[dict(kind="boom", strength=-1)]),
            packet(seq=True),
            packet(seq=2, events=[None]),
            packet(seq=2, events=[{}]),
        ):
            self.assertEqual(session.receive(value, 2, 100), [])
            self.assertFalse(session.enabled(2))
        self.assertEqual(session.rejected, 10)

    def test_old_sequences_and_retired_sessions_cannot_resume_playback(self) -> None:
        session = audio.Session()
        session.receive(packet(seq=2), 1, 100)
        self.assertEqual(session.receive(packet(seq=1), 1.1, 100), [])
        session.receive(packet(session="new", active=False), 1.2, 100)
        self.assertEqual(session.receive(packet(seq=3), 1.3, 100), [])
        self.assertFalse(session.enabled(1.3))

    def test_deeply_nested_invalid_packet_does_not_crash_receiver(self) -> None:
        session = audio.Session()
        self.assertEqual(session.receive(b"[" * 2000 + b"]" * 2000, 1, 100), [])
        self.assertEqual(session.rejected, 1)


class PlayerTests(unittest.TestCase):
    def test_samples_are_distinct_bounded_pcm_with_silent_edges(self) -> None:
        sounds = []
        for kind in audio.KINDS:
            data = audio.synthesize(kind, 1)
            samples = array("h", data)
            self.assertGreater(len(samples), audio.RATE // 4)
            self.assertLessEqual(len(samples), audio.RATE * 2)
            self.assertLessEqual(max(abs(n) for n in samples), 27900)
            self.assertEqual(samples[0], 0)
            self.assertGreater(sum(abs(n) for n in samples), 10000)
            sounds.append(data)
        self.assertEqual(len(set(sounds)), len(audio.KINDS))

    def test_voice_budget_and_immediate_stop(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(audio.subprocess, "Popen") as popen,
        ):
            popen.side_effect = lambda *a, **kw: MagicMock(poll=lambda: None)
            player = audio.Player(Path(directory))
            for _ in range(20):
                player.play(dict(kind="boom", strength=1), 0.25, "bedroom")
            self.assertEqual(len(player.voices), audio.MAX_VOICES)
            self.assertEqual(player.dropped, 20 - audio.MAX_VOICES)
            voices = list(player.voices)
            player.silence()
            self.assertEqual(player.voices, [])
            for voice in voices:
                voice.kill.assert_called_once()
                voice.wait.assert_called_once()

    def test_pulse_failure_is_counted_and_reaped(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                audio.subprocess, "Popen", return_value=MagicMock(poll=lambda: 1)
            ),
        ):
            player = audio.Player(Path(directory))
            player.play(dict(kind="boom", strength=1), 0.25, "bedroom")
            player.reap()
            self.assertEqual(player.failed, 1)
            self.assertEqual(player.voices, [])

    def test_monitor_yields_to_music_and_only_selects_bose_sink(self) -> None:
        monitor = audio.SpeakerMonitor("AA:BB")
        sinks = [dict(name="bluez_output.AA_BB.1", index=7)]
        inputs = [dict(sink=7, corked=False, properties={"application.name": "Music"})]

        def one_poll(_: float) -> None:
            monitor.stop.set()

        with (
            patch.object(
                audio.subprocess,
                "check_output",
                side_effect=[json.dumps(sinks), json.dumps(inputs)],
            ),
            patch.object(monitor.stop, "wait", side_effect=one_poll),
        ):
            monitor.run()
        self.assertTrue(monitor.busy)
        self.assertEqual(monitor.sink, sinks[0]["name"])

    def test_monitor_does_not_treat_own_voices_as_music(self) -> None:
        monitor = audio.SpeakerMonitor("AA:BB")
        sinks = [dict(name="bluez_output.AA_BB.1", index=7)]
        inputs = [
            dict(sink=7, corked=False, properties={"application.name": audio.APP})
        ]
        with (
            patch.object(
                audio.subprocess,
                "check_output",
                side_effect=[json.dumps(sinks), json.dumps(inputs)],
            ),
            patch.object(
                monitor.stop, "wait", side_effect=lambda _: monitor.stop.set()
            ),
        ):
            monitor.run()
        self.assertFalse(monitor.busy)


class ReceiverTests(unittest.TestCase):
    def test_receiver_plays_then_yields_and_expires_without_hardware(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "inputs.json"
            inputs.write_text("[]")
            for name, code in {
                "pactl": "import json,sys; from pathlib import Path; print(Path(sys.argv[0]).with_name('inputs.json').read_text() if sys.argv[-1]=='sink-inputs' else json.dumps([dict(name='bluez_output.AA_BB.1',index=7)]))",
                "pacat": "import time; time.sleep(10)",
            }.items():
                script = root / name
                script.write_text(f"#!{sys.executable}\n{code}\n")
                script.chmod(0o700)
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--directory",
                    directory,
                    "--mac",
                    "AA:BB",
                ],
                env={
                    **os.environ,
                    "PATH": directory + os.pathsep + os.environ.get("PATH", ""),
                },
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )

            def health() -> dict:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=1
                ) as response:
                    return json.load(response)

            def until(predicate) -> dict:
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    try:
                        value = health()
                        if predicate(value):
                            return value
                    except OSError:
                        pass
                    time.sleep(0.05)
                self.fail("receiver did not reach expected state")

            try:
                until(lambda h: h["connected"] and not h["yielding"])
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:

                    def send(seq: int, **changes: object) -> None:
                        sender.sendto(
                            packet(seq=seq, sent_at=time.time(), **changes),
                            ("127.0.0.1", port),
                        )

                    send(1)
                    until(lambda h: h["played"] == 1 and h["voices"] == 1)
                    inputs.write_text(
                        json.dumps(
                            [
                                dict(
                                    sink=7,
                                    corked=False,
                                    properties={"application.name": "Music"},
                                )
                            ]
                        )
                    )
                    until(lambda h: h["yielding"] and h["voices"] == 0)
                    send(2)
                    self.assertEqual(health()["played"], 1)
                    inputs.write_text("[]")
                    until(lambda h: not h["yielding"])
                    send(3)
                    until(lambda h: h["played"] == 2)
                    until(lambda h: not h["active"] and h["voices"] == 0)
                    send(4)
                    until(lambda h: h["played"] == 3)
                    send(5, active=False)
                    until(lambda h: not h["active"] and h["voices"] == 0)
            finally:
                process.terminate()
                _, errors = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, errors.decode())


if __name__ == "__main__":
    unittest.main()
