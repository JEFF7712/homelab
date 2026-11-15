"""Exercise the real relay with subprocess pipes and a stalled downstream reader."""

from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

RELAY = (
    Path(__file__).resolve().parents[1]
    / "gitops/music-assistant/spotify-connect-relay.py"
)


class SpotifyConnectRelayTest(unittest.TestCase):
    def launch(self, source: str) -> subprocess.Popen[bytes]:
        proc = subprocess.Popen(
            [sys.executable, str(RELAY), sys.executable, "-u", "-c", source],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.addCleanup(self.finish, proc)
        return proc

    @staticmethod
    def finish(proc: subprocess.Popen[bytes]) -> None:
        if proc.poll() is None:
            proc.terminate()
        proc.communicate(timeout=5)

    def test_preserves_pcm_and_child_exit_status(self) -> None:
        payload = bytes(range(256)) * 100
        proc = self.launch(
            "import os; os.write(1, bytes(range(256))*100); raise SystemExit(7)"
        )
        output, errors = proc.communicate(timeout=5)
        self.assertEqual(output, payload)
        self.assertEqual(proc.returncode, 7, errors.decode())

    def test_does_not_decode_a_whole_track_ahead_of_realtime(self) -> None:
        proc = self.launch("import os\nwhile True: os.write(1, b'x'*4096)")
        assert proc.stdout is not None
        total = 0
        started = time.monotonic()
        while time.monotonic() - started < 0.6:
            ready, _, _ = select.select([proc.stdout], [], [], 0.05)
            if ready:
                total += len(os.read(proc.stdout.fileno(), 65536))
        # A half-second startup burst plus elapsed realtime, with scheduler slack.
        self.assertGreater(total, 44100 * 4 // 4)
        self.assertLess(total, 44100 * 4 * 1.4)

    def test_stalled_ma_reader_does_not_block_daemon_control_or_cleanup(self) -> None:
        proc = self.launch(
            "import os, signal, threading, time\n"
            "lock=threading.Lock()\n"
            "def pause(sig, frame):\n"
            " with lock: os.write(2, b'paused\\n')\n"
            "def write():\n"
            " while True:\n"
            "  with lock: os.write(1, b'x'*16384)\n"
            "  time.sleep(0.001)\n"
            "signal.signal(signal.SIGUSR1, pause)\n"
            "print(os.getpid(), file=__import__('sys').stderr, flush=True)\n"
            "threading.Thread(target=write, daemon=True).start()\n"
            "while True: signal.pause()\n"
        )
        assert proc.stderr is not None
        child_pid = int(proc.stderr.readline())
        # Deliberately never read PCM: both the OS pipe and relay buffer fill.
        time.sleep(0.8)
        os.kill(child_pid, signal.SIGUSR1)
        ready, _, _ = select.select([proc.stderr], [], [], 2)
        self.assertTrue(ready, "pause blocked behind an audio write")
        self.assertEqual(proc.stderr.readline(), b"paused\n")
        proc.terminate()
        proc.communicate(timeout=5)
        with self.assertRaises(ProcessLookupError):
            os.kill(child_pid, 0)


if __name__ == "__main__":
    unittest.main()
