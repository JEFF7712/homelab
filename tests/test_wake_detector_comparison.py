"""Adapter tests use labeled doubles; acoustic measurements require real models and WAVs."""

from __future__ import annotations

import json
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.jarvis_wake_eval import (
    DetectorConfig,
    WakeDetectorHarness,
    evaluate,
    replay_wav,
)


class WakeDetectorComparisonHarnessTests(unittest.TestCase):
    def test_real_adapter_feature_and_noise_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_path = Path(tmp) / "model.json"
            model_path.write_text("{}")
            model = MagicMock()
            model.process_streaming_prob.side_effect = [None, 0.8]
            features = MagicMock()
            features.process_streaming.return_value = ["features-a", "features-b"]
            processor = MagicMock()
            processor.process.return_value = b"processed"
            micro = types.ModuleType("pymicro_wakeword")
            micro.MicroWakeWord = MagicMock()
            micro.MicroWakeWord.from_config.return_value = model
            micro.MicroWakeWordFeatures = MagicMock(return_value=features)
            webrtc = types.ModuleType("linux_voice_assistant.webrtc")
            webrtc.WebRTCProcessor = MagicMock(return_value=processor)
            with patch.dict(
                "sys.modules",
                {"pymicro_wakeword": micro, "linux_voice_assistant.webrtc": webrtc},
            ):
                harness = WakeDetectorHarness(
                    DetectorConfig("micro", model_path, 0.7, 3)
                )
                self.assertEqual(harness.process_chunk(b"pcm"), [0.8])
            micro.MicroWakeWord.from_config.assert_called_once_with(
                config_path=model_path
            )
            webrtc.WebRTCProcessor.assert_called_once_with(agc_level=0, ns_level=3)
            features.process_streaming.assert_called_once_with(b"processed")
            self.assertEqual(model.probability_cutoff, 0.7)

    def test_open_adapter_uses_embeddings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.tflite"
            path.touch()
            module = types.ModuleType("pyopen_wakeword")
            module.OpenWakeWord = MagicMock()
            module.OpenWakeWordFeatures = MagicMock()
            module.OpenWakeWordFeatures.from_builtin.return_value.process_streaming.return_value = [
                "embedding"
            ]
            module.OpenWakeWord.from_model.return_value.process_streaming.return_value = [
                0.2,
                0.9,
            ]
            with patch.dict("sys.modules", {"pyopen_wakeword": module}):
                harness = WakeDetectorHarness(DetectorConfig("open", path, 0.5, 0))
                self.assertEqual(harness.process_chunk(b"pcm"), [0.2, 0.9])
            module.OpenWakeWord.from_model.return_value.process_streaming.assert_called_once_with(
                "embedding"
            )

    def test_replay_reads_wav_and_resets_each_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with wave.open(str(root / "clip.wav"), "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                wav.writeframes(b"\x00\x00" * 320)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "cases": [
                            {"id": "a", "recording": "clip.wav", "wake_expected": True},
                            {
                                "id": "b",
                                "recording": "clip.wav",
                                "wake_expected": False,
                            },
                        ]
                    }
                )
            )
            with patch("scripts.jarvis_wake_eval.WakeDetectorHarness") as factory:
                factory.return_value.process_chunk.return_value = [0.7]
                results = evaluate(
                    manifest, DetectorConfig("micro", root / "model", 0.5, 0)
                )
                self.assertEqual(factory.call_count, 2)
                self.assertEqual([r["correct"] for r in results], [True, False])
                self.assertEqual(results[0]["score_count"], 2)
                self.assertEqual(results[0]["audio_seconds"], 0.02)
            with patch("scripts.jarvis_wake_eval.WakeDetectorHarness") as factory:
                factory.return_value.process_chunk.return_value = [0.7]
                result = replay_wav(
                    root / "clip.wav", DetectorConfig("micro", root / "model", 0.8, 0)
                )
                self.assertFalse(result["detected"])

    def test_missing_model_fails_explicitly(self):
        with self.assertRaises(FileNotFoundError):
            WakeDetectorHarness(DetectorConfig("micro", Path("/missing/model"), 0.5, 0))
