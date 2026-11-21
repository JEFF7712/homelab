from __future__ import annotations

import json
import socket
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from fireworks_audio import FireworksAudio
from ha_source import HomeAssistantSource
from modes.fireworks import FireworksShow


class ClientTests(unittest.TestCase):
    def test_crackle_clusters_are_coalesced_instead_of_emitting_every_frame(
        self,
    ) -> None:
        show = FireworksShow(np.random.default_rng(1))
        for _ in range(60):
            show.sound("crackle")
            show.step(1 / 60)
        self.assertLessEqual(len(show.sounds), 7)
        self.assertGreaterEqual(len(show.sounds), 6)

    def test_loopback_sends_real_events_and_stop_packet_in_order(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(("127.0.0.1", 0))
            receiver.settimeout(1)
            client = FireworksAudio("127.0.0.1", receiver.getsockname()[1])
            show = FireworksShow(np.random.default_rng(1))
            show.launch(32, "peony", apex=20)
            self.assertEqual(show.sounds[0]["kind"], "launch")
            for _ in range(120):
                show.step(1 / 60)
            self.assertIn("boom", [e["kind"] for e in show.sounds])
            client.update(True, 0.25, list(show.sounds), 1)
            playing = json.loads(receiver.recv(8192))
            self.assertTrue(playing["active"])
            self.assertIn("boom", [e["kind"] for e in playing["events"]])
            client.close()
            stopped = json.loads(receiver.recv(8192))
            self.assertFalse(stopped["active"])
            self.assertGreater(stopped["seq"], playing["seq"])

    def test_visual_delay_is_bounded_and_disable_flushes_old_frames(self) -> None:
        client = FireworksAudio(None)
        client.active = True
        for i in range(200):
            frame = Image.new("RGB", (1, 1), (i, 0, 0))
            result = client.delay(frame, i / 60, 0.3)
        self.assertAlmostEqual(result.getpixel((0, 0))[0], 181, delta=1)
        self.assertLessEqual(len(client.frames), 90)
        client.update(False, 0, [], 4)
        self.assertEqual(len(client.frames), 0)
        self.assertIs(client.delay(frame, 4, 0.3), frame)
        client.close()

    def test_socket_backpressure_does_not_escape_into_rendering(self) -> None:
        client = FireworksAudio("127.0.0.1")
        with patch.object(client, "socket") as sock:
            sock.sendto.side_effect = BlockingIOError()
            client.update(True, 0.25, [dict(kind="boom", strength=1)], 1)
        self.assertEqual(client.errors, 1)
        client.close()

    def test_controls_parse_updates_and_fail_closed_when_removed(self) -> None:
        source = HomeAssistantSource(
            "http://ha.test",
            "test",
            "media_player.test",
            fireworks_audio_entity="input_boolean.audio",
            fireworks_volume_entity="input_number.volume",
            fireworks_delay_entity="input_number.delay",
        )
        source._apply_hub_entity("input_boolean.audio", {"s": "on"})
        source._apply_hub_entity("input_number.volume", {"s": "35"})
        source._apply_hub_entity("input_number.delay", {"s": "450"})
        state = source.hub_state()
        self.assertTrue(state.fireworks_audio)
        self.assertEqual(state.fireworks_volume, 0.35)
        self.assertEqual(state.fireworks_delay, 0.45)
        source._apply_hub_entity("input_boolean.audio", None)
        self.assertFalse(source.hub_state().fireworks_audio)
        source._apply_hub_entity("input_number.volume", {"s": "nan"})
        self.assertEqual(source.hub_state().fireworks_volume, 0)


if __name__ == "__main__":
    unittest.main()
