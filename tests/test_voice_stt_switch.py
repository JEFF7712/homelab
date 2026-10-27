import unittest

from scripts.voice_stt_switch import ADDRESSES, BACKENDS, GATEWAY_FILE, preference_value


class PreferenceTest(unittest.TestCase):
    def test_preference_value_promotes_requested_backend(self) -> None:
        for backend in BACKENDS:
            parts = preference_value(backend).split(",")
            self.assertEqual(parts[0], f"{backend}={ADDRESSES[backend]}")
            self.assertEqual({part.split("=", 1)[0] for part in parts}, set(BACKENDS))

    def test_gateway_prefers_nemotron_with_groq_as_its_only_fallback(self) -> None:
        # Local Whisper was removed; Groq is the sole fallback for Nemotron.
        self.assertIn(
            f'value: "{preference_value("nemotron")}"', GATEWAY_FILE.read_text()
        )
        self.assertEqual(
            preference_value("nemotron"),
            "nemotron=wyoming-stt-nemotron:10300,cloud=wyoming-stt-groq:10300",
        )
