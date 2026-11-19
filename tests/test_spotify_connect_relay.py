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
        # A 0.1 s startup burst plus elapsed realtime, with scheduler slack.
        self.assertGreater(total, 44100 * 4 // 4)
        self.assertLess(total, 44100 * 4 * 0.95)

    def read_all(self, proc: subprocess.Popen[bytes]) -> int:
        assert proc.stdout is not None
        total = 0
        while chunk := os.read(proc.stdout.fileno(), 65536):
            total += len(chunk)
        return total

    def test_gaps_between_tracks_do_not_accumulate_lead(self) -> None:
        # Four 0.3 s spurts of unpaced PCM separated by idle gaps, as the
        # daemon does around track loads. Each gap may only add the small
        # catch-up allowance, never another startup burst.
        proc = self.launch(
            "import os, time\n"
            "for _ in range(4):\n"
            " end = time.monotonic() + 0.3\n"
            " while time.monotonic() < end: os.write(1, b'x'*4096)\n"
            " time.sleep(0.3)\n"
        )
        # 1.2 s of spurts + 0.1 s startup burst + 3 x 0.02 s catch-up + a pipe
        # page refilled per spurt is ~1.46 s; a 0.1 s catch-up per gap is ~1.7.
        seconds = self.read_all(proc) / (44100 * 4)
        self.assertGreater(seconds, 1.2)
        self.assertLess(seconds, 1.6)

    def test_daemon_runs_only_a_pipe_page_ahead(self) -> None:
        # go-librespot writes as fast as its pipe accepts; whatever that pipe
        # holds is standing latency, so it must be one page, not 64 KiB.
        proc = self.launch(
            "import os, sys, time\n"
            "written, end = 0, time.monotonic() + 0.5\n"
            "while time.monotonic() < end: written += os.write(1, b'x'*1024)\n"
            "print(written, time.monotonic(), file=sys.stderr, flush=True)\n"
        )
        assert proc.stdout is not None and proc.stderr is not None
        received = 0
        stopped_at: float | None = None
        report = b""
        while True:
            ready, _, _ = select.select([proc.stdout, proc.stderr], [], [], 2)
            if not ready:
                break
            if proc.stderr in ready and stopped_at is None:
                report += os.read(proc.stderr.fileno(), 4096)
                if b"\n" in report:
                    written_text, stopped_text = report.split()[:2]
                    stopped_at = float(stopped_text)
                    lead = int(written_text) - received
            if proc.stdout in ready:
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    break
                if stopped_at is None:
                    received += len(chunk)
        self.assertIsNotNone(stopped_at)
        # Pipe page plus the relay's 0.1 s output buffer and read granularity.
        self.assertLess(lead, 4096 + 44100 * 4 // 10 + 8192)

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
