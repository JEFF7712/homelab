import os
import pty
import socket
import struct
import threading
import time
import unittest

import numpy as np
from PIL import Image, ImageDraw

from spotify_matrix_streamer import (
    R890_STATS,
    R890_STATS_FIELDS,
    TCPMatrixStreamer,
    USBMatrixStreamer,
    decode_r889_frame,
    encode_r889_frame,
    frame_to_rgb565,
    frame_to_rgb888,
    render_record,
    render_test_album,
)


class StreamingTests(unittest.TestCase):
    def test_rgb565_wire_order(self) -> None:
        for color, pixel in (
            ("red", b"\x00\xf8"),
            ("lime", b"\xe0\x07"),
            ("blue", b"\x1f\x00"),
        ):
            self.assertEqual(
                frame_to_rgb565(Image.new("RGB", (64, 64), color)), pixel * 4096
            )
        self.assertEqual(
            frame_to_rgb565(Image.new("RGB", (64, 64), "white"), 0), bytes(8192)
        )

    def test_rejects_wrong_dimensions(self) -> None:
        with self.assertRaises(ValueError):
            frame_to_rgb565(Image.new("RGB", (32, 64)))

    def test_tcp_disconnect_and_reconnect(self) -> None:
        received = []
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            server.settimeout(3)

            def receive() -> None:
                for reply in (b"", b"A"):
                    conn, _ = server.accept()
                    with conn:
                        conn.settimeout(3)
                        conn.sendall(b"R")
                        conn.sendall(b"565")
                        payload = bytearray()
                        while len(payload) < 8192:
                            part = conn.recv(min(37, 8192 - len(payload)))
                            if not part:
                                return
                            payload.extend(part)
                        received.append(bytes(payload))
                        if reply:
                            conn.sendall(reply)

            worker = threading.Thread(target=receive, daemon=True)
            worker.start()
            streamer = TCPMatrixStreamer("127.0.0.1", server.getsockname()[1])
            try:
                image = Image.new("RGB", (64, 64), "red")
                self.assertFalse(streamer.send_frame(image))
                self.assertTrue(streamer.send_frame(image))
                worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertEqual(received, [b"\x00\xf8" * 4096] * 2)
                self.assertEqual((streamer.frames, streamer.failures), (1, 1))
            finally:
                streamer.close()

    def test_usb_uses_data_framing_and_waits_for_display(self) -> None:
        master, slave = pty.openpty()
        payload = bytearray()
        received = threading.Event()
        acknowledge = threading.Event()
        result = []
        streamer = USBMatrixStreamer(os.ttyname(slave))

        def receive() -> None:
            while len(payload) < 8196:
                payload.extend(os.read(master, min(37, 8196 - len(payload))))
            received.set()
            if acknowledge.wait(3):
                os.write(master, b"A")

        reader = threading.Thread(target=receive, daemon=True)
        sender = threading.Thread(
            target=lambda: result.append(
                streamer.send_frame(Image.new("RGB", (64, 64), "blue"))
            ),
            daemon=True,
        )
        try:
            reader.start()
            sender.start()
            self.assertTrue(received.wait(3))
            self.assertTrue(sender.is_alive())
            self.assertEqual(bytes(payload), b"S565" + b"\x1f\x00" * 4096)
            acknowledge.set()
            sender.join(3)
            reader.join(3)
            self.assertEqual(result, [True])
        finally:
            acknowledge.set()
            streamer.close()
            os.close(master)
            os.close(slave)

    def test_tcp_drops_frames_instead_of_blocking_when_pipeline_is_full(
        self,
    ) -> None:
        streamer = TCPMatrixStreamer("unused", max_in_flight=4)
        sender, receiver = socket.socketpair()
        with sender, receiver:
            sender.settimeout(1)
            receiver.settimeout(1)
            streamer.sock = sender
            image = Image.new("RGB", (64, 64), "red")
            try:
                for _ in range(4):
                    self.assertTrue(streamer.send_frame(image))
                payload = bytearray()
                while len(payload) < 4 * 8192:
                    payload.extend(receiver.recv(4 * 8192 - len(payload)))
                self.assertEqual(bytes(payload), b"\x00\xf8" * (4096 * 4))
                self.assertEqual(streamer.frames, 0)
                self.assertEqual(streamer.last_ack, 0)

                started = time.monotonic()
                self.assertTrue(streamer.send_frame(image))
                self.assertLess(time.monotonic() - started, 0.1)
                self.assertEqual(streamer.dropped_frames, 1)
                receiver.settimeout(0.1)
                with self.assertRaises(socket.timeout):
                    receiver.recv(1)

                receiver.sendall(b"AAAA")
                time.sleep(0.05)
                self.assertTrue(streamer.send_frame(Image.new("RGB", (64, 64), "blue")))
                self.assertEqual(streamer.frames, 4)
                self.assertGreater(streamer.last_ack, 0)
                self.assertEqual(streamer.failures, 0)
            finally:
                streamer.close()

    def test_tcp_reconnects_after_ack_deadline(self) -> None:
        streamer = TCPMatrixStreamer("unused", max_in_flight=4)
        sender, receiver = socket.socketpair()
        with sender, receiver:
            streamer.sock = sender
            image = Image.new("RGB", (64, 64), "red")
            try:
                self.assertTrue(streamer.send_frame(image))
                streamer._oldest_pending_since = time.monotonic() - 2.0
                self.assertTrue(streamer.send_frame(image))
                self.assertEqual(streamer.failures, 0)
                streamer._oldest_pending_since = time.monotonic() - 3.5
                self.assertFalse(streamer.send_frame(image))
                self.assertEqual(streamer.failures, 1)
                self.assertIsNone(streamer.sock)
            finally:
                streamer.close()

    def test_tcp_retries_first_failure_immediately_then_backs_off(self) -> None:
        streamer = TCPMatrixStreamer("127.0.0.1", 9, max_in_flight=4)
        sleeps: list[float] = []
        image = Image.new("RGB", (64, 64), "red")
        original_sleep = time.sleep
        time.sleep = sleeps.append  # type: ignore[assignment]
        try:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                streamer.port = probe.getsockname()[1]
                self.assertFalse(streamer.send_frame(image))
                self.assertEqual(sleeps, [])
                self.assertFalse(streamer.send_frame(image))
                self.assertEqual(sleeps, [0.25])
        finally:
            time.sleep = original_sleep
            streamer.close()

    def test_tcp_r890_queries_and_parses_receiver_stats(self) -> None:
        stats = (12_000, 360, 2, 4_000_000, 7, 1, 0, 455, 178, -61, 3)
        queries: list[bytes] = []
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            server.settimeout(3)

            def receive() -> None:
                conn, _ = server.accept()
                with conn:
                    conn.settimeout(3)
                    conn.sendall(b"R890")
                    handled = 0
                    while handled < 3:
                        hdr = bytearray()
                        while len(hdr) < 4:
                            part = conn.recv(4 - len(hdr))
                            if not part:
                                return
                            hdr.extend(part)
                        _, plen = struct.unpack("<BH", hdr[1:4])
                        if hdr[:1] == b"Q":
                            queries.append(bytes(hdr))
                            reply = b"S" + R890_STATS.pack(*stats)
                            conn.sendall(reply[:10])
                            time.sleep(0.02)
                            conn.sendall(reply[10:])
                            continue
                        body = bytearray()
                        while len(body) < plen:
                            body.extend(conn.recv(plen - len(body)))
                        conn.sendall(b"A")
                        handled += 1

            worker = threading.Thread(target=receive, daemon=True)
            worker.start()
            streamer = TCPMatrixStreamer(
                "127.0.0.1", server.getsockname()[1], max_in_flight=1
            )
            try:
                for color in ("red", "lime", "blue"):
                    self.assertTrue(
                        streamer.send_frame(Image.new("RGB", (64, 64), color))
                    )
                worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertEqual(streamer.protocol, "R890")
                self.assertEqual(queries, [b"Q\x00\x00\x00"])
                self.assertEqual((streamer.frames, streamer.failures), (3, 0))
                self.assertEqual(
                    streamer.receiver_stats,
                    dict(zip(R890_STATS_FIELDS, stats, strict=True)),
                )
            finally:
                streamer.close()

    def test_tcp_rejects_stats_reply_on_r889(self) -> None:
        streamer = TCPMatrixStreamer("unused", max_in_flight=4)
        sender, receiver = socket.socketpair()
        with sender, receiver:
            streamer.sock = sender
            streamer.protocol = "R889"
            image = Image.new("RGB", (64, 64), "blue")
            try:
                self.assertTrue(streamer.send_frame(image))
                receiver.sendall(b"S" + bytes(R890_STATS.size))
                self.assertFalse(streamer.send_frame(image))
                self.assertEqual(streamer.failures, 1)
            finally:
                streamer.close()

    def test_tcp_pipeline_rejects_invalid_reply(self) -> None:
        streamer = TCPMatrixStreamer("unused", max_in_flight=4)
        sender, receiver = socket.socketpair()
        with sender, receiver:
            streamer.sock = sender
            image = Image.new("RGB", (64, 64), "blue")
            try:
                self.assertTrue(streamer.send_frame(image))
                receiver.sendall(b"X")
                self.assertFalse(streamer.send_frame(image))
                self.assertEqual((streamer.frames, streamer.failures), (0, 1))
                self.assertIsNone(streamer.sock)
            finally:
                streamer.close()

    def test_tcp_rejects_invalid_ready_without_sending_pixels(self) -> None:
        received = []
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            server.settimeout(3)

            def receive() -> None:
                conn, _ = server.accept()
                with conn:
                    conn.settimeout(3)
                    conn.sendall(b"NOPE")
                    received.append(conn.recv(1))

            worker = threading.Thread(target=receive, daemon=True)
            worker.start()
            streamer = TCPMatrixStreamer("127.0.0.1", server.getsockname()[1])
            try:
                self.assertFalse(streamer.send_frame(Image.new("RGB", (64, 64))))
                worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertEqual(received, [b""])
                self.assertEqual((streamer.frames, streamer.failures), (0, 1))
            finally:
                streamer.close()

    def test_dithering_smooths_gradients_without_near_black_speckle(self) -> None:
        # Near-black clock background stays clean zero at 0.7 brightness
        bg = Image.new("RGB", (64, 64), (6, 8, 14))
        self.assertEqual(frame_to_rgb565(bg, brightness=0.7), bytes(8192))

        # Intentional dim UI strokes stay unbroken across all Bayer positions
        dim_stroke = Image.new("RGB", (64, 64), (40, 40, 40))
        words = np.frombuffer(frame_to_rgb565(dim_stroke, brightness=0.7), dtype="<u2")
        self.assertTrue(bool(np.all(words > 0)))
        self.assertEqual(len(set(words.tolist())), 1)

        # Midtone sweep produces monotonic 4x4 Bayer substeps beyond 16 raw levels
        block_means: list[float] = []
        for level in range(0, 256, 4):
            patch = Image.new("RGB", (64, 64), (level, 0, 0))
            raw = frame_to_rgb565(patch, brightness=1.0)
            r4 = ((np.frombuffer(raw, dtype="<u2").reshape(64, 64) >> 11) & 0x1F) >> 1
            block_means.append(float(r4[:4, :4].mean()))
        self.assertGreater(len(set(block_means)), 40)
        self.assertEqual(block_means, sorted(block_means))

    def test_tcp_negotiates_r888_full_color_protocol(self) -> None:
        received: list[bytes] = []
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            server.settimeout(3)

            def receive() -> None:
                conn, _ = server.accept()
                with conn:
                    conn.settimeout(3)
                    conn.sendall(b"R888")
                    payload = bytearray()
                    while len(payload) < 12288:
                        part = conn.recv(12288 - len(payload))
                        if not part:
                            return
                        payload.extend(part)
                    received.append(bytes(payload))
                    conn.sendall(b"A")

            worker = threading.Thread(target=receive, daemon=True)
            worker.start()
            streamer = TCPMatrixStreamer("127.0.0.1", server.getsockname()[1])
            try:
                img = Image.new("RGB", (64, 64), (12, 34, 56))
                self.assertTrue(streamer.send_frame(img, brightness=0.5))
                worker.join(3)
                self.assertEqual(streamer.protocol, "R888")
                self.assertEqual(received, [frame_to_rgb888(img, brightness=0.5)])
                self.assertEqual(received[0][:3], bytes([6, 17, 28]))
            finally:
                streamer.close()

    def test_static_deduplication_skips_identical_frames_until_keepalive_or_change(
        self,
    ) -> None:
        sent_frames = 0
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            server.settimeout(3)

            def receive() -> None:
                nonlocal sent_frames
                conn, _ = server.accept()
                with conn:
                    conn.settimeout(3)
                    conn.sendall(b"R565")
                    for _ in range(3):
                        buf = bytearray()
                        while len(buf) < 8192:
                            chunk = conn.recv(8192 - len(buf))
                            if not chunk:
                                return
                            buf.extend(chunk)
                        sent_frames += 1
                        conn.sendall(b"A")

            worker = threading.Thread(target=receive, daemon=True)
            worker.start()
            streamer = TCPMatrixStreamer(
                "127.0.0.1",
                server.getsockname()[1],
                max_in_flight=4,
                keepalive_interval=0.12,
            )
            try:
                red = Image.new("RGB", (64, 64), "red")
                blue = Image.new("RGB", (64, 64), "blue")
                # Frame 1 transmits; frames 2-4 are identical within keepalive window
                self.assertTrue(streamer.send_frame(red))
                for _ in range(3):
                    self.assertTrue(streamer.send_frame(red))
                self.assertEqual(streamer.skipped_frames, 3)

                # Wait past keepalive window -> identical frame retransmits
                time.sleep(0.14)
                self.assertTrue(streamer.send_frame(red))
                self.assertEqual(streamer.skipped_frames, 3)

                # Immediate visual change transmits without waiting for keepalive
                self.assertTrue(streamer.send_frame(blue))
                worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertEqual(sent_frames, 3)
            finally:
                streamer.close()

    def test_r565_clamps_keepalive_below_circuitpython_half_second_timeout(
        self,
    ) -> None:
        streamer = TCPMatrixStreamer("unused", max_in_flight=4, keepalive_interval=4.0)
        sender, receiver = socket.socketpair()
        with sender, receiver:
            sender.settimeout(1)
            receiver.settimeout(1)
            streamer.sock = sender
            streamer.protocol = "R565"
            red = Image.new("RGB", (64, 64), "red")
            try:
                self.assertTrue(streamer.send_frame(red))
                receiver.recv(8192)
                receiver.sendall(b"A")
                self.assertTrue(streamer.send_frame(red))
                self.assertEqual(streamer.skipped_frames, 1)

                # After 0.36s (< 0.5s CircuitPython recv timeout), R565 sends keepalive
                time.sleep(0.36)
                self.assertTrue(streamer.send_frame(red))
                self.assertEqual(len(receiver.recv(8192)), 8192)
            finally:
                streamer.close()

    def test_r889_delta_rle_roundtrip_and_hardware_brightness(self) -> None:
        base = Image.new("RGB", (64, 64), (0, 0, 0))
        draw = ImageDraw.Draw(base)
        draw.rectangle((16, 16, 48, 48), fill=(255, 80, 20))

        pkt0, rgb0, hb0 = encode_r889_frame(base, brightness=0.7, prev_rgb=None)
        self.assertEqual(pkt0[:1], b"F")
        self.assertEqual(len(pkt0), 12292)
        canvas, decoded_hb0 = decode_r889_frame(pkt0, None)
        self.assertEqual(bytes(canvas), rgb0)
        self.assertEqual(decoded_hb0, hb0)
        self.assertEqual(hb0, 178)

        # Brightness-only change uses 4-byte zero-payload header preserving full 24-bit RGB
        pkt1, rgb1, hb1 = encode_r889_frame(base, brightness=0.1, prev_rgb=rgb0)
        self.assertEqual(len(pkt1), 4)
        canvas, decoded_hb1 = decode_r889_frame(pkt1, canvas)
        self.assertEqual(bytes(canvas), rgb1)
        self.assertEqual(decoded_hb1, hb1)
        self.assertEqual(hb1, 26)

        # Small sprite change encodes compactly (< 200 bytes)
        moved = base.copy()
        ImageDraw.Draw(moved).rectangle((2, 2, 5, 5), fill=(20, 200, 255))
        pkt2, rgb2, _ = encode_r889_frame(moved, brightness=0.7, prev_rgb=rgb1)
        self.assertEqual(pkt2[:1], b"R")
        self.assertLess(len(pkt2), 200)
        canvas, _ = decode_r889_frame(pkt2, canvas)
        self.assertEqual(bytes(canvas), rgb2)

        # Spinning vinyl compresses outer corners & spindle hole losslessly
        art = render_test_album(64)
        v1 = render_record(art, 10.0, size=64)
        v2 = render_record(art, 11.5, size=64)
        pkt_v1, rgb_v1, _ = encode_r889_frame(v1, brightness=0.7, prev_rgb=None)
        canvas, _ = decode_r889_frame(pkt_v1, None)
        pkt_v2, rgb_v2, _ = encode_r889_frame(v2, brightness=0.7, prev_rgb=rgb_v1)
        self.assertLess(len(pkt_v2), 9500)
        canvas, _ = decode_r889_frame(pkt_v2, canvas)
        self.assertEqual(bytes(canvas), rgb_v2)

        # Incompressible random noise falls back to 'F' cleanly
        rng = np.random.default_rng(42)
        noise = Image.fromarray(
            rng.integers(0, 256, (64, 64, 3), dtype=np.uint8), "RGB"
        )
        pkt_noise, rgb_noise, _ = encode_r889_frame(
            noise, brightness=1.0, prev_rgb=rgb_v2
        )
        self.assertEqual(pkt_noise[:1], b"F")
        canvas, _ = decode_r889_frame(pkt_noise, canvas)
        self.assertEqual(bytes(canvas), rgb_noise)

    def test_tcp_negotiates_r889_compressed_stream(self) -> None:
        decoded_frames: list[tuple[bytes, int]] = []
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            server.settimeout(3)

            def receive() -> None:
                conn, _ = server.accept()
                with conn:
                    conn.settimeout(3)
                    conn.sendall(b"R889")
                    canvas: bytearray | None = None
                    for _ in range(3):
                        hdr = bytearray()
                        while len(hdr) < 4:
                            part = conn.recv(4 - len(hdr))
                            if not part:
                                return
                            hdr.extend(part)
                        _, plen = struct.unpack("<BH", hdr[1:4])
                        body = bytearray()
                        while len(body) < plen:
                            part = conn.recv(plen - len(body))
                            if not part:
                                return
                            body.extend(part)
                        canvas, hb = decode_r889_frame(bytes(hdr + body), canvas)
                        decoded_frames.append((bytes(canvas), hb))
                        conn.sendall(b"A")

            worker = threading.Thread(target=receive, daemon=True)
            worker.start()
            streamer = TCPMatrixStreamer(
                "127.0.0.1",
                server.getsockname()[1],
                max_in_flight=4,
                keepalive_interval=4.0,
            )
            try:
                f1 = Image.new("RGB", (64, 64), (0, 0, 0))
                ImageDraw.Draw(f1).ellipse((20, 20, 44, 44), fill=(120, 40, 250))
                f2 = f1.copy()
                ImageDraw.Draw(f2).point((10, 10), fill=(255, 255, 255))

                self.assertTrue(streamer.send_frame(f1, brightness=0.7))
                # Identical frame + brightness -> skipped on wire
                self.assertTrue(streamer.send_frame(f1, brightness=0.7))
                self.assertEqual(streamer.skipped_frames, 1)
                # Brightness change -> immediate 4-byte hardware OE brightness packet
                self.assertTrue(streamer.send_frame(f1, brightness=0.2))
                # Pixel change -> compact delta packet
                self.assertTrue(streamer.send_frame(f2, brightness=0.2))

                worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertEqual(streamer.protocol, "R889")
                self.assertEqual(
                    decoded_frames,
                    [
                        (f1.tobytes(), 178),
                        (f1.tobytes(), 51),
                        (f2.tobytes(), 51),
                    ],
                )
            finally:
                streamer.close()


if __name__ == "__main__":
    unittest.main()
