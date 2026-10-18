"""Procedural music reactive visualizer effects for 64x64 LED matrix."""

from __future__ import annotations

import math
import random
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, ClassVar

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
    def draw(self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64) -> None:
        """Render the effect onto the 64x64 frame."""
        ...


class RadialSpectrumEffect(VisualizerEffect):
    """Circular multi-band audio spectrum radiating from center."""

    name = "RADIAL SPECTRUM"

    def __init__(self, size: int = 64) -> None:
        self.size = size
        self.center = (size / 2.0, size / 2.0)
        self.rotation = 0.0
        self.bands = 24
        self.band_heights = [4.0] * self.bands
        self.ripples: list[list[float]] = []  # [radius, alpha, hue_offset]
        self.reset()

    def reset(self) -> None:
        self.rotation = 0.0
        self.band_heights = [6.0] * self.bands
        self.ripples.clear()

    def update(self, dt: float, beat: float, bands: list[float] | None = None) -> None:
        self.rotation += dt * 0.8
        for i in range(self.bands):
            if bands and len(bands) > 0:
                idx = min(len(bands) - 1, int(i / self.bands * len(bands)))
                b_val = bands[idx]
                target = 4.0 + 26.0 * b_val * (0.7 + 0.6 * beat)
            else:
                target = 5.0 + 16.0 * beat * (0.6 + 0.4 * math.sin(i * 1.2 + self.rotation * 3.0))
            self.band_heights[i] += (target - self.band_heights[i]) * min(1.0, dt * 14.0)

        # Spawn shockwave ripple on strong beat (or bass transient)
        bass = bands[0] if (bands and len(bands) > 0) else beat
        if beat > 0.65 or bass > 0.75:
            self.ripples.append([8.0, 1.0, self.rotation])

        # Advance ripples
        active_ripples = []
        for r in self.ripples:
            r[0] += dt * 38.0  # expand
            r[1] -= dt * 1.5   # fade
            if r[1] > 0 and r[0] < self.size * 0.75:
                active_ripples.append(r)
        self.ripples = active_ripples

    def draw(self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64) -> None:
        cx, cy = self.center
        draw.rectangle([(0, 0), (size - 1, size - 1)], fill=(4, 2, 8))

        # 1. Draw ripples
        for radius, alpha, hue_off in self.ripples:
            col_val = int(220 * max(0.0, alpha))
            r_col = int(col_val * (0.5 + 0.5 * math.sin(hue_off)))
            g_col = int(col_val * (0.5 + 0.5 * math.sin(hue_off + 2.0)))
            b_col = int(col_val * (0.5 + 0.5 * math.sin(hue_off + 4.0)))
            draw.ellipse(
                [(cx - radius, cy - radius), (cx + radius, cy + radius)],
                outline=(r_col, g_col, b_col),
            )

        # 2. Draw radiating radial EQ bars
        inner_r = 7.0
        angle_step = (2.0 * math.pi) / self.bands
        for i in range(self.bands):
            angle = i * angle_step + self.rotation
            h = self.band_heights[i]
            x1 = cx + math.cos(angle) * inner_r
            y1 = cy + math.sin(angle) * inner_r
            x2 = cx + math.cos(angle) * (inner_r + h)
            y2 = cy + math.sin(angle) * (inner_r + h)

            # Rainbow neon palette
            hue = (i / self.bands + self.rotation * 0.2) % 1.0
            r = int(128 + 127 * math.sin(hue * 2 * math.pi))
            g = int(128 + 127 * math.sin(hue * 2 * math.pi + 2.094))
            b = int(128 + 127 * math.sin(hue * 2 * math.pi + 4.188))
            draw.line([(x1, y1), (x2, y2)], fill=(r, g, b), width=2)

        # 3. Center pulsating core
        core_r = max(2.0, inner_r * 0.6 + math.sin(self.rotation * 4.0) * 1.5)
        draw.ellipse(
            [(cx - core_r, cy - core_r), (cx + core_r, cy + core_r)],
            fill=(255, 255, 255),
            outline=(180, 40, 240),
        )


