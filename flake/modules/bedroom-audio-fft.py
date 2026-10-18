"""Streams real-time audio FFT frequency spectrum from bedroom music tap to LED matrix."""

from __future__ import annotations

import socket
import struct
import sys
import numpy as np

TARGET_IP = "10.0.40.15"
TARGET_PORT = 4048
CHUNK_SAMPLES = 1024  # ~46ms at 22050Hz (~21.5 FPS)
CHUNK_BYTES = CHUNK_SAMPLES * 2


def main() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    fft_len = CHUNK_SAMPLES // 2 + 1
    bins = np.logspace(np.log10(2), np.log10(fft_len - 1), 17).astype(int)
    window = np.hanning(CHUNK_SAMPLES)
    prev_energy = 0.0

    while True:
        raw = sys.stdin.buffer.read(CHUNK_BYTES)
        if not raw or len(raw) < CHUNK_BYTES:
            break
        samples = np.frombuffer(raw, dtype=np.int16)
        float_samples = samples.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(float_samples**2)))

        fft = np.abs(np.fft.rfft(float_samples * window))
        bands: list[float] = []
        for i in range(16):
            b_val = float(np.mean(fft[bins[i] : max(bins[i] + 1, bins[i + 1])]))
            weight = 1.0 + (i / 16.0) * 2.0
            normalized = np.tanh(b_val * 4.0 * weight)
            bands.append(float(normalized))

        bass = (bands[0] + bands[1] + bands[2]) / 3.0
        energy_diff = max(0.0, bass - prev_energy)
        beat = min(1.0, energy_diff * 3.5 + (1.0 if bass > 0.8 else 0.0))
        prev_energy = bass * 0.85 + prev_energy * 0.15

        packet = b"FFT1" + struct.pack("!ff16f", rms, beat, *bands)
        try:
            sock.sendto(packet, (TARGET_IP, TARGET_PORT))
        except OSError:
            pass


if __name__ == "__main__":
    main()
