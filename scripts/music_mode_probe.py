"""Measure whether LedFx music mode is actually audio-reactive.

Sampling has to span two pods: the SPDIF monitor tap that LedFx analyzes only
exists on homelab-05, while source port 4002 is already held by LedFx itself so
only the Home Assistant pod can bind it for bulb status reads. The two pods
share a wall clock, so both record epoch timestamps and are joined here onto a
fixed grid.

The question this answers is not "do the colors change" but "does each bulb
track the audio band it is mapped to". LedFx maps the 20-15000 Hz virtual
across its four segments, so a working effect shows each bulb correlating with
its own band rather than with broadband level or with its neighbours.

Run from the repository root in the pinned dev environment:

    python -m scripts.music_mode_probe measure --duration 30
    python -m scripts.music_mode_probe audio --duration 30   # single pod
    python -m scripts.music_mode_probe color --duration 30   # single pod
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import subprocess
import sys
import tempfile
import time
import wave

GRID_MS = 100
SAMPLE_RATE = 44100
BULBS = {
    "floor-lamp": "10.0.20.166",
    "ceiling-light-1": "10.0.20.167",
    "ceiling-light-2": "10.0.20.168",
    "tulip-lamp": "10.0.20.169",
}
# music-mode-bulbs config: frequency_min 20, frequency_max 15000, mapping span.
FREQ_MIN = 20.0
FREQ_MAX = 15000.0
SEGMENT_NAMES = ["floor-lamp", "ceiling-light-1", "ceiling-light-2", "tulip-lamp"]
SPDIF_SOURCE = (
    "alsa_output.usb-Generic_USB_SPDIF_Adapter_202110200032-00.analog-stereo.monitor"
)
PULSE_SOCKET = "unix:/run/user/1001/pulse/native"
REPLY_PORT = 4002
CONTROL_PORT = 4003


def band_centres(count: int = 4) -> list[float]:
    """Log-spaced band centres matching LedFx's span mapping across segments."""
    ratio = FREQ_MAX / FREQ_MIN
    return [FREQ_MIN * ratio ** ((index + 0.5) / count) for index in range(count)]


def goertzel(samples: list[int], freq: float, rate: int = SAMPLE_RATE) -> float:
    """Magnitude of one frequency in a block, without numpy."""
    k = 2 * math.cos(2 * math.pi * freq / rate)
    s1 = 0.0
    s2 = 0.0
    for sample in samples:
        s0 = sample + k * s1 - s2
        s2 = s1
        s1 = s0
    return math.sqrt(s1 * s1 + s2 * s2 - k * s1 * s2) / max(1, len(samples))


def rms(samples: list[int]) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def pearson(pairs: list[tuple[float, float]]) -> float:
    """Pearson r, or 0.0 when either series is flat (no signal to correlate)."""
    if len(pairs) < 4:
        return 0.0
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in pairs)
    den = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    return num / den if den else 0.0


def read_wav_mono(path: str) -> tuple[list[int], int]:
    """Decode a 16-bit stereo WAV into interleaved mono samples."""
    with wave.open(path) as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    if width != 2:
        raise ValueError(f"expected 16-bit PCM, got {width * 8}-bit")
    interleaved = struct.unpack(f"<{len(raw) // 2}h", raw)
    if channels == 1:
        return list(interleaved), rate
    return [
        (interleaved[i] + interleaved[i + 1]) // 2
        for i in range(0, len(interleaved), 2)
    ], rate


def analyse_audio(path: str) -> list[dict]:
    """Per-grid-block broadband level and per-band energy, with epoch stamps."""
    samples, rate = read_wav_mono(path)
    block = rate * GRID_MS // 1000
    centres = band_centres(len(SEGMENT_NAMES))
    blocks = []
    for index, start in enumerate(range(0, len(samples) - block, block)):
        chunk = samples[start : start + block]
        blocks.append(
            {
                "index": index,
                "level": rms(chunk),
                "bands": [goertzel(chunk, freq, rate) for freq in centres],
            }
        )
    return blocks


# --- in-pod workers -------------------------------------------------------


