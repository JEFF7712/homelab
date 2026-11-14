"""MatrixPrism-inspired music reactive visualizer effects for 64x64 LED matrix.

Implements high-fidelity procedural patterns adapted from MatrixPrism:
1. Fibonacci128: Golden-angle phyllotaxis spiral with dual-arm wave propagation.
2. Mark Estes Plasma: Classic Table_Mark_Estes multi-harmonic trigonometric field.
3. AuroraDrop Mandala: Symmetrical kaleidoscopic radial mandala with aurora drops.
4. Spectrum Analyzer: Multi-band logarithmic equalizer with peak-hold & floor reflection.
5. Beat Warp: Concentric perspective warp tunnel with hyperspace starburst spokes.
"""

from __future__ import annotations

import math
import random
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, ClassVar

import numpy as np
from PIL import Image, ImageDraw

from modes.base import ModeRenderer, RenderContext


class VisualizerEffect(ABC):
    name: ClassVar[str] = "EFFECT"

    @abstractmethod
    def reset(self) -> None:
        """Reset the effect state."""
        ...

    @abstractmethod
    def update(self, dt: float, beat: float, bands: list[float] | None = None) -> None:
        """Update effect state given delta time, beat intensity (0.0 to 1.0), and optional frequency bands."""
        ...

    @abstractmethod
    def draw(
        self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64
    ) -> None:
        """Render the effect onto the 64x64 frame."""
        ...


class Fibonacci128Effect(VisualizerEffect):
    """Golden-angle phyllotaxis spiral with dual-arm wave propagation (MatrixPrism)."""

    name = "FIBONACCI128"
    GOLDEN_ANGLE: float = math.pi * (
        3.0 - math.sqrt(5.0)
    )  # ~2.399963 rad (~137.508 deg)
    NUM_POINTS: int = 128

    def __init__(self, size: int = 64) -> None:
        self.size = size
        self.center = (size / 2.0, size / 2.0)
        self.time = 0.0
        self.rotation = 0.0
        self.beat_energy = 0.0
        self.bass = 0.0
        self.treble = 0.0
        # Precompute normalized base radius and phyllotaxis angles
        self.angles = [i * self.GOLDEN_ANGLE for i in range(self.NUM_POINTS)]
        self.base_radii = [
            math.sqrt(i / max(1, self.NUM_POINTS - 1)) * ((size / 2.0) - 2.5)
            for i in range(self.NUM_POINTS)
        ]
        self.reset()

    def reset(self) -> None:
        self.time = 0.0
        self.rotation = 0.0
        self.beat_energy = 0.0
        self.bass = 0.0
        self.treble = 0.0

    def update(self, dt: float, beat: float, bands: list[float] | None = None) -> None:
        if bands and len(bands) > 0:
            self.bass = bands[0]
            mid_idx = min(len(bands) - 1, 4)
            treble_idx = min(len(bands) - 1, 10)
            self.treble = bands[treble_idx]
            energy = (
                (bands[0] * 0.5) + (bands[mid_idx] * 0.3) + (bands[treble_idx] * 0.2)
            )
        else:
            self.bass = beat
            self.treble = beat * 0.5
            energy = beat

        speed = 0.8 + 2.2 * energy
        self.time += dt * speed
        self.rotation += dt * (0.35 + 0.65 * self.bass)
        self.beat_energy += (energy - self.beat_energy) * min(1.0, dt * 10.0)

    def draw(
        self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64
    ) -> None:
        cx, cy = self.center
        draw.rectangle([(0, 0), (size - 1, size - 1)], fill=(2, 1, 6))

        # Fibonacci arms (e.g. 8 and 13)
        arm1, arm2 = 8, 13
        for i in range(self.NUM_POINTS):
            phase1 = (i % arm1) * (2.0 * math.pi / arm1)
            phase2 = (i % arm2) * (2.0 * math.pi / arm2)
            pos = i / self.NUM_POINTS

            wave1 = math.sin(pos * 6.0 - self.time * 3.5 + phase1)
            wave2 = math.sin(pos * 4.0 + self.time * 2.8 + phase2)

            r = self.base_radii[i] * (0.92 + 0.18 * self.beat_energy)
            if wave1 > 0.0:
                r += wave1 * (1.2 + 1.8 * self.bass)

            angle = self.angles[i] + self.rotation
            x = cx + math.cos(angle) * r
            y = cy + math.sin(angle) * r

            # Color along phyllotaxis spiral arms
            hue = (pos * 320.0 + self.time * 40.0 + wave2 * 30.0) % 360.0
            brightness = max(0.2, (wave1 * 0.5 + 0.5) * 0.8 + 0.3 * self.beat_energy)
            # HSV to RGB approximation
            h_norm = hue / 60.0
            c = brightness * 255.0
            x_val = c * (1.0 - abs((h_norm % 2.0) - 1.0))
            if 0 <= h_norm < 1:
                rgb = (int(c), int(x_val), 0)
            elif 1 <= h_norm < 2:
                rgb = (int(x_val), int(c), 0)
            elif 2 <= h_norm < 3:
                rgb = (0, int(c), int(x_val))
            elif 3 <= h_norm < 4:
                rgb = (0, int(x_val), int(c))
            elif 4 <= h_norm < 5:
                rgb = (int(x_val), 0, int(c))
            else:
                rgb = (int(c), 0, int(x_val))

            dot_r = 1.0 if pos < 0.75 else 1.2
            if self.beat_energy > 0.6 and i < 20:
                dot_r += 0.8
            draw.ellipse(
                [(x - dot_r, y - dot_r), (x + dot_r, y + dot_r)],
                fill=rgb,
            )


