from __future__ import annotations

import contextlib
import importlib.util
import io
import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE = REPO_ROOT / "scripts/music_mode_probe.py"


def load():
    spec = importlib.util.spec_from_file_location("music_mode_probe", MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = load()


def tone(freq: float, seconds: float, rate: int = probe.SAMPLE_RATE, amp: int = 8000):
    return [
        int(amp * math.sin(2 * math.pi * freq * n / rate))
        for n in range(int(rate * seconds))
    ]


def write_wav(path: Path, samples: list[int], rate: int = probe.SAMPLE_RATE) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(struct.pack(f"<{len(samples)}h", *samples))


class BandMappingTests(unittest.TestCase):
    def test_centres_are_log_spaced_inside_range(self) -> None:
        centres = probe.band_centres(4)
        self.assertEqual(len(centres), 4)
        self.assertTrue(all(probe.FREQ_MIN < c < probe.FREQ_MAX for c in centres))
        self.assertEqual(centres, sorted(centres))

    def test_centres_span_bass_to_treble(self) -> None:
        centres = probe.band_centres(4)
        self.assertLess(centres[0], 200)
        self.assertGreater(centres[-1], 2000)

    def test_band_count_follows_segment_count(self) -> None:
        self.assertEqual(len(probe.band_centres(len(probe.SEGMENT_NAMES))), 4)


class GoertzelTests(unittest.TestCase):
    def test_detects_energy_at_its_own_frequency(self) -> None:
        centre = probe.band_centres(4)
        for freq in centre:
            magnitude = probe.goertzel(tone(freq, 0.2), freq)
            self.assertGreater(magnitude, 100, f"{freq}Hz should register")

    def test_rejects_a_distant_frequency(self) -> None:
        low = probe.band_centres(4)[0]
        high = probe.band_centres(4)[3]
        at_low = probe.goertzel(tone(low, 0.2), low)
        at_high = probe.goertzel(tone(low, 0.2), high)
        self.assertGreater(at_low, at_high * 3)

    def test_silence_is_silent(self) -> None:
        self.assertEqual(probe.goertzel([0] * 4000, 440.0), 0.0)


class AudioAnalysisTests(unittest.TestCase):
    def test_silence_yields_flat_zero_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "silence.wav"
            write_wav(path, [0] * probe.SAMPLE_RATE)
            blocks = probe.analyse_audio(str(path))
        self.assertTrue(blocks)
        self.assertTrue(all(b["level"] == 0.0 for b in blocks))
        self.assertTrue(all(all(v == 0.0 for v in b["bands"]) for b in blocks))

    def test_block_count_follows_grid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "one.wav"
            write_wav(path, tone(probe.band_centres(4)[0], 1.0))
            blocks = probe.analyse_audio(str(path))
        expected = probe.SAMPLE_RATE * probe.GRID_MS // 1000
        self.assertAlmostEqual(len(blocks), probe.SAMPLE_RATE / expected, delta=2)

    def test_bass_tone_energises_the_low_band_only(self) -> None:
        low = probe.band_centres(4)[0]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bass.wav"
            write_wav(path, tone(low, 1.0))
            blocks = probe.analyse_audio(str(path))
        low_band = max(block["bands"][0] for block in blocks)
        higher = max(max(block["bands"][1:]) for block in blocks)
        self.assertGreater(low_band, 10, "bass must register in the low band")
        self.assertLess(higher, low_band / 5, "bass must not leak into higher bands")

    def test_stereo_is_mixed_to_mono(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stereo.wav"
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(2)
                handle.setsampwidth(2)
                handle.setframerate(probe.SAMPLE_RATE)
                frames = struct.pack(
                    f"<{2 * 100}h", *[v for _ in range(100) for v in (1000, -1000)]
                )
                handle.writeframes(frames)
            samples, _ = probe.read_wav_mono(str(path))
        self.assertEqual(len(samples), 100)
        self.assertTrue(all(abs(v) < 5 for v in samples), "opposite channels cancel")


class PearsonTests(unittest.TestCase):
    def test_perfect_positive(self) -> None:
        pairs = [(float(i), float(i) * 2) for i in range(10)]
        self.assertAlmostEqual(probe.pearson(pairs), 1.0, places=6)

    def test_perfect_negative(self) -> None:
        pairs = [(float(i), -float(i)) for i in range(10)]
        self.assertAlmostEqual(probe.pearson(pairs), -1.0, places=6)

    def test_flat_series_is_undefined_not_infinite(self) -> None:
        self.assertEqual(probe.pearson([(1.0, 5.0)] * 10), 0.0)
        self.assertEqual(probe.pearson([(float(i), 2.0) for i in range(10)]), 0.0)

    def test_too_few_points(self) -> None:
        self.assertEqual(probe.pearson([(1.0, 2.0), (2.0, 3.0)]), 0.0)

    def test_noise_is_near_zero(self) -> None:
        pairs = [(float(i), float((i * 37) % 11)) for i in range(40)]
        self.assertLess(abs(probe.pearson(pairs)), 0.5)


class AudioWorkerTests(unittest.TestCase):
    """The audio worker's clock anchor decides whether the join is valid."""

    def test_wall_start_is_stamped_before_recording(self) -> None:
        # Regression: anchoring the grid to the end of the capture shifts every
        # audio block by the whole window, so the color series lines up with
        # nothing and every correlation reads 0.00.
        import inspect

        source = inspect.getsource(probe.run_audio_worker)
        stamp = source.index("wall_start = time.time()")
        record = source.index('"timeout"')
        self.assertLess(stamp, record, "clock must be read before parecord runs")

    def test_result_reports_the_pre_recording_anchor(self) -> None:
        import inspect

        source = inspect.getsource(probe.run_audio_worker)
        self.assertIn('"wall_start": wall_start', source)
        self.assertNotIn('"wall_start": time.time()', source)

    def test_long_option_equals_form_is_required(self) -> None:
        # The separated form makes parecord abort with "Too many arguments",
        # which surfaced as an empty block list and a silent INCONCLUSIVE.
        import inspect

        source = inspect.getsource(probe.run_audio_worker)
        self.assertIn('f"--device={source}"', source)
        self.assertNotIn('"--device",', source)


class CorrelateTests(unittest.TestCase):
    """The join is the part that was wrong before, so it gets explicit tests."""

    def build(self, band_index=0, start=100.0, period=0.1):
        """A synthetic audio track and a floor-lamp series that tracks it.

        The lamp mirrors the sine in the mapped band, so a correct join and
        correlation must report a strong positive match at zero lag.
        """
        blocks = [
            {
                "index": i,
                "level": 1000.0 + 500 * math.sin(i / 3.0),
                "bands": [
                    (900.0 + 400 * math.sin(i / 3.0)) if b == band_index else 50.0
                    for b in range(4)
                ],
            }
            for i in range(60)
        ]
        samples = [
            {
                "bulb": "floor-lamp",
                "at": start + i * period,
                "rgb": [int(1000 + 500 * math.sin(i / 3.0))] * 3,
                "brightness": 100,
            }
            for i in range(60)
        ]
        return {
            "wall_start": start,
            "blocks": blocks,
            "band_centres": probe.band_centres(4),
        }, {
            "samples": samples,
            "dropped": {"floor-lamp": 0},
        }

    def test_perfectly_correlated_bulb_is_reactive(self) -> None:
        audio, color = self.build(band_index=0)
        report = probe.correlate(audio, color)
        bulb = report["bulbs"]["floor-lamp"]
        self.assertGreater(bulb["r_vs_level"], 0.9)
        self.assertGreater(bulb["distinct_colors"], 1)

    def test_own_band_is_identified_as_strongest(self) -> None:
        audio, color = self.build(band_index=0)
        report = probe.correlate(audio, color)
        self.assertIs(report["bulbs"]["floor-lamp"]["own_band_is_strongest"], True)

    def test_flat_bulb_is_not_reactive(self) -> None:
        audio, color = self.build(band_index=0)
        for sample in color["samples"]:
            sample["rgb"] = [7, 7, 7]
        report = probe.correlate(audio, color)
        self.assertEqual(report["bulbs"]["floor-lamp"]["distinct_colors"], 1)
        self.assertEqual(report["bulbs"]["floor-lamp"]["r_vs_level"], 0.0)

    def test_missing_bulb_is_reported_not_fatal(self) -> None:
        audio, color = self.build(band_index=0)
        report = probe.correlate(audio, color)
        self.assertIn("error", report["bulbs"]["tulip-lamp"])

    def test_empty_streams_are_inconclusive(self) -> None:
        self.assertIn("error", probe.correlate({"blocks": []}, {"samples": []}))

    def test_lag_is_reported_in_milliseconds(self) -> None:
        audio, color = self.build(band_index=0)
        # shift the bulb series two blocks later than the audio
        for sample in color["samples"]:
            sample["at"] += 2 * probe.GRID_MS / 1000.0
        report = probe.correlate(audio, color)
        self.assertEqual(report["bulbs"]["floor-lamp"]["best_lag_blocks"], 2)
        self.assertEqual(report["bulbs"]["floor-lamp"]["best_lag_ms"], 200)


class ThinDataTests(unittest.TestCase):
    def test_thin_reads_are_flagged_not_scored(self) -> None:
        bulb = {
            "reads": 3,
            "distinct_colors": 3,
            "r_vs_level": 0.0,
            "own_band_is_strongest": None,
        }
        report = {
            "audio_peak_level": 5000.0,
            "bulbs": {n: dict(bulb) for n in probe.SEGMENT_NAMES},
        }
        self.assertIn("THIN DATA", probe.interpret(report))

    def test_missing_bulb_is_not_counted_as_thin(self) -> None:
        healthy = {
            "reads": 40,
            "distinct_colors": 9,
            "r_vs_level": 0.7,
            "own_band_is_strongest": True,
        }
        report = {
            "audio_peak_level": 5000.0,
            "bulbs": {n: dict(healthy) for n in probe.SEGMENT_NAMES[:2]}
            | {n: {"error": "no reads"} for n in probe.SEGMENT_NAMES[2:]},
        }
        verdict = probe.interpret(report)
        self.assertNotIn("THIN DATA", verdict)
        self.assertIn("REACTIVE", verdict)


class VerdictTests(unittest.TestCase):
    def test_no_audio_is_called_out_first(self) -> None:
        report = {"audio_peak_level": 0.0, "bulbs": {}}
        self.assertIn("NO AUDIO", probe.interpret(report))

    def test_reactive_when_every_bulb_tracks(self) -> None:
        bulb = {
            "reads": 40,
            "distinct_colors": 9,
            "r_vs_level": 0.7,
            "own_band_is_strongest": True,
        }
        report = {
            "audio_peak_level": 5000.0,
            "bulbs": {n: dict(bulb) for n in probe.SEGMENT_NAMES},
        }
        self.assertIn("REACTIVE", probe.interpret(report))

    def test_static_despite_audio(self) -> None:
        bulb = {
            "reads": 40,
            "distinct_colors": 1,
            "r_vs_level": 0.0,
            "own_band_is_strongest": None,
        }
        report = {
            "audio_peak_level": 5000.0,
            "bulbs": {n: dict(bulb) for n in probe.SEGMENT_NAMES},
        }
        self.assertIn("STATIC", probe.interpret(report))

    def test_changing_but_unproven_is_distinguished(self) -> None:
        bulb = {
            "reads": 40,
            "distinct_colors": 9,
            "r_vs_level": 0.05,
            "own_band_is_strongest": None,
        }
        report = {
            "audio_peak_level": 5000.0,
            "bulbs": {n: dict(bulb) for n in probe.SEGMENT_NAMES},
        }
        self.assertIn("NOT PROVABLY REACTIVE", probe.interpret(report))


class CliTests(unittest.TestCase):
    def help_exits_cleanly(self, command: str) -> None:
        buffer = io.StringIO()
        with (
            contextlib.redirect_stdout(buffer),
            self.assertRaises(SystemExit) as exit_ctx,
        ):
            probe.main([command, "--help"])
        self.assertEqual(exit_ctx.exception.code, 0)
        self.assertIn("--duration", buffer.getvalue())

    def test_measure_audio_and_color_subcommands_exist(self) -> None:
        for command in ("measure", "audio", "color"):
            with self.subTest(command=command):
                self.help_exits_cleanly(command)

    def test_measure_defaults_to_a_long_enough_window(self) -> None:
        source = MODULE.read_text(encoding="utf-8")
        self.assertIn('"--duration", type=int, default=30', source)

    def test_module_docstring_documents_usage(self) -> None:
        self.assertIn("python -m scripts.music_mode_probe", probe.__doc__ or "")

    def test_probe_targets_the_four_bulbs(self) -> None:
        self.assertEqual(
            list(probe.BULBS.values()),
            ["10.0.20.166", "10.0.20.167", "10.0.20.168", "10.0.20.169"],
        )
        self.assertEqual(list(probe.BULBS), probe.SEGMENT_NAMES)


if __name__ == "__main__":
    unittest.main()
