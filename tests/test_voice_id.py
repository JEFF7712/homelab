"""Unit tests for Jarvis Voice-ID classifier and proxy logic."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

try:
    import numpy as np

    from scripts.voice_id.proxy import SessionState, VoiceIdClassifier, pad_or_tile

    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False


@unittest.skipUnless(
    HAS_DEPS, "numpy/sherpa_onnx/wyoming not in host python env; validated in container"
)
class TestVoiceIdPadding(unittest.TestCase):
    def test_pad_or_tile_short(self) -> None:
        samples = np.ones(8000, dtype=np.float32)  # 0.5s at 16kHz
        tiled = pad_or_tile(samples, target_sec=1.0, rate=16000)
        self.assertEqual(len(tiled), 16000)
        self.assertTrue(np.all(tiled == 1.0))

    def test_pad_or_tile_long(self) -> None:
        samples = np.ones(32000, dtype=np.float32)  # 2.0s at 16kHz
        tiled = pad_or_tile(samples, target_sec=1.0, rate=16000)
        self.assertEqual(len(tiled), 32000)


@unittest.skipUnless(
    HAS_DEPS, "numpy/sherpa_onnx/wyoming not in host python env; validated in container"
)
class TestVoiceIdClassifier(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.profiles_path = Path(self.temp_dir.name) / "profiles.json"
        self.model_path = Path(self.temp_dir.name) / "model.onnx"
        self.model_path.touch()

        # Enrolled profiles: Rupan along X axis, Sam along Y axis
        self.dim = 4
        profiles_data = {
            "version": 1,
            "embedding_dim": self.dim,
            "threshold": 0.40,
            "min_margin": 0.10,
            "speakers": {
                "Rupan": [1.0, 0.0, 0.0, 0.0],
                "Sam": [0.0, 1.0, 0.0, 0.0],
            },
        }
        with self.profiles_path.open("w", encoding="utf-8") as f:
            json.dump(profiles_data, f)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @patch("sherpa_onnx.SpeakerEmbeddingExtractor")
    def test_profile_loading(self, mock_extractor_cls: MagicMock) -> None:
        classifier = VoiceIdClassifier(
            model_path=self.model_path,
            profiles_path=self.profiles_path,
        )
        self.assertIn("Rupan", classifier.profiles)
        self.assertIn("Sam", classifier.profiles)
        self.assertEqual(classifier.threshold, 0.40)
        self.assertEqual(classifier.min_margin, 0.10)

    @patch("sherpa_onnx.SpeakerEmbeddingExtractor")
    def test_identify_rupan(self, mock_extractor_cls: MagicMock) -> None:
        mock_extractor = MagicMock()
        # Mock extractor returns vector aligned with Rupan: [0.95, 0.05, 0.0, 0.0]
        mock_extractor.compute.return_value = [0.95, 0.05, 0.0, 0.0]
        mock_extractor_cls.return_value = mock_extractor

        classifier = VoiceIdClassifier(
            model_path=self.model_path,
            profiles_path=self.profiles_path,
        )

        pcm_audio = (np.ones(16000, dtype=np.int16) * 4000).tobytes()
        speaker, scores = classifier.identify_pcm(pcm_audio, rate=16000)

        self.assertEqual(speaker, "Rupan")
        self.assertGreater(scores["Rupan"], scores["Sam"])

    @patch("sherpa_onnx.SpeakerEmbeddingExtractor")
    def test_identify_sam(self, mock_extractor_cls: MagicMock) -> None:
        mock_extractor = MagicMock()
        # Mock extractor returns vector aligned with Sam: [0.05, 0.95, 0.0, 0.0]
        mock_extractor.compute.return_value = [0.05, 0.95, 0.0, 0.0]
        mock_extractor_cls.return_value = mock_extractor

        classifier = VoiceIdClassifier(
            model_path=self.model_path,
            profiles_path=self.profiles_path,
        )

        pcm_audio = (np.ones(16000, dtype=np.int16) * 4000).tobytes()
        speaker, scores = classifier.identify_pcm(pcm_audio, rate=16000)

        self.assertEqual(speaker, "Sam")
        self.assertGreater(scores["Sam"], scores["Rupan"])

    @patch("sherpa_onnx.SpeakerEmbeddingExtractor")
    def test_ambiguous_or_guest(self, mock_extractor_cls: MagicMock) -> None:
        mock_extractor = MagicMock()
        # Mock extractor returns vector along Z axis (unrecognized guest): [0.0, 0.0, 1.0, 0.0]
        mock_extractor.compute.return_value = [0.0, 0.0, 1.0, 0.0]
        mock_extractor_cls.return_value = mock_extractor

        classifier = VoiceIdClassifier(
            model_path=self.model_path,
            profiles_path=self.profiles_path,
        )

        pcm_audio = (np.ones(16000, dtype=np.int16) * 4000).tobytes()
        speaker, scores = classifier.identify_pcm(pcm_audio, rate=16000)

        self.assertIsNone(speaker)

    @patch("sherpa_onnx.SpeakerEmbeddingExtractor")
    def test_too_short_or_silent(self, mock_extractor_cls: MagicMock) -> None:
        classifier = VoiceIdClassifier(
            model_path=self.model_path,
            profiles_path=self.profiles_path,
        )
        # Too short (< 0.6s)
        pcm_short = (np.ones(4000, dtype=np.int16) * 4000).tobytes()
        speaker, _ = classifier.identify_pcm(pcm_short, rate=16000)
        self.assertIsNone(speaker)

        # Silent audio (RMS < 0.003)
        pcm_silent = np.zeros(16000, dtype=np.int16).tobytes()
        speaker, _ = classifier.identify_pcm(pcm_silent, rate=16000)
        self.assertIsNone(speaker)


@unittest.skipUnless(
    HAS_DEPS, "numpy/sherpa_onnx/wyoming not in host python env; validated in container"
)
class TestSessionState(unittest.TestCase):
    def test_session_state_lifecycle(self) -> None:
        session = SessionState()
        self.assertTrue(session.recognition_done.is_set())
        self.assertIsNone(session.speaker)
        self.assertIsNone(session.identify_task)

        session.audio_buffer.extend(b"1234")
        session.speaker = "Rupan"
        session.recognition_done.clear()
        self.assertFalse(session.recognition_done.is_set())

        session.reset_audio()
        self.assertEqual(len(session.audio_buffer), 0)
        self.assertIsNone(session.speaker)
        self.assertTrue(session.recognition_done.is_set())
        self.assertIsNone(session.identify_task)

    def test_session_state_task_cancellation(self) -> None:
        import asyncio

        session = SessionState()

        async def run() -> None:
            async def slow():
                await asyncio.sleep(10)

            task = asyncio.create_task(slow())
            session.identify_task = task
            session.reset_audio()
            self.assertIsNone(session.identify_task)
            self.assertTrue(task.cancelled())

        asyncio.run(run())


class TestFormatTranscript(unittest.TestCase):
    def test_standard_home_commands_unaltered(self) -> None:
        from scripts.voice_id.proxy import format_transcript_with_speaker

        # Light commands must stay clean so HassTurnOn/HassTurnOff match locally
        self.assertEqual(
            format_transcript_with_speaker("Turn off the living room lights.", "Rupan"),
            "Turn off the living room lights.",
        )
        self.assertEqual(
            format_transcript_with_speaker("Turn on the kitchen lights.", "Sam"),
            "Turn on the kitchen lights.",
        )
        self.assertEqual(
            format_transcript_with_speaker(
                "Make the downstairs lights neon pink.", "Rupan"
            ),
            "Make the downstairs lights neon pink.",
        )
        self.assertEqual(
            format_transcript_with_speaker(
                "What is the temperature downstairs?", "Sam"
            ),
            "What is the temperature downstairs?",
        )

    def test_identity_queries_formatted(self) -> None:
        from scripts.voice_id.proxy import format_transcript_with_speaker

        self.assertEqual(
            format_transcript_with_speaker("Who am I?", "Rupan"),
            "speaker Rupan Who am I",
        )
        self.assertEqual(
            format_transcript_with_speaker("who is speaking", "Sam"),
            "speaker Sam who is speaking",
        )
        self.assertEqual(
            format_transcript_with_speaker("What is my name?", "Rupan"),
            "speaker Rupan What is my name",
        )

    def test_music_queries_formatted(self) -> None:
        from scripts.voice_id.proxy import format_transcript_with_speaker

        self.assertEqual(
            format_transcript_with_speaker("Play Drake.", "Rupan"),
            "speaker Rupan Play Drake",
        )
        self.assertEqual(
            format_transcript_with_speaker("Play some Coldplay.", "Sam"),
            "speaker Sam Play some Coldplay",
        )
        self.assertEqual(
            format_transcript_with_speaker("Put on some jazz.", "Rupan"),
            "speaker Rupan Put on some jazz",
        )

    def test_no_speaker_unaltered(self) -> None:
        from scripts.voice_id.proxy import format_transcript_with_speaker

        self.assertEqual(
            format_transcript_with_speaker("Play Drake.", None),
            "Play Drake.",
        )
        self.assertEqual(
            format_transcript_with_speaker("Who am I?", None),
            "Who am I?",
        )


class TestSpeakerTagContract(unittest.TestCase):
    """The proxy's emitted tag and the canonical prompt must agree.

    Runs without sherpa/wyoming deps: reads both sources as text. The live
    HA copy is UI-managed with no read API, so this pins the two repo sides
    and the runbook procedure covers pasting the prompt into HA.
    """

    def test_proxy_prefix_matches_canonical_prompt(self) -> None:
        root = Path(__file__).resolve().parent.parent
        proxy_src = (root / "scripts" / "voice_id" / "proxy.py").read_text(
            encoding="utf-8"
        )
        prompt = (
            root / "home-assistant" / "conversation" / "jarvis_prompt.md"
        ).read_text(encoding="utf-8")
        self.assertIn('return f"speaker {speaker} {body}"', proxy_src)
        self.assertIn("`speaker <Name>`", prompt)
        self.assertNotIn("[Speaker:", proxy_src)
        self.assertNotIn("[Speaker:", prompt)


if __name__ == "__main__":
    unittest.main()
