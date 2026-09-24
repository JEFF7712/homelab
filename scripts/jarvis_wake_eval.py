"""Replay recorded audio with the satellite's installed wake engines and WebRTC."""

from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DetectorConfig:
    engine: str
    model: Path
    threshold: float
    noise_suppression: int


class WakeDetectorHarness:
    def __init__(self, config: DetectorConfig):
        if not config.model.is_file():
            raise FileNotFoundError(config.model)
        if not 0 <= config.threshold <= 1 or not 0 <= config.noise_suppression <= 4:
            raise ValueError("Invalid threshold or noise suppression level")
        self.config = config
        self.processor = None
        if config.noise_suppression:
            from linux_voice_assistant.webrtc import WebRTCProcessor

            self.processor = WebRTCProcessor(
                agc_level=0, ns_level=config.noise_suppression
            )
        if config.engine == "micro":
            from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures

            with redirect_stdout(sys.stderr):
                self.model = MicroWakeWord.from_config(config_path=config.model)
            self.model.probability_cutoff = config.threshold
            self.features = MicroWakeWordFeatures()
        elif config.engine == "open":
            from pyopen_wakeword import OpenWakeWord, OpenWakeWordFeatures

            self.model = OpenWakeWord.from_model(model_path=config.model)
            self.features = OpenWakeWordFeatures.from_builtin()
        else:
            raise ValueError(f"Unknown engine: {config.engine}")

    def process_chunk(self, pcm: bytes) -> list[float]:
        if self.processor is not None:
            pcm = self.processor.process(pcm)
        scores = []
        for features in self.features.process_streaming(pcm):
            if self.config.engine == "micro":
                score = self.model.process_streaming_prob(features)
                if score is not None:
                    scores.append(float(score))
            else:
                scores.extend(
                    float(score) for score in self.model.process_streaming(features)
                )
        return scores


def replay_wav(path: Path, config: DetectorConfig) -> dict:
    with wave.open(str(path), "rb") as wav:
        if (
            wav.getframerate(),
            wav.getsampwidth(),
            wav.getnchannels(),
            wav.getcomptype(),
        ) != (16000, 2, 1, "NONE"):
            raise ValueError(f"{path}: expected uncompressed 16 kHz mono PCM16")
        if wav.getnframes() == 0:
            raise ValueError(f"{path}: empty recording")
        harness = WakeDetectorHarness(config)
        duration = wav.getnframes() / 16000
        scores = []
        started = time.perf_counter()
        while pcm := wav.readframes(160):
            scores.extend(harness.process_chunk(pcm))
        elapsed = time.perf_counter() - started
    if not scores:
        raise ValueError(f"{path}: recording produced no model scores")
    return {
        "detected": any(score > config.threshold for score in scores),
        "max_score": max(scores, default=None),
        "score_count": len(scores),
        "audio_seconds": duration,
        "processing_seconds": elapsed,
    }


def evaluate(manifest: Path, config: DetectorConfig) -> list[dict]:
    cases = json.loads(manifest.read_text())["cases"]
    if not cases:
        raise ValueError("No recorded cases")
    for case in cases:
        if type(case.get("wake_expected")) is not bool:
            raise ValueError(
                f"{case.get('id')}: wake_expected must be a labeled boolean"
            )
        if not (manifest.parent / case["recording"]).is_file():
            raise FileNotFoundError(manifest.parent / case["recording"])
    results = []
    for case in cases:
        result = replay_wav(manifest.parent / case["recording"], config)
        results.append(
            {
                "id": case["id"],
                "wake_expected": case["wake_expected"],
                "correct": result["detected"] == case["wake_expected"],
                **result,
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=Path("tests/acoustic/manifest.json")
    )
    parser.add_argument("--engine", choices=("micro", "open"), required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--noise-suppression", type=int, default=1)
    args = parser.parse_args()
    if not args.manifest.is_file():
        print(
            json.dumps(
                {
                    "status": "pending",
                    "reason": "Physical recordings and manifest required",
                }
            )
        )
        return 2
    config = DetectorConfig(
        args.engine, args.model, args.threshold, args.noise_suppression
    )
    results = evaluate(args.manifest, config)
    print(
        json.dumps(
            {
                "status": "measured",
                "engine": args.engine,
                "model": str(args.model),
                "threshold": args.threshold,
                "noise_suppression": args.noise_suppression,
                "cases": results,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
