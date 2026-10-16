#!/usr/bin/env python3
"""Display the spinning vinyl hardware test over the dedicated USB data port."""

import argparse
import time

from spotify_matrix_streamer import USBMatrixStreamer, render_record, render_test_album


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="Dedicated USB data port (default: auto-detect)")
    parser.add_argument("--fps", type=float, default=20)
    args = parser.parse_args()
    if not 1 <= args.fps <= 60:
        parser.error("fps must be between 1 and 60")
    streamer = USBMatrixStreamer(args.port)
    art = render_test_album()
    started = time.monotonic()
    print(f"Streaming USB vinyl at {args.fps:g} FPS", flush=True)
    try:
        while True:
            tick = time.monotonic()
            frame = render_record(art, (tick - started) * 180)
            streamer.send_frame(frame, brightness=0.7)
            time.sleep(max(0, 1 / args.fps - (time.monotonic() - tick)))
    except KeyboardInterrupt:
        pass
    finally:
        streamer.close()


if __name__ == "__main__":
    main()