class GeometricTunnelEffect(VisualizerEffect):
    """Concentric 3D wireframe polygons zooming toward camera."""

    name = "HYPERSPACE TUNNEL"

    def __init__(self, size: int = 64) -> None:
        self.size = size
        self.cx = size / 2.0
        self.cy = size / 2.0
        self.depth_offsets = [i * 0.16 for i in range(7)]
        self.angle = 0.0
        self.speed = 0.35

    def reset(self) -> None:
        self.depth_offsets = [i * 0.16 for i in range(7)]
        self.angle = 0.0

    def update(self, dt: float, beat: float, bands: list[float] | None = None) -> None:
        bass = (bands[0] + bands[1]) / 2.0 if (bands and len(bands) > 1) else beat
        # Speed surges with beat and bass intensity
        current_speed = self.speed * (1.0 + max(beat, bass) * 2.4)
        self.angle += dt * (0.6 + max(beat, bass) * 1.5)

        for i in range(len(self.depth_offsets)):
            self.depth_offsets[i] += dt * current_speed
            if self.depth_offsets[i] >= 1.0:
                self.depth_offsets[i] -= 1.0

    def draw(self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64) -> None:
        draw.rectangle([(0, 0), (size - 1, size - 1)], fill=(2, 3, 10))

        # Sort layers by depth (back to front)
        sorted_depths = sorted(self.depth_offsets)

        for depth in sorted_depths:
            if depth <= 0.04:
                continue
            scale = (depth ** 2.2) * (size * 0.72)
            rot = self.angle + (1.0 - depth) * 1.5

            # 4-point diamond / polygon
            pts = []
            for k in range(4):
                a = rot + (k * math.pi / 2.0)
                px = self.cx + math.cos(a) * scale
                py = self.cy + math.sin(a) * scale
                pts.append((px, py))

            # Color shifts from deep violet at distance to cyan/white close-up
            brightness = min(1.0, depth * 1.3)
            r = int((20 + 220 * (1.0 - depth)) * brightness)
            g = int((140 * depth + 115 * (1.0 - depth)) * brightness)
            b = int((255) * brightness)

            # Draw polygon edges
            for k in range(4):
                draw.line([pts[k], pts[(k + 1) % 4]], fill=(r, g, b), width=1)

        # Center vortex star
        draw.rectangle([(int(self.cx - 1), int(self.cy - 1)), (int(self.cx + 1), int(self.cy + 1))], fill=(0, 240, 255))


