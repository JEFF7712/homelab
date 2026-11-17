import os
import pty
import socket
import threading
import time
import unittest

import numpy as np
from PIL import Image

from spotify_matrix_streamer import (
    TCPMatrixStreamer,
    USBMatrixStreamer,
    frame_to_rgb565,
    frame_to_rgb888,
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

    def test_tcp_keeps_sending_during_delayed_replies_but_caps_pending_frames(
        self,
    ) -> None:
        streamer = TCPMatrixStreamer("unused", max_in_flight=4)
        result = []
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
                worker = threading.Thread(
                    target=lambda: result.append(streamer.send_frame(image)),
                    daemon=True,
                )
                worker.start()
                receiver.settimeout(0.1)
                with self.assertRaises(socket.timeout):
                    receiver.recv(1)
                receiver.sendall(b"AAAA")
                worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(result, [True])
                self.assertEqual(streamer.frames, 4)
                self.assertGreater(streamer.last_ack, 0)
                self.assertEqual(streamer.failures, 0)
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


if __name__ == "__main__":
    unittest.main()