class MarkEstesPlasmaEffect(VisualizerEffect):
    """Table_Mark_Estes multi-harmonic trigonometric plasma field (MatrixPrism)."""

    name = "MARK ESTES PLASMA"

    def __init__(self, size: int = 64) -> None:
        self.size = size
        self.time = 0.0
        self.beat_pulse = 0.0
        self.drift_x = size / 2.0
        self.drift_y = size / 2.0
        # Precompute coordinate grids for vectorized plasma rendering
        y_coords, x_coords = np.mgrid[0:size, 0:size]
        self.grid_x = x_coords.astype(np.float32)
        self.grid_y = y_coords.astype(np.float32)
        self.reset()

    def reset(self) -> None:
        self.time = 0.0
        self.beat_pulse = 0.0
        self.drift_x = self.size / 2.0
        self.drift_y = self.size / 2.0

    def update(self, dt: float, beat: float, bands: list[float] | None = None) -> None:
        energy = bands[0] if (bands and len(bands) > 0) else beat
        self.time += dt * (1.2 + 2.4 * energy)
        self.drift_x = (self.size / 2.0) + 10.0 * math.sin(self.time * 0.45)
        self.drift_y = (self.size / 2.0) + 10.0 * math.cos(self.time * 0.6)
        self.beat_pulse += (beat - self.beat_pulse) * min(1.0, dt * 10.0)

    def draw(
        self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64
    ) -> None:
        dx = self.grid_x - self.drift_x
        dy = self.grid_y - self.drift_y
        dist = np.sqrt(dx * dx + dy * dy)

        # 4 harmonic trigonometric equations from Table_Mark_Estes
        p1 = np.sin(self.grid_x * 0.12 + self.time * 0.9)
        p2 = np.sin(self.grid_y * 0.14 - self.time * 1.1)
        p3 = np.sin((self.grid_x + self.grid_y) * 0.08 + self.time * 0.7)
        p4 = np.sin(dist * 0.22 - self.time * 1.6 + self.beat_pulse * 1.4)

        field = (p1 + p2 + p3 + p4) * 0.25  # range [-1.0, 1.0]

        # Dynamic Cyberpunk / Neon RGB palette mapping
        t = self.time * 0.35
        r_channel = 128.0 + 127.0 * np.sin(field * math.pi + t)
        g_channel = 128.0 + 127.0 * np.sin(field * math.pi + t + 2.094)
        b_channel = 128.0 + 127.0 * np.sin(field * math.pi + t + 4.188)

        # Modulate amplitude with music beat
        amp = 0.55 + 0.45 * self.beat_pulse
        r_u8 = np.clip(r_channel * amp, 0, 255).astype(np.uint8)
        g_u8 = np.clip(g_channel * amp, 0, 255).astype(np.uint8)
        b_u8 = np.clip(b_channel * amp, 0, 255).astype(np.uint8)

        rgb_arr = np.dstack((r_u8, g_u8, b_u8))
        plasma_img = Image.fromarray(rgb_arr, mode="RGB")
        frame.paste(plasma_img, (0, 0))


