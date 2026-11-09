#!/usr/bin/env python3
"""Direct low-latency LED matrix music streamer for laptop.

Streams real-time music reactive visualizer effects directly from your laptop's audio
monitor (Bose speaker or default sink) to the 64x64 LED matrix over Wi-Fi TCP (:9999)
or USB CDC. Also accepts LedFx DDP packets on UDP :4048 for seamless LedFx support.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any

import numpy as np

from modes.base import HAState, PlaybackState, RenderContext
from modes.ledfx_mode import AudioFrame, LedFxReceiver, LedFxRenderer
from modes.visualizer import VisualizerRenderer
from spotify_matrix_streamer import TCPMatrixStreamer, USBMatrixStreamer

CHUNK_SAMPLES = 1024
CHUNK_BYTES = CHUNK_SAMPLES * 2  # 16-bit PCM = 2 bytes/sample
SAMPLE_RATE = 22050


def find_default_monitor_sink() -> str:
    """Detect the best audio monitor source (preferring Bose SoundLink Flex if active)."""
    try:
        proc = subprocess.run(
            ["pactl", "list", "sources", "short"],
            capture_output=True,
            text=True,
            check=True,
        )
        lines = proc.stdout.strip().splitlines()
        bose_sink = None
        first_monitor = None
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                name = parts[1]
                if "bluez_output" in name and "monitor" in name:
                    bose_sink = name
                elif name.endswith(".monitor") and first_monitor is None:
                    first_monitor = name
        if bose_sink:
            return bose_sink
        if first_monitor:
            return first_monitor
    except Exception:
        pass
    return "@DEFAULT_MONITOR@"


def find_default_mic_source() -> str:
    """Detect the best microphone input source."""
    try:
        proc = subprocess.run(
            ["pactl", "list", "sources", "short"],
            capture_output=True,
            text=True,
            check=True,
        )
        lines = proc.stdout.strip().splitlines()
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                name = parts[1]
                if "Mic1" in name or (
                    "mic" in name.lower() and not name.endswith(".monitor")
                ):
                    return name
    except Exception:
        pass
    return "@DEFAULT_SOURCE@"


def find_speaker_monitor_sink() -> str:
    """Detect internal laptop speaker monitor source."""
    try:
        proc = subprocess.run(
            ["pactl", "list", "sources", "short"],
            capture_output=True,
            text=True,
            check=True,
        )
        lines = proc.stdout.strip().splitlines()
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                name = parts[1]
                if "Speaker" in name and name.endswith(".monitor"):
                    return name
    except Exception:
        pass
    return "@DEFAULT_MONITOR@"


def resolve_audio_device(choice: str | None, explicit_device: str | None = None) -> str:
    if explicit_device:
        return explicit_device
    choice = (choice or "music").lower()
    if choice in ("mic", "microphone"):
        return find_default_mic_source()
    if choice == "speaker":
        return find_speaker_monitor_sink()
    return find_default_monitor_sink()


def sync_ledfx_recording_source(target_source: str) -> None:
    """If LedFx is running, move its recording stream to target_source."""
    try:
        proc = subprocess.run(
            ["pactl", "list", "source-outputs"],
            capture_output=True,
            text=True,
            check=True,
        )
        blocks = proc.stdout.split("\n\n")
        for block in blocks:
            if "ledfx" in block or "ALSA plug-in" in block:
                for line in block.splitlines():
                    if "Source Output #" in line:
                        out_id = line.split("#")[1].strip()
                        subprocess.run(
                            ["pactl", "move-source-output", out_id, target_source],
                            capture_output=True,
                            check=False,
                        )
                        print(
                            f"[LedFx] Synchronized LedFx audio stream to: {target_source}"
                        )
                        return
    except Exception:
        pass


def get_ha_token(token_arg: str | None = None) -> str | None:
    if token_arg:
        return token_arg
    # Check keyring via secret-tool
    for cmd in [
        [
            "secret-tool",
            "lookup",
            "service",
            "hass",
            "origin",
            "https://ha.rupan.dev:443",
        ],
        ["secret-tool", "lookup", "service", "hass", "account", "token"],
    ]:
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            token = res.stdout.strip()
            if token:
                return token
        except Exception:
            pass
    # Check file
    token_file = os.path.expanduser("~/.config/spotify-matrix/ha-token")
    if os.path.isfile(token_file):
        try:
            with open(token_file) as f:
                return f.read().strip()
        except Exception:
            pass
    return None


def set_ha_mode(ha_url: str, token: str, mode: str) -> bool:
    """Update Home Assistant bedroom_matrix_mode."""
    url = f"{ha_url.rstrip('/')}/api/services/input_select/select_option"
    payload = json.dumps(
        {"entity_id": "input_select.bedroom_matrix_mode", "option": mode}
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return resp.status in (200, 201)
    except Exception as e:
        print(f"[HA] Warning: Failed to set mode to {mode}: {e}", file=sys.stderr)
        return False


class AudioTap:
    """Captures audio from PipeWire and computes FFT spectrum in a background thread."""

    def __init__(self, device: str) -> None:
        self.device = device
        self._latest_audio: AudioFrame | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[bytes] | None = None

        fft_len = CHUNK_SAMPLES // 2 + 1
        self.bins = np.logspace(np.log10(2), np.log10(fft_len - 1), 17).astype(int)
        self.window = np.hanning(CHUNK_SAMPLES)
        self.prev_energy = 0.0

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._capture_loop, daemon=True, name="audio-capture"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._process:
            try:
                self._process.terminate()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=1.0)

    def get_latest_audio(self, timeout: float = 2.0) -> AudioFrame | None:
        with self._lock:
            if self._latest_audio is not None and (
                time.monotonic() - self._latest_audio.timestamp < timeout
            ):
                return self._latest_audio
            return None

    def _capture_loop(self) -> None:
        cmd = [
            "parec",
            f"--rate={SAMPLE_RATE}",
            "--channels=1",
            "--format=s16le",
            f"--device={self.device}",
        ]
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=CHUNK_BYTES * 2,
            )
        except Exception as e:
            print(f"[AudioTap] Failed to start parec ({cmd}): {e}", file=sys.stderr)
            return

        stdout = self._process.stdout
        if stdout is None:
            return

        while not self._stop.is_set():
            try:
                raw = stdout.read(CHUNK_BYTES)
                if not raw or len(raw) < CHUNK_BYTES:
                    time.sleep(0.01)
                    continue

                samples = np.frombuffer(raw, dtype=np.int16)
                float_samples = samples.astype(np.float32) / 32768.0
                rms = float(np.sqrt(np.mean(float_samples**2)))

                fft = np.abs(np.fft.rfft(float_samples * self.window))
                bands: list[float] = []
                for i in range(16):
                    b_val = float(
                        np.mean(
                            fft[self.bins[i] : max(self.bins[i] + 1, self.bins[i + 1])]
                        )
                    )
                    weight = 1.0 + (i / 16.0) * 2.0
                    normalized = np.tanh(b_val * 4.0 * weight)
                    bands.append(float(normalized))

                bass = (bands[0] + bands[1] + bands[2]) / 3.0
                energy_diff = max(0.0, bass - self.prev_energy)
                beat = min(1.0, energy_diff * 3.5 + (1.0 if bass > 0.8 else 0.0))
                self.prev_energy = bass * 0.85 + self.prev_energy * 0.15

                with self._lock:
                    self._latest_audio = AudioFrame(
                        rms=rms,
                        beat=beat,
                        bands=bands,
                        timestamp=time.monotonic(),
                    )
            except Exception:
                break


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pico-ip",
        default="10.0.20.136",
        help="Pico W IP address (default: 10.0.20.136)",
    )
    parser.add_argument(
        "--port", type=int, default=9999, help="Pico W TCP port (default: 9999)"
    )
    parser.add_argument(
        "--transport", choices=["tcp", "usb"], default="tcp", help="Transport mode"
    )
    parser.add_argument(
        "--usb-port",
        default=None,
        help="Pico CDC2 data port (default: auto-detected /dev/ttyACM1)",
    )
    parser.add_argument(
        "--fps", type=float, default=20.0, help="Target frame rate (default: 20)"
    )
    parser.add_argument(
        "--brightness", type=float, default=0.7, help="Matrix brightness 0.0 - 1.0"
    )
    parser.add_argument(
        "--input",
        choices=["music", "mic", "bose", "speaker", "auto"],
        default="music",
        help="Audio input source: 'music' (Bose Bluetooth), 'mic' (laptop microphone), or 'speaker'",
    )
    parser.add_argument(
        "--device", default=None, help="Audio monitor source device name override"
    )
    parser.add_argument(
        "--ledfx-port",
        type=int,
        default=4048,
        help="UDP port for local LedFx DDP (default: 4048)",
    )
    parser.add_argument(
        "--ha-url", default="http://10.0.40.13:8123", help="Home Assistant URL"
    )
    parser.add_argument(
        "--ha-token", default=None, help="Home Assistant Long-Lived Access Token"
    )
    parser.add_argument(
        "--no-ha",
        action="store_true",
        help="Skip toggling Home Assistant input_select mode",
    )

    args = parser.parse_args()

    audio_device = resolve_audio_device(args.input, args.device)
    print(f"[AudioTap] Capturing audio from [{args.input}]: {audio_device}")
    sync_ledfx_recording_source(audio_device)

    tap = AudioTap(audio_device)
    tap.start()

    ledfx_receiver = LedFxReceiver(port=args.ledfx_port)
    ledfx_receiver.start()

    visualizer = VisualizerRenderer(audio_source=tap.get_latest_audio)
    renderer = LedFxRenderer(receiver=ledfx_receiver, fallback_renderer=visualizer)

    token = None if args.no_ha else get_ha_token(args.ha_token)
    if not args.no_ha and token:
        print("[HA] Switching bedroom_matrix_mode to 'Laptop'...")
        set_ha_mode(args.ha_url, token, "Laptop")
        # Give cluster pod a moment to release port 9999
        time.sleep(0.5)

    if args.transport == "usb":
        streamer: Any = USBMatrixStreamer(args.usb_port)
        print(f"[Streamer] Connected via USB CDC data ({args.usb_port or 'auto'})")
    else:
        streamer = TCPMatrixStreamer(args.pico_ip, args.port, max_in_flight=4)
        print(
            f"[Streamer] Connecting to Pico W via Wi-Fi TCP {args.pico_ip}:{args.port}..."
        )

    stop_event = threading.Event()

    def handle_signal(*_: Any) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print("=== Laptop LED Matrix Streamer Running (Ctrl+C to stop) ===")
    last_tick = time.monotonic()
    ha_dummy = HAState(power=True, mode="Laptop", brightness=args.brightness)
    pb_dummy = PlaybackState(available=True, playing=True)

    try:
        while not stop_event.is_set():
            tick = time.monotonic()
            dt = tick - last_tick

            ctx = RenderContext(
                tick=tick,
                dt=dt,
                ha_state=ha_dummy,
                spotify_state=pb_dummy,
                spotify_art=None,
                brightness=args.brightness,
                rotation_speed=45.0,
            )

            frame = renderer.render(ctx, size=64)
            streamer.send_frame(frame, brightness=args.brightness)

            last_tick = tick
            elapsed = time.monotonic() - tick
            sleep_time = max(0.0, (1.0 / args.fps) - elapsed)
            stop_event.wait(sleep_time)
    finally:
        print("\nShutting down laptop streamer...")
        tap.stop()
        ledfx_receiver.stop()
        streamer.close()
        if not args.no_ha and token:
            print("[HA] Reverting bedroom_matrix_mode to 'Auto'...")
            set_ha_mode(args.ha_url, token, "Auto")
        print("Done.")


if __name__ == "__main__":
    main()
