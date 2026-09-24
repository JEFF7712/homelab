import json
import tempfile
import unittest
import wave
from pathlib import Path

from scripts.jarvis_acoustic_eval import (
    ALLOWED_CATEGORIES,
    load_manifest,
    normalize,
    validate_manifest,
    validate_wav,
    word_error_rate,
)


def make_wav(
    path: Path, *, rate: int = 16000, channels: int = 1, frames: int = 1600
) -> None:
    with wave.open(str(path), "wb") as dest:
        dest.setnchannels(channels)
        dest.setsampwidth(2)
        dest.setframerate(rate)
        dest.writeframes(b"\x00\x00" * frames * channels)


def write_manifest(directory: Path, cases: list[dict]) -> Path:
    manifest = directory / "manifest.json"
    manifest.write_text(json.dumps({"cases": cases}))
    return manifest


class AcousticScoringTest(unittest.TestCase):
    def test_normalize_ignores_case_and_punctuation(self) -> None:
        self.assertEqual(normalize("Turn on Govee!"), "turn on govee")

    def test_word_error_rate(self) -> None:
        self.assertEqual(word_error_rate("turn on lights", "turn on lights"), 0.0)
        self.assertAlmostEqual(word_error_rate("turn on lights", "turn lights"), 1 / 3)


class AcousticManifestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _valid_cases(self, count: int = 10) -> list[dict]:
        categories = sorted(ALLOWED_CATEGORIES)
        cases = []
        for index in range(count):
            name = f"case-{index:02d}.wav"
            make_wav(self.directory / name)
            cases.append(
                {
                    "id": f"case-{index:02d}",
                    "recording": name,
                    "expected": "turn on the kitchen lights",
                    "category": categories[index % len(categories)],
                }
            )
        return cases

    def test_valid_manifest_passes(self) -> None:
        manifest = write_manifest(self.directory, self._valid_cases())
        self.assertEqual(validate_manifest(manifest), [])
        self.assertEqual(len(load_manifest(manifest)), 10)

    def test_corpus_size_is_bounded(self) -> None:
        manifest = write_manifest(self.directory, self._valid_cases(3))
        errors = validate_manifest(manifest)
        self.assertTrue(any("10 to 20" in error for error in errors))
        with self.assertRaises(ValueError):
            load_manifest(manifest)

    def test_duplicate_ids_are_rejected(self) -> None:
        cases = self._valid_cases()
        cases[1]["id"] = cases[0]["id"]
        errors = validate_manifest(write_manifest(self.directory, cases))
        self.assertTrue(any("duplicate id" in error for error in errors))

    def test_unknown_category_is_rejected(self) -> None:
        cases = self._valid_cases()
        cases[0]["category"] = "synthesized"
        errors = validate_manifest(write_manifest(self.directory, cases))
        self.assertTrue(any("unknown category" in error for error in errors))

    def test_missing_recording_is_rejected(self) -> None:
        cases = self._valid_cases()
        cases[0]["recording"] = "absent.wav"
        errors = validate_manifest(write_manifest(self.directory, cases))
        self.assertTrue(any("missing recording" in error for error in errors))

    def test_empty_expected_transcript_is_rejected(self) -> None:
        cases = self._valid_cases()
        cases[0]["expected"] = "   "
        errors = validate_manifest(write_manifest(self.directory, cases))
        self.assertTrue(any("non-empty expected" in error for error in errors))

    def test_wrong_sample_rate_is_rejected(self) -> None:
        make_wav(self.directory / "slow.wav", rate=8000)
        error = validate_wav(self.directory / "slow.wav")
        assert error is not None
        self.assertIn("16000 Hz", error)

    def test_stereo_is_rejected(self) -> None:
        make_wav(self.directory / "stereo.wav", channels=2)
        error = validate_wav(self.directory / "stereo.wav")
        assert error is not None
        self.assertIn("mono", error)

    def test_unreadable_file_is_rejected_not_fatal(self) -> None:
        bad = self.directory / "bad.wav"
        bad.write_bytes(b"not a wav file")
        error = validate_wav(bad)
        assert error is not None
        self.assertIn("not a readable WAV", error)

    def test_missing_manifest_reports_error(self) -> None:
        errors = validate_manifest(self.directory / "absent.json")
        self.assertEqual(len(errors), 1)
        self.assertIn("cannot read manifest", errors[0])