class AuroraDropMandalaEffect(VisualizerEffect):
    """Symmetrical kaleidoscopic radial mandala with falling aurora drops (MatrixPrism)."""

    name = "AURORADROP MANDALA"

    def __init__(self, size: int = 64) -> None:
        self.size = size
        self.center = (size / 2.0, size / 2.0)
        self.time = 0.0
        self.rotation = 0.0
        self.beat_energy = 0.0
        self.drops: list[list[float]] = []  # [x, y, vy, hue, life]
        self.reset()

    def reset(self) -> None:
        self.time = 0.0
        self.rotation = 0.0
        self.beat_energy = 0.0
        self.drops.clear()

    def update(self, dt: float, beat: float, bands: list[float] | None = None) -> None:
        self.time += dt * (1.0 + 1.5 * beat)
        self.rotation += dt * 0.6
        self.beat_energy += (beat - self.beat_energy) * min(1.0, dt * 10.0)

        # Spawn aurora drops on beat or rhythmic interval
        spawn_rate = 3 if beat > 0.65 else 1
        for _ in range(spawn_rate):
            if len(self.drops) < 32 and (random.random() < 0.4 or beat > 0.6):
                x = random.uniform(8.0, self.size - 8.0)
                y = random.uniform(0.0, 10.0)
                vy = random.uniform(15.0, 35.0) + (25.0 * beat)
                hue = (self.time * 60.0 + random.uniform(0, 120)) % 360.0
                self.drops.append([x, y, vy, hue, 1.0])

        active_drops: list[list[float]] = []
        for drop in self.drops:
            drop[1] += drop[2] * dt
            drop[4] -= dt * 0.85
            if drop[1] < self.size and drop[4] > 0.0:
                active_drops.append(drop)
        self.drops = active_drops

    def draw(
        self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64
    ) -> None:
        draw.rectangle([(0, 0), (size - 1, size - 1)], fill=(3, 1, 8))
        cx, cy = self.center

        # 1. Falling aurora drops with short neon tails
        for x, y, _, hue, life in self.drops:
            val = int(255 * max(0.0, min(1.0, life)))
            r = int(val * (0.5 + 0.5 * math.sin(hue * 0.0174)))
            g = int(val * (0.5 + 0.5 * math.sin((hue + 120.0) * 0.0174)))
            b = int(val * (0.5 + 0.5 * math.sin((hue + 240.0) * 0.0174)))
            draw.line([(x, y - 2), (x, y + 1)], fill=(r, g, b), width=1)

        # 2. 8-fold Symmetrical Mandala Petals & Nodes
        symmetry = 8
        step = (2.0 * math.pi) / symmetry
        core_r = 7.0 + 8.0 * self.beat_energy
        outer_r = 21.0 + 5.0 * math.sin(self.time * 2.0) + 7.0 * self.beat_energy

        pts_inner: list[tuple[float, float]] = []
        pts_outer: list[tuple[float, float]] = []

        for k in range(symmetry):
            ang = k * step + self.rotation
            ang_mid = ang + (step / 2.0)

            xi = cx + math.cos(ang) * core_r
            yi = cy + math.sin(ang) * core_r
            xo = cx + math.cos(ang_mid) * outer_r
            yo = cy + math.sin(ang_mid) * outer_r

            pts_inner.append((xi, yi))
            pts_outer.append((xo, yo))

        # Draw mandala arcs and radiating petals
        for k in range(symmetry):
            xi, yi = pts_inner[k]
            xo, yo = pts_outer[k]
            next_k = (k + 1) % symmetry
            xi_next, yi_next = pts_inner[next_k]

            col_inner = (
                int(220 * (0.5 + 0.5 * math.sin(self.time + k))),
                40,
                int(240 * (0.5 + 0.5 * math.cos(self.time * 0.7 + k))),
            )
            col_outer = (
                30,
                int(240 * (0.5 + 0.5 * math.sin(self.time * 1.2 + k))),
                int(255 * (0.5 + 0.5 * math.cos(self.time + k))),
            )

            draw.line([(xi, yi), (xo, yo)], fill=col_outer, width=1)
            draw.line([(xo, yo), (xi_next, yi_next)], fill=col_outer, width=1)
            draw.line([(xi, yi), (xi_next, yi_next)], fill=col_inner, width=1)
            # Glowing node dot
            draw.ellipse([(xo - 1, yo - 1), (xo + 1, yo + 1)], fill=(255, 230, 160))

        # Central pulsing core
        inner_pulse = 3.0 + 2.5 * math.sin(self.time * 4.0) + 3.0 * self.beat_energy
        draw.ellipse(
            [
                (cx - inner_pulse, cy - inner_pulse),
                (cx + inner_pulse, cy + inner_pulse),
            ],
            outline=(240, 200, 255),
        )