def run_audio_worker(duration: int, pulse_socket: str, source: str) -> dict:
    """Record the SPDIF tap and return per-block audio features.

    Runs where the Pulse socket lives. parecord is given the whole window in
    one invocation: per-block invocations lose the header and return nothing.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/probe.wav"
        # Stamped before recording starts, not after it ends: the grid has to
        # be anchored to the first captured sample, and reading the clock after
        # a 30s capture shifts every block by the whole window, which silently
        # correlates the color series against nothing.
        wall_start = time.time()
        result = subprocess.run(
            [
                "timeout",
                str(duration + 2),
                "parecord",
                # Long-option values must use the = form here; the separated
                # form makes parecord treat the value as a stray positional and
                # fail with "Too many arguments".
                f"--device={source}",
                f"--rate={SAMPLE_RATE}",
                "--channels=2",
                "--format=s16le",
                "--file-format=wav",
                path,
            ],
            capture_output=True,
            check=False,
            env={
                "PULSE_SERVER": pulse_socket,
                "PATH": "/usr/bin:/bin",
                "HOME": "/tmp",
            },
        )
        if result.returncode not in (0, 124):
            return {"error": result.stderr.decode()[:400] or "parecord failed"}
        blocks = analyse_audio(path)
    return {
        "pod": "audio",
        "wall_start": wall_start,
        "blocks": blocks,
        "band_centres": band_centres(len(SEGMENT_NAMES)),
    }


def run_color_worker(
    duration: int, bulbs: dict[str, str], retries: int = 3, timeout: float = 0.25
) -> dict:
    """Poll each bulb's applied color and stamp it with the wall clock.

    Binds REPLY_PORT because the bulbs only answer a socket that uses the
    documented response port; an ephemeral port gets no reply at all.

    devStatus replies are lossy while LedFx is streaming colorwc at the same
    bulbs, so each bulb gets a bounded number of attempts per round. Polling
    is sequential, so a silent bulb must not stall the others for the whole
    window: the per-attempt timeout is what bounds that cost.
    """
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", REPLY_PORT))
    sock.settimeout(timeout)

    query = json.dumps({"msg": {"cmd": "devStatus", "data": {}}}).encode()
    samples: list[dict] = []
    dropped = {name: 0 for name in bulbs}
    attempts = {name: 0 for name in bulbs}
    start = time.time()

    while time.time() - start < duration:
        for name, ip in bulbs.items():
            for _ in range(retries):
                attempts[name] += 1
                sock.sendto(query, (ip, CONTROL_PORT))
                try:
                    payload = json.loads(sock.recvfrom(4096)[0].decode())
                    data = payload["msg"]["data"]
                except (TimeoutError, ValueError, KeyError, OSError):
                    continue
                if payload["msg"].get("cmd") != "devStatus":
                    continue
                color = data["color"]
                samples.append(
                    {
                        "bulb": name,
                        "at": time.time(),
                        "rgb": [color["r"], color["g"], color["b"]],
                        "brightness": data["brightness"],
                    }
                )
                break
            else:
                dropped[name] += 1

    sock.close()
    return {
        "pod": "color",
        "wall_start": start,
        "samples": samples,
        "dropped": dropped,
        "attempts": attempts,
    }


# --- orchestration --------------------------------------------------------


def kubectl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", *args], capture_output=True, text=True, check=False
    )


def kubectl_stream(*args: str) -> subprocess.Popen:
    return subprocess.Popen(["kubectl", *args])


def find_pod(namespace: str, selector: str) -> str | None:
    result = kubectl(
        "get",
        "pods",
        "-n",
        namespace,
        "-l",
        selector,
        "-o",
        "jsonpath={.items[0].metadata.name}",
    )
    name = result.stdout.strip()
    return name or None


def push_script(pod: str, namespace: str, source: str, dest: str) -> None:
    kubectl("cp", source, f"{namespace}/{pod}:{dest}")


def pull_json(pod: str, namespace: str, remote: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        local = f"{tmp}/out.json"
        kubectl("cp", f"{namespace}/{pod}:{remote}", local)
        with open(local) as handle:
            return json.load(handle)


def correlate(audio: dict, color: dict, lag_blocks: int = 4) -> dict:
    """Join both streams onto the grid and score each bulb against each band.

    The audio worker stamps wall_start when it begins recording, so grid index
    n sits at wall_start + n * GRID_MS / 1000. A positive lag means the bulb is
    compared against audio from that many blocks earlier.
    """
    blocks = audio.get("blocks", [])
    samples = color.get("samples", [])
    if not blocks or not samples:
        return {"error": "no audio blocks or no color samples"}

    levels = [b["level"] for b in blocks]
    audio_start = audio["wall_start"]

    per_bulb: dict[str, list[dict]] = {name: [] for name in SEGMENT_NAMES}
    for sample in samples:
        if sample["bulb"] in per_bulb:
            per_bulb[sample["bulb"]].append(sample)

    report: dict = {
        "grid_ms": GRID_MS,
        "audio_blocks": len(blocks),
        "band_centres_hz": [round(f, 1) for f in audio.get("band_centres", [])],
        "audio_peak_level": max(levels) if levels else 0.0,
        "audio_mean_level": (sum(levels) / len(levels)) if levels else 0.0,
        "dropped_reads": color.get("dropped", {}),
        "bulbs": {},
    }

    for index, name in enumerate(SEGMENT_NAMES):
        points = per_bulb[name]
        if not points:
            report["bulbs"][name] = {"error": "no reads"}
            continue
        luma = [(s["at"], sum(s["rgb"]) / 3.0) for s in points]
        distinct = {tuple(s["rgb"]) for s in points}
        brightness = [s["brightness"] for s in points]

        def grid_index(at: float, lag: int) -> int:
            return round((at - audio_start) * 1000.0 / GRID_MS - lag)

        best = {"lag": 0, "r": 0.0, "n": 0}
        for lag in range(lag_blocks + 1):
            pairs: list[tuple[float, float]] = []
            for at, value in luma:
                index_ = grid_index(at, lag)
                if 0 <= index_ < len(blocks):
                    pairs.append((blocks[index_]["level"], value))
            r = pearson(pairs)
            if abs(r) > abs(best["r"]):
                best = {"lag": lag, "r": r, "n": len(pairs)}

        # Score the bulb against every band to prove it tracks its own mapping.
        band_scores = []
        for band_index in range(len(SEGMENT_NAMES)):
            pairs_band = []
            for at, value in luma:
                index_ = grid_index(at, best["lag"])
                if 0 <= index_ < len(blocks):
                    pairs_band.append((blocks[index_]["bands"][band_index], value))
            band_scores.append(pearson(pairs_band))

        report["bulbs"][name] = {
            "reads": len(points),
            "distinct_colors": len(distinct),
            "luma_min": round(min(v for _, v in luma), 1),
            "luma_max": round(max(v for _, v in luma), 1),
            "brightness_min": min(brightness),
            "brightness_max": max(brightness),
            "best_lag_blocks": best["lag"],
            "best_lag_ms": best["lag"] * GRID_MS,
            "r_vs_level": round(best["r"], 3),
            "r_vs_band": [round(v, 3) for v in band_scores],
            "own_band_is_strongest": (
                band_scores.index(max(band_scores)) == index
                if band_scores and max(band_scores) > 0
                else None
            ),
        }
    return report


def render(report: dict) -> str:
    lines = []
    lines.append(
        f"audio blocks={report.get('audio_blocks')} "
        f"peak={report.get('audio_peak_level', 0):.0f} "
        f"mean={report.get('audio_mean_level', 0):.0f}"
    )
    centres = report.get("band_centres_hz", [])
    if centres:
        lines.append("band centres: " + ", ".join(f"{c:.0f}Hz" for c in centres))
    lines.append("")
    header = f"{'bulb':16s} {'reads':>5s} {'colors':>6s} {'luma':>13s} {'lag':>6s} {'r':>6s}  r_vs_bands"
    lines.append(header)
    for name in SEGMENT_NAMES:
        bulb = report.get("bulbs", {}).get(name, {})
        if "error" in bulb:
            lines.append(f"{name:16s} {bulb['error']}")
            continue
        bands = " ".join(f"{v:+.2f}" for v in bulb["r_vs_band"])
        lines.append(
            f"{name:16s} {bulb['reads']:5d} {bulb['distinct_colors']:6d} "
            f"{bulb['luma_min']:5.0f}-{bulb['luma_max']:<7.0f} "
            f"{bulb['best_lag_ms']:5d}ms {bulb['r_vs_level']:+6.2f}  {bands}"
        )
    verdict = interpret(report)
    lines.append("")
    lines.append(verdict)
    return "\n".join(lines)


def interpret(report: dict) -> str:
    if report.get("error"):
        return f"INCONCLUSIVE: {report['error']}"
    if report.get("audio_peak_level", 0) < 200:
        return (
            "NO AUDIO on the SPDIF tap. The effect correctly renders black, so "
            "there is nothing to correlate. Start playback and re-run."
        )
    active = [b for b in report["bulbs"].values() if "error" not in b]
    if not active:
        return "INCONCLUSIVE: no bulb reads at all."
    thin = [
        name
        for name, bulb in report["bulbs"].items()
        if "error" not in bulb and bulb["reads"] < 10
    ]
    tail = ""
    if thin:
        tail = (
            f"  THIN DATA on {', '.join(thin)}: too few reads to judge. "
            "Raise --duration or lower --retries to sample more."
        )
    changing = [b for b in active if b["distinct_colors"] > 1]
    correlated = [b for b in active if abs(b["r_vs_level"]) >= 0.3]
    own = [b for b in active if b["own_band_is_strongest"] is True]
    parts = [
        f"{len(changing)}/{len(active)} bulbs changing color",
        f"{len(correlated)}/{len(active)} correlating with level (|r|>=0.3)",
        f"{len(own)}/{len(active)} strongest on their own mapped band",
    ]
    if len(correlated) == len(active) and len(changing) == len(active):
        return "REACTIVE: " + "; ".join(parts) + tail
    if changing:
        return "CHANGING BUT NOT PROVABLY REACTIVE: " + "; ".join(parts) + tail
    return "STATIC despite audio: " + "; ".join(parts) + tail


def measure(args: argparse.Namespace) -> int:
    ledfx = find_pod("music-assistant", "app=ledfx")
    ha = find_pod("home-assistant", "app=home-assistant")
    if not ledfx or not ha:
        print(f"missing pod: ledfx={ledfx} home-assistant={ha}", file=sys.stderr)
        return 2
    print(f"audio pod: {ledfx}   color pod: {ha}")

    here = __file__
    push_script(ledfx, "music-assistant", here, "/tmp/music_mode_probe.py")
    push_script(ha, "home-assistant", here, "/tmp/music_mode_probe.py")

    audio_proc = kubectl_stream(
        "exec",
        "-n",
        "music-assistant",
        ledfx,
        "--",
        "python3",
        "/tmp/music_mode_probe.py",
        "audio",
        "--duration",
        str(args.duration),
    )
    color_proc = kubectl_stream(
        "exec",
        "-n",
        "home-assistant",
        ha,
        "--",
        "python3",
        "/tmp/music_mode_probe.py",
        "color",
        "--duration",
        str(args.duration),
    )
    for proc in (audio_proc, color_proc):
        proc.wait()

    audio = pull_json(ledfx, "music-assistant", "/tmp/audio_result.json")
    color = pull_json(ha, "home-assistant", "/tmp/color_result.json")
    report = correlate(audio, color)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render(report))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    measure_parser = sub.add_parser("measure", help="sample both pods and correlate")
    measure_parser.add_argument("--duration", type=int, default=30)
    measure_parser.add_argument("--json", action="store_true")
    measure_parser.set_defaults(func=measure)

    audio_parser = sub.add_parser("audio", help="run in the pod with the Pulse socket")
    audio_parser.add_argument("--duration", type=int, default=30)
    audio_parser.add_argument("--out", default="/tmp/audio_result.json")
    audio_parser.add_argument("--pulse-socket", default=PULSE_SOCKET)
    audio_parser.add_argument("--source", default=SPDIF_SOURCE)
    audio_parser.set_defaults(
        func=lambda a: _write(
            run_audio_worker(a.duration, a.pulse_socket, a.source), a.out
        )
    )

    color_parser = sub.add_parser("color", help="run in the pod that can bind 4002")
    color_parser.add_argument("--duration", type=int, default=30)
    color_parser.add_argument("--out", default="/tmp/color_result.json")
    color_parser.add_argument("--retries", type=int, default=3)
    color_parser.add_argument("--timeout", type=float, default=0.25)
    color_parser.set_defaults(
        func=lambda a: _write(
            run_color_worker(a.duration, BULBS, retries=a.retries, timeout=a.timeout),
            a.out,
        )
    )

    args = parser.parse_args(argv)
    return args.func(args) or 0


def _write(payload: dict, path: str) -> None:
    with open(path, "w") as handle:
        json.dump(payload, handle)
    print(f"wrote {path}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
