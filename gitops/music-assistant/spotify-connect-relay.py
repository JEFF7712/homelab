"""Keep go-librespot's PCM pipe writable while MA pauses or replaces a stream.

The pipe backend holds its control lock during writes. A detached MA reader
therefore wedges pause/stop/Connect. Drain at the PCM clock rate, forwarding
through a bounded, nonblocking output buffer. Only a stalled reader loses old
audio; the daemon remains paced and can always process control commands.
"""

from __future__ import annotations

import fcntl
import os
import select
import signal
import subprocess
import sys
import time

# Startup burst and downstream buffer bound. Every buffered second delays
# skip, pause and resume, so keep only enough to absorb scheduler jitter.
BURST_SECONDS = 0.1
# After the start, a late clock (the daemon idle while loading the next track)
# may only catch up this much. Each catch-up becomes permanent lead, because
# MA paces its own read at exactly realtime and never drains it.
CATCH_UP_SECONDS = 0.02
# go-librespot is not realtime-paced, so it keeps its pipe to us full: a
# default 64 KiB pipe is ~0.37 s of standing latency. One page is the minimum.
CHILD_PIPE_BYTES = 4096


def relay(command: list[str], *, byte_rate: int = 44100 * 2 * 2) -> int:
    frame_size = 4  # stereo s16le, as configured by MA
    chunk_size = max(frame_size, byte_rate // 100 // frame_size * frame_size)
    capacity = max(
        chunk_size, int(byte_rate * BURST_SECONDS) // frame_size * frame_size
    )
    pending = bytearray()
    child = subprocess.Popen(command, stdout=subprocess.PIPE, start_new_session=True)
    assert child.stdout is not None
    source = child.stdout.fileno()
    try:
        fcntl.fcntl(source, getattr(fcntl, "F_SETPIPE_SZ", 1031), CHILD_PIPE_BYTES)
    except OSError:
        pass  # not resizable; only latency suffers
    os.set_blocking(source, False)
    os.set_blocking(sys.stdout.fileno(), False)
    stopped = False

    def stop(signum: int, _frame: object) -> None:
        nonlocal stopped
        stopped = True
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    old_handlers = {
        sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)
    }
    # The realtime clock starts with the first PCM, not with the daemon.
    next_read: float | None = None
    eof_at: float | None = None
    try:
        while not stopped:
            now = time.monotonic()
            due = next_read is None or now >= next_read
            readable, writable, _ = select.select(
                [source] if eof_at is None and due else [],
                [sys.stdout.fileno()] if pending else [],
                [],
                0.01 if due else min(0.01, next_read - now),
            )
            if writable:
                try:
                    written = os.write(sys.stdout.fileno(), pending)
                except BlockingIOError:
                    written = 0
                del pending[:written]
            if readable:
                data = os.read(source, chunk_size)
                if not data:
                    eof_at = now
                else:
                    pending.extend(data)
                    if next_read is None:
                        next_read = now - BURST_SECONDS
                    else:
                        next_read = max(next_read, now - CATCH_UP_SECONDS)
                    next_read += len(data) / byte_rate
                    if len(pending) > capacity:
                        drop = (
                            (len(pending) - capacity + frame_size - 1)
                            // frame_size
                            * frame_size
                        )
                        del pending[:drop]
            if eof_at is not None and (not pending or now - eof_at > 2):
                break
        if eof_at is not None:
            try:
                return child.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                return 1
        return child.poll() if child.poll() is not None else 0
    except BrokenPipeError:
        return 0
    finally:
        # A provider restart must not leave a paired daemon owning the old ports.
        try:
            os.killpg(child.pid, signal.SIGTERM)
            child.wait(timeout=2)
        except ProcessLookupError:
            child.wait()
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
        child.stdout.close()
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: spotify-connect-relay.py DAEMON [ARGS...]")
    raise SystemExit(relay(sys.argv[1:]))