class SpectrumAnalyzerEffect(VisualizerEffect):
    """MatrixPrism multi-band log equalizer with peak-hold dots and floor reflection."""

    name = "SPECTRUM ANALYZER"

    def __init__(self, size: int = 64) -> None:
        self.size = size
        self.num_bars = 16
        self.bar_width = 3
        self.bar_gap = 1
        self.heights = [2.0] * self.num_bars
        self.peaks = [2.0] * self.num_bars
        self.peak_speeds = [0.0] * self.num_bars
        self.time = 0.0
        self.beat_energy = 0.0
        self.reset()

    def reset(self) -> None:
        self.time = 0.0
        self.beat_energy = 0.0
        self.heights = [2.0] * self.num_bars
        self.peaks = [2.0] * self.num_bars
        self.peak_speeds = [0.0] * self.num_bars

    def update(self, dt: float, beat: float, bands: list[float] | None = None) -> None:
        self.time += dt * 1.2
        self.beat_energy += (beat - self.beat_energy) * min(1.0, dt * 10.0)

        for i in range(self.num_bars):
            if bands and len(bands) > 0:
                idx = min(len(bands) - 1, int((i / self.num_bars) * len(bands)))
                raw_target = bands[idx] * (self.size - 18.0) * (0.8 + 0.4 * beat)
            else:
                # Procedural rhythmic bounce
                freq = 0.3 + (i * 0.15)
                raw_target = (
                    (
                        math.sin(self.time * 4.0 * freq) * 0.4
                        + math.cos(self.time * 2.5 + i * 0.5) * 0.3
                        + 0.5
                    )
                    * (self.size - 22.0)
                    * (0.4 + 0.6 * beat)
                )

            target = max(2.0, min(float(self.size - 14), raw_target))
            self.heights[i] += (target - self.heights[i]) * min(1.0, dt * 16.0)

            # Peak hold with gravity
            if self.heights[i] >= self.peaks[i]:
                self.peaks[i] = self.heights[i]
                self.peak_speeds[i] = 0.0
            else:
                self.peak_speeds[i] += dt * 38.0  # gravity
                self.peaks[i] = max(
                    self.heights[i], self.peaks[i] - self.peak_speeds[i] * dt
                )

    def draw(
        self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64
    ) -> None:
        draw.rectangle([(0, 0), (size - 1, size - 1)], fill=(2, 2, 8))
        baseline = size - 10

        # Sub-bass bottom floor glow
        floor_glow = int(80 * self.beat_energy)
        if floor_glow > 5:
            draw.rectangle(
                [(0, baseline), (size - 1, baseline + 2)],
                fill=(floor_glow, 0, floor_glow * 2),
            )

        for i in range(self.num_bars):
            x0 = i * (self.bar_width + self.bar_gap)
            x1 = x0 + self.bar_width - 1
            h = int(self.heights[i])
            y0 = baseline - h

            # Draw bar gradient slices
            for y in range(y0, baseline):
                frac = (baseline - y) / max(1.0, float(size - 14))
                # Gradient: Deep Blue -> Cyan -> Emerald -> Yellow -> Crimson
                if frac < 0.25:
                    r, g, b = 20, int(180 * (frac / 0.25)), 255
                elif frac < 0.55:
                    r, g, b = 20, 255, int(255 * (1.0 - (frac - 0.25) / 0.3))
                elif frac < 0.8:
                    r, g, b = int(255 * ((frac - 0.55) / 0.25)), 230, 20
                else:
                    r, g, b = 255, int(80 * (1.0 - (frac - 0.8) / 0.2)), 40
                draw.line([(x0, y), (x1, y)], fill=(r, g, b))

            # Draw peak-hold dot
            peak_y = int(baseline - self.peaks[i])
            if 0 <= peak_y < baseline:
                draw.line([(x0, peak_y), (x1, peak_y)], fill=(255, 255, 255))

            # Floor reflection (subtle mirror bounce)
            refl_h = min(6, int(h * 0.35))
            for ry in range(refl_h):
                alpha = (1.0 - (ry / 6.0)) * 0.3
                draw.line(
                    [(x0, baseline + 1 + ry), (x1, baseline + 1 + ry)],
                    fill=(int(40 * alpha), int(120 * alpha), int(220 * alpha)),
                )


