"""Verification script for Nemotron model contract and native ABI symbols."""

from __future__ import annotations

import ctypes
import hashlib
import os
import sys
from pathlib import Path

REQUIRED_SYMBOLS = (
    "nemo_speech_asr_create",
    "nemo_speech_asr_destroy",
    "nemo_speech_asr_recognition_options_default",
    "nemo_speech_asr_streaming_recognize",
    "nemo_speech_asr_stream_push_f32",
    "nemo_speech_asr_stream_finish",
    "nemo_speech_asr_stream_next",
    "nemo_speech_asr_stream_close",
    "nemo_speech_asr_result_transcript",
    "nemo_speech_asr_result_is_final",
    "nemo_speech_asr_result_audio_processed",
    "nemo_speech_asr_result_destroy",
    "nemo_speech_asr_last_error",
)


def verify_library_symbols(lib_path: str | Path) -> bool:
    p = Path(lib_path)
    if not p.is_file():
        print(f"Library not found at {p}", file=sys.stderr)
        return False
    try:
        cdll = ctypes.CDLL(str(p))
    except Exception as exc:
        print(f"Failed to load shared library {p}: {exc}", file=sys.stderr)
        return False

    missing = [sym for sym in REQUIRED_SYMBOLS if not hasattr(cdll, sym)]
    if missing:
        print(f"Missing required symbols in {p}: {missing}", file=sys.stderr)
        return False
    print(f"All {len(REQUIRED_SYMBOLS)} symbols verified in {p}")
    return True


def verify_recognition_request(
    lib_path: str | Path, model_path: str | Path, manifest: Path
) -> bool:
    """Run the production recognizer against labeled recordings, never invented speech."""
    import json
    import re
    import wave

    from bridge import NativeConfig, NativeRecognizer

    cases = json.loads(manifest.read_text())["cases"]

    def normalize(value: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", value.lower()))

    expected = {normalize(case["expected"]) for case in cases}
    if "" not in expected or len(expected - {""}) < 2:
        raise ValueError(
            "Recognition gate needs silence and at least two distinct utterances"
        )
    config = NativeConfig()
    config.lib_path = str(lib_path)
    config.model_path = str(model_path)
    config.phrases = []
    config.validate()
    recognizer = NativeRecognizer(config)
    try:
        for case in cases:
            with wave.open(str(manifest.parent / case["recording"]), "rb") as wav:
                if (wav.getframerate(), wav.getsampwidth(), wav.getnchannels()) != (
                    16000,
                    2,
                    1,
                ):
                    raise ValueError("Recognition corpus must be 16 kHz mono PCM16")
                stream = recognizer.open_stream()
                try:
                    while pcm := wav.readframes(1600):
                        recognizer.push(stream, pcm, 16000)
                    observed = recognizer.finish(stream)
                finally:
                    recognizer.close_stream(stream)
            if normalize(observed) != normalize(case["expected"]):
                print(f"Recognition mismatch: {case['id']}", file=sys.stderr)
                return False
    finally:
        recognizer.destroy()
    return True


def verify_python_modules() -> bool:
    """Verifies that proxy.py and required voice-ID modules are accessible."""
    base_dir = Path(__file__).resolve().parent
    proxy_path = base_dir / "proxy.py"
    if not proxy_path.is_file():
        print(f"Required module proxy.py missing at {proxy_path}", file=sys.stderr)
        return False

    # Check that proxy.py exports the required interfaces
    import importlib.util

    spec = importlib.util.spec_from_file_location("proxy", str(proxy_path))
    if not spec or not spec.loader:
        print("Failed to load spec for proxy.py", file=sys.stderr)
        return False
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:
        print(f"Failed to import proxy.py: {exc}", file=sys.stderr)
        return False

    for attr in (
        "VoiceIdClassifier",
        "format_transcript_with_speaker",
        "is_garbage_transcript",
    ):
        if not hasattr(mod, attr):
            print(f"proxy.py missing required attribute {attr}", file=sys.stderr)
            return False

    print("Python voice-ID proxy module verified successfully")
    return True


def verify_model_file(
    model_path: str | Path, expected_sha256: str | None = None
) -> bool:
    p = Path(model_path)
    if not p.is_file():
        print(f"Model file not found at {p}", file=sys.stderr)
        return False
    size = p.stat().st_size
    if size < 100 * 1024 * 1024:  # At least 100 MB
        print(f"Model file {p} too small ({size} bytes)", file=sys.stderr)
        return False

    if expected_sha256:
        h = hashlib.sha256()
        with p.open("rb") as f:
            while chunk := f.read(1024 * 1024):
                h.update(chunk)
        digest = h.hexdigest()
        if digest != expected_sha256:
            print(
                f"Model hash mismatch: expected {expected_sha256}, got {digest}",
                file=sys.stderr,
            )
            return False
        print(f"Model SHA256 verified: {digest}")
    else:
        print(f"Model file verified ({size} bytes)")
    return True


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--abi-only", action="store_true")
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    lib_path = os.environ.get("NEMO_LIB", "/opt/nemo/lib64/libnemo_speech_asr_c.so")
    model_path = os.environ.get("NEMO_MODEL", "")
    if not (verify_python_modules() and verify_library_symbols(lib_path)):
        return 1
    if args.abi_only:
        print(
            "ABI/import checks passed. Recognition is UNVERIFIED without model and corpus."
        )
        return 0
    if not model_path or not verify_model_file(model_path):
        return 1
    if args.manifest is None:
        parser.error("--manifest is required for recognition verification")
    return 0 if verify_recognition_request(lib_path, model_path, args.manifest) else 1


if __name__ == "__main__":
    sys.exit(main())
