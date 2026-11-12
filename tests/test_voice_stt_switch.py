import unittest

from scripts.voice_stt_switch import ADDRESSES, BACKENDS, GATEWAY_FILE, preference_value


class PreferenceTest(unittest.TestCase):
    def test_preference_value_promotes_requested_backend(self) -> None:
        for backend in BACKENDS:
            parts = preference_value(backend).split(",")
            self.assertEqual(parts[0], f"{backend}={ADDRESSES[backend]}")
            self.assertEqual({part.split("=", 1)[0] for part in parts}, set(BACKENDS))

    def test_gateway_uses_only_nemotron(self) -> None:
        # Cloud fallback is deliberately removed.
        self.assertIn(
            f'value: "{preference_value("nemotron")}"', GATEWAY_FILE.read_text()
        )
        self.assertEqual(
            preference_value("nemotron"),
            "nemotron=wyoming-stt-nemotron:10300",
        )