class BeatWarpTunnelEffect(VisualizerEffect):
    """Perspective warp tunnel with expanding polygon rings and starburst spokes (MatrixPrism)."""

    name = "BEAT WARP"

    def __init__(self, size: int = 64) -> None:
        self.size = size
        self.center = (size / 2.0, size / 2.0)
        self.time = 0.0
        self.rotation = 0.0
        self.num_rings = 7
        self.ring_z = [(i / self.num_rings) for i in range(self.num_rings)]
        self.stars = [
            [
                random.uniform(-1.0, 1.0),
                random.uniform(-1.0, 1.0),
                random.uniform(0.1, 1.0),
            ]
            for _ in range(36)
        ]
        self.beat_warp = 0.0
        self.reset()

    def reset(self) -> None:
        self.time = 0.0
        self.rotation = 0.0
        self.beat_warp = 0.0
        self.ring_z = [(i / self.num_rings) for i in range(self.num_rings)]

    def update(self, dt: float, beat: float, bands: list[float] | None = None) -> None:
        energy = bands[0] if (bands and len(bands) > 0) else beat
        self.time += dt * (1.2 + 2.0 * energy)
        self.rotation += dt * (0.5 + 1.2 * energy)
        self.beat_warp += (energy - self.beat_warp) * min(1.0, dt * 10.0)

        # Advance rings toward camera
        warp_speed = 0.45 + 1.35 * self.beat_warp
        for k in range(self.num_rings):
            self.ring_z[k] += dt * warp_speed
            if self.ring_z[k] > 1.0:
                self.ring_z[k] -= 1.0

        # Advance starfield
        for star in self.stars:
            star[2] -= dt * (warp_speed * 0.8)
            if star[2] <= 0.05:
                star[0] = random.uniform(-1.0, 1.0)
                star[1] = random.uniform(-1.0, 1.0)
                star[2] = 1.0

    def draw(
        self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64
    ) -> None:
        draw.rectangle([(0, 0), (size - 1, size - 1)], fill=(3, 2, 7))
        cx, cy = self.center

        # 1. Warp starfield streaks
        for sx, sy, sz in self.stars:
            proj = (1.0 - sz) / max(0.05, sz)
            x = cx + sx * proj * 12.0
            y = cy + sy * proj * 12.0
            if 0 <= x < size and 0 <= y < size:
                bri = int(255 * (1.0 - sz))
                draw.point((x, y), fill=(bri, bri, min(255, bri + 40)))

        # 2. Concentric octagonal perspective rings
        sides = 8
        step = (2.0 * math.pi) / sides
        for z in sorted(self.ring_z):
            # Scale radius with perspective projection
            r = (z**1.7) * (size * 0.72)
            if r < 1.5:
                continue

            rot = self.rotation + (z * 0.8)
            poly_pts: list[tuple[float, float]] = []
            for s in range(sides):
                ang = s * step + rot
                poly_pts.append((cx + math.cos(ang) * r, cy + math.sin(ang) * r))

            # Chromatic neon coloring based on depth
            val = int(255 * z)
            col = (
                int(val * (0.6 + 0.4 * math.sin(self.time + z * 3.0))),
                int(val * 0.8),
                int(val * (0.6 + 0.4 * math.cos(self.time + z * 3.0))),
            )
            for k in range(sides):
                draw.line([poly_pts[k], poly_pts[(k + 1) % sides]], fill=col, width=1)

        # 3. Rotating radial spokes
        for s in range(sides):
            ang = s * step + self.rotation
            inner_x = cx + math.cos(ang) * 4.0
            inner_y = cy + math.sin(ang) * 4.0
            outer_x = cx + math.cos(ang) * (size * 0.65)
            outer_y = cy + math.sin(ang) * (size * 0.65)
            spoke_col = (
                int(120 * self.beat_warp),
                int(60 + 100 * self.beat_warp),
                int(200 + 55 * self.beat_warp),
            )
            draw.line([(inner_x, inner_y), (outer_x, outer_y)], fill=spoke_col, width=1)