class ParticleFireworksEffect(VisualizerEffect):
    """High-energy particle burst fountain with phosphor decay trails."""

    name = "PARTICLE FOUNTAIN"

    def __init__(self, size: int = 64) -> None:
        self.size = size
        self.particles: list[list[float]] = []  # [x, y, vx, vy, life, max_life, r, g, b]
        self.spawn_timer = 0.0

    def reset(self) -> None:
        self.particles.clear()
        self.spawn_timer = 0.0

    def update(self, dt: float, beat: float, bands: list[float] | None = None) -> None:
        self.spawn_timer += dt
        bass = bands[0] if (bands and len(bands) > 0) else beat
        trigger_intensity = max(beat, bass)

        # Spawn burst on beat pulse or bass kick
        if trigger_intensity > 0.45 or self.spawn_timer > 0.25:
            burst_count = int(8 + 26 * trigger_intensity)
            cx, cy = self.size / 2.0, self.size / 2.0
            for _ in range(burst_count):
                angle = random.uniform(0, 2 * math.pi)
                speed = random.uniform(12.0, 52.0) * (0.8 + 1.3 * trigger_intensity)
                vx = math.cos(angle) * speed
                vy = math.sin(angle) * speed
                lifespan = random.uniform(0.6, 1.4)
                # Neon color palette (gold, magenta, electric cyan)
                color_type = random.randint(0, 2)
                if color_type == 0:
                    r, g, b = 255, random.randint(180, 240), 40
                elif color_type == 1:
                    r, g, b = 255, 30, random.randint(140, 220)
                else:
                    r, g, b = 40, random.randint(200, 255), 255
                self.particles.append([cx, cy, vx, vy, lifespan, lifespan, r, g, b])
            self.spawn_timer = 0.0

        # Update existing particles
        active = []
        for p in self.particles:
            p[0] += p[2] * dt
            p[1] += p[3] * dt
            # Subtle gravity and drag
            p[3] += 12.0 * dt
            p[2] *= 0.96
            p[3] *= 0.96
            p[4] -= dt
            if p[4] > 0 and 0 <= p[0] < self.size and 0 <= p[1] < self.size:
                active.append(p)
        self.particles = active

    def draw(self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64) -> None:
        draw.rectangle([(0, 0), (size - 1, size - 1)], fill=(3, 2, 7))

        for x, y, _, _, life, max_life, pr, pg, pb in self.particles:
            alpha = max(0.0, life / max_life)
            r = int(pr * alpha)
            g = int(pg * alpha)
            b = int(pb * alpha)
            ix, iy = int(x), int(y)
            if 0 <= ix < size and 0 <= iy < size:
                draw.point((ix, iy), fill=(r, g, b))
                # Soft glow around bright living particles
                if alpha > 0.7:
                    glow = (r // 3, g // 3, b // 3)
                    for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                        gx, gy = ix + dx, iy + dy
                        if 0 <= gx < size and 0 <= gy < size:
                            draw.point((gx, gy), fill=glow)


class GraphicEqualizerEffect(VisualizerEffect):
    """16-band vertical graphic equalizer with segmented LED blocks and falling peak dots."""

    name = "GRAPHIC EQUALIZER"

    def __init__(self, size: int = 64) -> None:
        self.size = size
        self.num_bars = 16
        self.bar_width = 3
        self.bar_gap = 1
        self.heights = [4.0] * self.num_bars
        self.peaks = [4.0] * self.num_bars
        self.peak_speeds = [0.0] * self.num_bars
        self.phase = 0.0

    def reset(self) -> None:
        self.heights = [4.0] * self.num_bars
        self.peaks = [4.0] * self.num_bars
        self.peak_speeds = [0.0] * self.num_bars
        self.phase = 0.0

    def update(self, dt: float, beat: float, bands: list[float] | None = None) -> None:
        self.phase += dt * 3.0
        max_h = 50.0

        for i in range(self.num_bars):
            if bands and len(bands) > 0:
                idx = min(len(bands) - 1, int(i / self.num_bars * len(bands)))
                target = 3.0 + max_h * bands[idx]
            else:
                # Frequency curve simulation: heavier bass on left, dynamic mids, sparkle highs
                freq_scale = 1.0 - (i / self.num_bars) * 0.45
                harmonic = math.sin(self.phase * 1.5 + i * 0.45) * 0.5 + 0.5
                target = 6.0 + max_h * freq_scale * (0.3 * harmonic + 0.7 * beat)

            # Smooth interpolation
            self.heights[i] += (target - self.heights[i]) * min(1.0, dt * 18.0)

            # Peak hold update
            if self.heights[i] >= self.peaks[i]:
                self.peaks[i] = self.heights[i]
                self.peak_speeds[i] = 0.0
            else:
                self.peak_speeds[i] += 48.0 * dt  # gravity
                self.peaks[i] = max(self.heights[i], self.peaks[i] - self.peak_speeds[i] * dt)

    def draw(self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64) -> None:
        draw.rectangle([(0, 0), (size - 1, size - 1)], fill=(2, 4, 8))
        base_y = 58

        for i in range(self.num_bars):
            x = i * (self.bar_width + self.bar_gap) + 1
            h = int(self.heights[i])
            peak_h = int(self.peaks[i])

            # Draw segmented LED blocks
            for seg in range(0, h, 2):
                sy = base_y - seg
                # Color gradient: Green -> Yellow -> Hot Red/Magenta
                pct = seg / 50.0
                if pct < 0.45:
                    col = (0, 240, 90)
                elif pct < 0.75:
                    col = (255, 210, 20)
                else:
                    col = (255, 30, 80)
                draw.rectangle([(x, sy - 1), (x + self.bar_width - 1, sy)], fill=col)

            # Draw falling peak hold dot
            py = max(4, base_y - peak_h - 1)
            draw.rectangle([(x, py), (x + self.bar_width - 1, py)], fill=(255, 255, 255))

            # Floor reflection (dim mirror)
            for seg in range(0, min(10, h // 4), 2):
                ry = base_y + 2 + seg
                if ry < size:
                    draw.rectangle([(x, ry), (x + self.bar_width - 1, ry)], fill=(0, 40, 25))


class OscilloscopeRibbonEffect(VisualizerEffect):
    """Hypnotic dual-wave Lissajous oscilloscope ribbon with chromatic glow."""

    name = "OSCILLOSCOPE RIBBON"

    def __init__(self, size: int = 64) -> None:
        self.size = size
        self.time = 0.0

    def reset(self) -> None:
        self.time = 0.0

    def update(self, dt: float, beat: float, bands: list[float] | None = None) -> None:
        energy = (sum(bands) / len(bands)) if (bands and len(bands) > 0) else beat
        self.time += dt * (1.2 + max(beat, energy) * 1.8)

    def draw(self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64) -> None:
        draw.rectangle([(0, 0), (size - 1, size - 1)], fill=(2, 1, 6))

        mid_x = size / 2.0
        mid_y = size / 2.0
        points_count = 64

        # Draw overlapping chromatic Lissajous paths
        for offset, color in [
            (-1.2, (255, 30, 110)),  # Red/Pink
            (0.0, (40, 255, 200)),   # Cyan/Mint
            (1.2, (140, 60, 255)),   # Violet
        ]:
            pts = []
            for i in range(points_count):
                t = (i / points_count) * 2 * math.pi
                # Parametric 3:2 harmonic curve
                x = mid_x + math.sin(t * 3.0 + self.time) * (mid_x - 6.0) + offset
                y = mid_y + math.sin(t * 2.0 + self.time * 0.8) * (mid_y - 8.0)
                pts.append((x, y))

            for k in range(len(pts) - 1):
                draw.line([pts[k], pts[k + 1]], fill=color, width=1)


class VisualizerRenderer(ModeRenderer):
    """Cycling music visualizer with radial, geometric, and particle animations."""

    def __init__(
        self,
        size: int = 64,
        cycle_interval: float = 60.0,
        effects: list[VisualizerEffect] | None = None,
        audio_source: Callable[[], Any] | None = None,
    ) -> None:
        self.size = size
        self.cycle_interval = cycle_interval
        self.effects = effects or [
            RadialSpectrumEffect(size),
            GeometricTunnelEffect(size),
            ParticleFireworksEffect(size),
            GraphicEqualizerEffect(size),
            OscilloscopeRibbonEffect(size),
        ]
        self.audio_source = audio_source
        self.current_idx = 0
        self.timer = 0.0
        self.beat_phase = 0.0

    @property
    def current_effect(self) -> VisualizerEffect:
        return self.effects[self.current_idx]

    @property
    def current_effect_name(self) -> str:
        return self.current_effect.name

    def set_effect(self, index_or_name: int | str) -> None:
        if isinstance(index_or_name, int):
            self.current_idx = index_or_name % len(self.effects)
            self.timer = 0.0
            self.current_effect.reset()
        else:
            name_lower = index_or_name.lower()
            for idx, eff in enumerate(self.effects):
                if eff.name.lower() == name_lower:
                    self.current_idx = idx
                    self.timer = 0.0
                    eff.reset()
                    return
            raise ValueError(f"Unknown visualizer effect: {index_or_name}")

    def update(self, dt: float, beat: float, bands: list[float] | None = None) -> None:
        # Cycle effects on timer
        if self.cycle_interval > 0 and len(self.effects) > 1:
            self.timer += dt
            if self.timer >= self.cycle_interval:
                steps = int(self.timer // self.cycle_interval)
                self.timer %= self.cycle_interval
                self.current_idx = (self.current_idx + steps) % len(self.effects)
                self.current_effect.reset()

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

        self.update(ctx.dt, beat, bands=bands)

        frame = Image.new("RGB", (size, size), (2, 2, 6))
        draw = ImageDraw.Draw(frame)
        self.current_effect.draw(frame, draw, size=size)
        return frame
