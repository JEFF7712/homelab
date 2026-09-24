"""Replay labeled QuadCast WAV recordings through Wyoming STT backends."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "tests/acoustic/manifest.json"

# Closed category vocabulary for the acoustic corpus. Every case must carry
# one of these so replays stay comparable across backends.
ALLOWED_CATEGORIES = frozenset(
    {
        "quiet",
        "near",
        "far",
        "noisy",
        "music",
        "tv",
        "chime",
        "echo",
        "clipped",
        "speaker-known",
        "speaker-unknown",
        "silence",
    }
)
REQUIRED_RATE_HZ = 16000
MIN_CASES = 10
MAX_CASES = 20


def normalize(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def word_error_rate(expected: str, observed: str) -> float:
    left, right = normalize(expected).split(), normalize(observed).split()
    row = list(range(len(right) + 1))
    for i, expected_word in enumerate(left, 1):
        next_row = [i]
        for j, observed_word in enumerate(right, 1):
            next_row.append(
                min(
                    next_row[-1] + 1,
                    row[j] + 1,
                    row[j - 1] + (expected_word != observed_word),
                )
            )
        row = next_row
    return row[-1] / max(1, len(left))


def validate_wav(path: Path) -> str | None:
    """Return an error string unless path is 16 kHz mono PCM16 WAV."""
    try:
        with wave.open(str(path), "rb") as source:
            if source.getcomptype() != "NONE":
                return f"{path.name} must be uncompressed PCM WAV"
            if source.getframerate() != REQUIRED_RATE_HZ:
                return (
                    f"{path.name} must be {REQUIRED_RATE_HZ} Hz, "
                    f"got {source.getframerate()} Hz"
                )
            if source.getnchannels() != 1:
                return f"{path.name} must be mono, got {source.getnchannels()} channels"
            if source.getsampwidth() != 2:
                return f"{path.name} must be 16-bit PCM"
            if source.getnframes() == 0:
                return f"{path.name} contains no audio frames"
    except (wave.Error, EOFError, OSError) as exc:
        return f"{path.name} is not a readable WAV file ({exc})"
    return None


def validate_manifest(path: Path) -> list[str]:
    """Check the manifest schema and recording files; return error strings."""
    errors: list[str] = []
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot read manifest {path}: {exc}"]
    cases = payload.get("cases")
    if not isinstance(cases, list):
        return ["acoustic manifest needs a 'cases' list"]
    if not MIN_CASES <= len(cases) <= MAX_CASES:
        errors.append(
            f"acoustic corpus must contain {MIN_CASES} to {MAX_CASES} labeled "
            f"cases, got {len(cases)}"
        )
    seen: set[str] = set()
    for index, case in enumerate(cases):
        label = f"case {index}"
        if not isinstance(case, dict):
            errors.append(f"{label} must be an object")
            continue
        required = {"id", "recording", "expected", "category"}
        if not required <= case.keys():
            errors.append(
                f"{label} needs id, recording, expected, category; "
                f"got {sorted(case.keys())}"
            )
            continue
        label = f"case {case['id']!r}"
        if case["id"] in seen:
            errors.append(f"{label} has a duplicate id")
        seen.add(case["id"])
        if case["category"] not in ALLOWED_CATEGORIES:
            errors.append(
                f"{label} has unknown category {case['category']!r}; "
                f"allowed: {sorted(ALLOWED_CATEGORIES)}"
            )
        if not isinstance(case["expected"], str) or not case["expected"].strip():
            errors.append(f"{label} needs a non-empty expected transcript")
        recording = path.parent / case["recording"]
        if not recording.is_file():
            errors.append(f"{label} is missing recording: {recording}")
        else:
            wav_error = validate_wav(recording)
            if wav_error is not None:
                errors.append(f"{label}: {wav_error}")
    return errors


def load_manifest(path: Path) -> list[dict]:
    errors = validate_manifest(path)
    if errors:
        raise ValueError("; ".join(errors))
    payload = json.loads(path.read_text())
    return payload["cases"]


async def transcribe(host: str, port: int, recording: Path) -> str:
    from gitops.voice.gateway.gateway import encode_event, read_event

    with wave.open(str(recording), "rb") as source:
        if source.getcomptype() != "NONE":
            raise ValueError(f"{recording} must be uncompressed PCM WAV")
        fmt = {
            "rate": source.getframerate(),
            "width": source.getsampwidth(),
            "channels": source.getnchannels(),
        }
        pcm = source.readframes(source.getnframes())
    reader, writer = await asyncio.open_connection(host, port)
    writer.write(encode_event({"type": "transcribe", "data": {"language": "en"}}))
    writer.write(encode_event({"type": "audio-start", "data": fmt}))
    chunk_bytes = fmt["rate"] * fmt["width"] * fmt["channels"] // 10
    for offset in range(0, len(pcm), chunk_bytes):
        writer.write(
            encode_event(
                {
                    "type": "audio-chunk",
                    "data": fmt,
                    "payload": pcm[offset : offset + chunk_bytes],
                }
            )
        )
    writer.write(encode_event({"type": "audio-stop", "data": {}}))
    await writer.drain()
    while (event := await asyncio.wait_for(read_event(reader), 30)) is not None:
        if event["type"] == "transcript":
            writer.close()
            await writer.wait_closed()
            return str(event["data"].get("text", ""))
    return ""


async def run(args: argparse.Namespace) -> int:
    manifest = Path(args.manifest)
    cases = load_manifest(manifest)
    results = []
    for case in cases:
        observed = await transcribe(
            args.host, args.port, manifest.parent / case["recording"]
        )
        wer = word_error_rate(case["expected"], observed)
        results.append(
            {**case, "observed": observed, "wer": wer, "ok": wer <= args.max_wer}
        )
    print(json.dumps({"backend": args.backend, "results": results}, indent=2))
    return 0 if all(result["ok"] for result in results) else 1


def main(argv: list[str] | None = None) -> int:
    import sys

    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", required=False, default=None)
    parser.add_argument("--host", required=False, default=None)
    parser.add_argument("--port", type=int, default=10300)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--max-wer", type=float, default=0.15)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate the manifest and recording files without replaying audio",
    )
    args = parser.parse_args(argv)
    if args.validate_only:
        errors = validate_manifest(Path(args.manifest))
        print(json.dumps({"manifest": args.manifest, "errors": errors}, indent=2))
        return 0 if not errors else 2
    if not args.backend or not args.host:
        print("--backend and --host are required for replay", file=sys.stderr)
        return 2
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