class VisualizerRenderer(ModeRenderer):
    """Cycling music visualizer with MatrixPrism procedural animations."""

    def __init__(
        self,
        size: int = 64,
        cycle_interval: float = 60.0,
        effects: list[VisualizerEffect] | None = None,
        audio_source: Callable[[], Any] | None = None,
        randomize: bool = True,
    ) -> None:
        self.size = size
        self.cycle_interval = cycle_interval
        self.effects = effects or [
            Fibonacci128Effect(size),
            MarkEstesPlasmaEffect(size),
            AuroraDropMandalaEffect(size),
            SpectrumAnalyzerEffect(size),
            BeatWarpTunnelEffect(size),
        ]
        self.audio_source = audio_source
        self.randomize = randomize
        self.playlist: list[int] = list(range(len(self.effects)))
        self.playlist_pos = 0
        if self.randomize:
            random.shuffle(self.playlist)
        self.current_idx = self.playlist[0] if self.randomize else 0
        self.timer = 0.0
        self.beat_phase = 0.0

    @property
    def current_effect(self) -> VisualizerEffect:
        return self.effects[self.current_idx]

    @property
    def current_effect_name(self) -> str:
        return self.current_effect.name

    def reset(self) -> None:
        """Reset rotation order (called when switching into Visualizer mode)."""
        self.timer = 0.0
        if self.randomize:
            random.shuffle(self.playlist)
            self.playlist_pos = 0
            self.current_idx = self.playlist[0]
        else:
            self.playlist_pos = 0
            self.current_idx = 0
        self.current_effect.reset()

    def set_effect(self, index_or_name: int | str) -> None:
        if isinstance(index_or_name, int):
            self.current_idx = index_or_name % len(self.effects)
            self.timer = 0.0
            if self.randomize and self.current_idx in self.playlist:
                self.playlist_pos = self.playlist.index(self.current_idx)
            self.current_effect.reset()
        else:
            name_lower = index_or_name.lower()
            for idx, eff in enumerate(self.effects):
                if eff.name.lower() == name_lower:
                    self.current_idx = idx
                    self.timer = 0.0
                    if self.randomize and self.current_idx in self.playlist:
                        self.playlist_pos = self.playlist.index(self.current_idx)
                    eff.reset()
                    return
            raise ValueError(f"Unknown visualizer effect: {index_or_name}")

    def _next_effect(self) -> None:
        if self.randomize:
            self.playlist_pos += 1
            if self.playlist_pos >= len(self.playlist):
                last_idx = self.playlist[-1]
                self.playlist = list(range(len(self.effects)))
                random.shuffle(self.playlist)
                # Avoid playing the same effect consecutively across playlist loops
                if len(self.playlist) > 1 and self.playlist[0] == last_idx:
                    self.playlist[0], self.playlist[1] = (
                        self.playlist[1],
                        self.playlist[0],
                    )
                self.playlist_pos = 0
            self.current_idx = self.playlist[self.playlist_pos]
        else:
            self.current_idx = (self.current_idx + 1) % len(self.effects)
        self.current_effect.reset()

    def update(
        self,
        dt: float,
        beat: float,
        bands: list[float] | None = None,
        should_cycle: bool = True,
    ) -> None:
        # Cycle effects on timer
        if should_cycle and self.cycle_interval > 0 and len(self.effects) > 1:
            self.timer += dt
            if self.timer >= self.cycle_interval:
                steps = int(self.timer // self.cycle_interval)
                self.timer %= self.cycle_interval
                for _ in range(steps):
                    self._next_effect()
        elif not should_cycle:
            self.timer = 0.0

        self.current_effect.update(dt, beat, bands=bands)

    def render(self, ctx: RenderContext, size: int = 64) -> Image.Image:
        audio_frame = self.audio_source() if self.audio_source else None
        if audio_frame is not None:
            beat = audio_frame.beat
            bands = audio_frame.bands
        else:
            # Standalone rhythmic audio pulse generator
            if ctx.spotify_state.available and ctx.spotify_state.playing:
                bpm = 124.0
                bps = bpm / 60.0
                self.beat_phase += ctx.dt * bps * 2.0 * math.pi
            else:
                self.beat_phase += ctx.dt * 2.0 * 2.0 * math.pi

            # Sharp beat curve with exponential decay
            raw_sin = math.sin(self.beat_phase)
            beat = max(0.0, raw_sin) ** 3.5
            bands = None

        should_cycle = (
            ctx.ha_state.cycle
            if ctx is not None and hasattr(ctx, "ha_state") and ctx.ha_state is not None
            else True
        )
        self.update(ctx.dt, beat, bands=bands, should_cycle=should_cycle)

        frame = Image.new("RGB", (size, size), (2, 2, 6))
        draw = ImageDraw.Draw(frame)
        self.current_effect.draw(frame, draw, size=size)
        return frame
