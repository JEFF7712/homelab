"""Gravity, collisions, waves, harmonic pendulums, sand, galaxy, flow fields, fire, and volcano on the matrix."""

from __future__ import annotations

import random

import numpy as np
from PIL import Image, ImageDraw

from modes.base import ModeRenderer, RenderContext


class PhysicsRenderer(ModeRenderer):
    PERIOD = 20.0
    NUM_SCENES = 9

    def __init__(self, randomize: bool = True) -> None:
        rng = np.random.default_rng()
        self.pos = np.zeros((36, 2), dtype=np.float64)
        self.vel = np.zeros((36, 2), dtype=np.float64)
        self.wave = np.zeros((64, 64), dtype=np.float32)
        self.previous = self.wave.copy()

        # Scene 3: Pendulum wave state
        self.pendulum_trail = np.zeros((64, 64, 3), dtype=np.float32)

        # Scene 4: Granular sand grid (PixelDust style)
        self.sand_grid = np.zeros((64, 64), dtype=np.uint8)

        # Scene 5: Spiral galaxy & vortex state (WLED PS Galaxy style)
        self.galaxy_pos = np.zeros((80, 2), dtype=np.float64)
        self.galaxy_vel = np.zeros((80, 2), dtype=np.float64)
        self.galaxy_colors = np.zeros((80, 3), dtype=np.uint8)
        self.galaxy_trail = np.zeros((64, 64, 3), dtype=np.float32)

        # Scene 6: Vector Flow Field state (Aurora / Animartrix)
        self.flow_pos = np.zeros((80, 2), dtype=np.float64)
        self.flow_trail = np.zeros((64, 64, 3), dtype=np.float32)

        # Scene 7: Convective Fire state (FastLED Fire2012 / Fire2018)
        self.fire_heat = np.zeros((64, 64), dtype=np.float32)
        self.fire_palette = self._build_fire_palette()

        # Scene 8: Volcanic Particle Fountain state (WLED PS Volcano)
        self.volcano_pos = np.zeros((70, 2), dtype=np.float64)
        self.volcano_vel = np.zeros((70, 2), dtype=np.float64)
        self.volcano_temp = np.zeros(70, dtype=np.float64)
        self.volcano_trail = np.zeros((64, 64, 3), dtype=np.float32)

        self.last_scene = -1
        self.rng = rng
        self.scene_timer = 0.0
        self.randomize = randomize
        self.playlist: list[int] = list(range(self.NUM_SCENES))
        self.playlist_pos = 0
        if self.randomize:
            random.shuffle(self.playlist)
        self.current_scene = self.playlist[0] if self.randomize else 0

    def reset(self) -> None:
        """Reset rotation order (called when switching into Physics mode)."""
        self.scene_timer = 0.0
        if self.randomize:
            random.shuffle(self.playlist)
            self.playlist_pos = 0
            self.current_scene = self.playlist[0]
        else:
            self.playlist_pos = 0
            self.current_scene = 0

    def set_scene(self, scene: int) -> None:
        """Manually select a scene by index and reset the timer."""
        self.current_scene = scene % self.NUM_SCENES
        self.scene_timer = 0.0
        if self.randomize and self.current_scene in self.playlist:
            self.playlist_pos = self.playlist.index(self.current_scene)

    def _next_scene(self) -> None:
        if self.randomize:
            self.playlist_pos += 1
            if self.playlist_pos >= len(self.playlist):
                last_idx = self.playlist[-1]
                self.playlist = list(range(self.NUM_SCENES))
                random.shuffle(self.playlist)
                # Avoid playing the same scene consecutively across playlist loops
                if len(self.playlist) > 1 and self.playlist[0] == last_idx:
                    self.playlist[0], self.playlist[1] = (
                        self.playlist[1],
                        self.playlist[0],
                    )
                self.playlist_pos = 0
            self.current_scene = self.playlist[self.playlist_pos]
        else:
            self.current_scene = (self.current_scene + 1) % self.NUM_SCENES

    @staticmethod
    def _build_fire_palette() -> np.ndarray:
        palette = np.zeros((256, 3), dtype=np.uint8)
        for i in range(256):
            if i < 48:
                # Deep dark ember to dark red
                r = int((i / 48.0) * 120)
                g = int((i / 48.0) * 15)
                b = 0
            elif i < 112:
                # Dark red to bright fiery orange
                t = (i - 48) / 64.0
                r = int(120 + t * 120)
                g = int(15 + t * 75)
                b = 0
            elif i < 192:
                # Fiery orange to vibrant golden yellow
                t = (i - 112) / 80.0
                r = 240 + int(t * 15)
                g = 90 + int(t * 120)
                b = int(t * 25)
            else:
                # Golden yellow to white-hot plasma core
                t = (i - 192) / 64.0
                r = 255
                g = 210 + int(t * 45)
                b = 25 + int(t * 205)
            palette[i] = [r, g, b]
        return palette

    def _reset_particles(self, scene: int, size: int) -> None:
        if scene == 0:
            angles = np.linspace(0, 2 * np.pi, len(self.pos), endpoint=False)
            angles += self.rng.uniform(-0.08, 0.08, len(self.pos))
            radius = self.rng.uniform(11, size / 2 - 5, len(self.pos))
            direction = np.column_stack((np.cos(angles), np.sin(angles)))
            self.pos = np.array([size / 2, size / 2]) + direction * radius[:, None]
            tangent = np.column_stack((-direction[:, 1], direction[:, 0]))
            self.vel = tangent * self.rng.uniform(14.5, 15.2, (len(self.pos), 1))
        else:
            self.pos = self.rng.uniform(4, size - 5, self.pos.shape)
            self.vel = self.rng.normal(0, 9, self.vel.shape)

    def _reset_galaxy(self, size: int) -> None:
        center = np.array([size / 2, size / 2], dtype=np.float64)
        n = len(self.galaxy_pos)
        arms = 3
        arm_indices = np.arange(n) % arms
        arm_angles = arm_indices * (2 * np.pi / arms)
        radii = self.rng.uniform(6.0, size / 2 - 4.0, n)
        angles = arm_angles + 1.8 * np.log(radii / 5.0) + self.rng.normal(0, 0.16, n)

        self.galaxy_pos[:, 0] = center[0] + radii * np.cos(angles)
        self.galaxy_pos[:, 1] = center[1] + radii * np.sin(angles)

        tangent_x = -np.sin(angles)
        tangent_y = np.cos(angles)
        radial_x = -np.cos(angles)
        radial_y = -np.sin(angles)
        speed = np.sqrt(340.0 / np.maximum(radii, 4.0))

        self.galaxy_vel[:, 0] = tangent_x * speed + radial_x * 0.4
        self.galaxy_vel[:, 1] = tangent_y * speed + radial_y * 0.4

        for i, r in enumerate(radii):
            if r < 12.0:
                self.galaxy_colors[i] = (255, 230, 180)
            elif r < 20.0:
                self.galaxy_colors[i] = (80, 190, 255)
            else:
                self.galaxy_colors[i] = (200, 110, 255)

    def _reset_volcano(self, size: int) -> None:
        self.volcano_pos[:, 0] = size / 2.0
        self.volcano_pos[:, 1] = size - 2.0
        # Stagger initial life so sparks don't all erupt simultaneously on frame 0
        self.volcano_temp = np.linspace(0.05, 1.0, len(self.volcano_temp))
        for i in range(len(self.volcano_temp)):
            self.volcano_vel[i, 0] = self.rng.normal(0, 12.0)
            self.volcano_vel[i, 1] = self.rng.uniform(-78.0, -52.0)
        self.volcano_trail.fill(0)

    def render(self, ctx: RenderContext, size: int = 64) -> Image.Image:
        dt = min(max(ctx.dt, 0.0), 0.05)
        should_cycle = (
            ctx.ha_state.cycle
            if ctx is not None and hasattr(ctx, "ha_state") and ctx.ha_state is not None
            else True
        )
        if should_cycle:
            self.scene_timer += ctx.dt
            if self.scene_timer >= self.PERIOD:
                steps = int(self.scene_timer // self.PERIOD)
                self.scene_timer %= self.PERIOD
                for _ in range(steps):
                    self._next_scene()
        else:
            self.scene_timer = 0.0

        scene = self.current_scene
        if scene != self.last_scene:
            self.last_scene = scene
            if scene == 2:
                self.wave.fill(0)
                self.previous.fill(0)
                self.wave[size // 2, size // 2] = 180.0
            elif scene in (0, 1):
                self._reset_particles(scene, size)
            elif scene == 3:
                self.pendulum_trail.fill(0)
            elif scene == 4:
                self.sand_grid.fill(0)
            elif scene == 5:
                self._reset_galaxy(size)
                self.galaxy_trail.fill(0)
            elif scene == 6:
                self.flow_pos = self.rng.uniform(4.0, size - 5.0, self.flow_pos.shape)
                self.flow_trail.fill(0)
            elif scene == 7:
                if self.fire_heat.shape != (size, size):
                    self.fire_heat = np.zeros((size, size), dtype=np.float32)
                else:
                    self.fire_heat.fill(0)
                # Seed base sparks so fire ignites immediately
                self.fire_heat[size - 4 :, :] = self.rng.uniform(
                    180.0, 255.0, (4, size)
                )
            elif scene == 8:
                self._reset_volcano(size)

        if scene == 2:
            return self._waves(size)
        if scene == 3:
            return self._pendulum_wave(size, dt)
        if scene == 4:
            return self._sand(size, dt)
        if scene == 5:
            return self._galaxy(size, dt)
        if scene == 6:
            return self._flow_field(size, dt)
        if scene == 7:
            return self._fire(size, dt)
        if scene == 8:
            return self._volcano(size, dt)
        return self._particles(scene, dt, size)

    def _particles(self, scene: int, dt: float, size: int) -> Image.Image:
        if scene == 0:
            delta = np.array([size / 2, size / 2]) - self.pos
            radius = np.maximum(np.linalg.norm(delta, axis=1), 5)
            self.vel += delta / radius[:, None] * (220 / radius[:, None]) * dt
        else:
            self.vel[:, 1] += 36.0 * dt
        self.pos += self.vel * dt
        if scene == 0:
            center = np.array([size / 2, size / 2])
            offset = self.pos - center
            distance = np.linalg.norm(offset, axis=1)
            inside = distance < 7
            if np.any(inside):
                normal = offset[inside] / np.maximum(distance[inside][:, None], 1)
                normal[distance[inside] < 1] = (1, 0)
                self.pos[inside] = center + normal * 7
                inward = np.sum(self.vel[inside] * normal, axis=1)
                self.vel[inside] -= np.minimum(inward, 0)[:, None] * 1.8 * normal
        for axis in (0, 1):
            low = self.pos[:, axis] < 2
            high = self.pos[:, axis] > size - 3
            self.pos[low, axis] = 2
            self.pos[high, axis] = size - 3
            if scene == 1 and axis == 1:
                floor_hit = high
                if np.any(floor_hit):
                    rebound = np.abs(self.vel[floor_hit, 1]) * 0.92
                    too_slow = rebound < 18.0
                    rebound[too_slow] = self.rng.uniform(28.0, 48.0, np.sum(too_slow))
                    self.vel[floor_hit, 1] = -rebound
                    self.vel[floor_hit, 0] += self.rng.normal(0, 3.0, np.sum(floor_hit))
                ceil_hit = low
                if np.any(ceil_hit):
                    self.vel[ceil_hit, 1] = np.abs(self.vel[ceil_hit, 1]) * 0.90
            else:
                self.vel[low | high, axis] *= -0.88
        if scene == 1:
            delta = self.pos[:, None, :] - self.pos[None, :, :]
            dist2 = np.sum(delta * delta, axis=2)
            collision = (dist2 > 0) & (dist2 < 16)
            push = np.sum(
                np.where(
                    collision[:, :, None], delta / np.maximum(dist2[:, :, None], 1), 0
                ),
                axis=1,
            )
            self.vel += push * 25 * dt
        if scene == 1:
            self.vel *= 0.999
        frame = Image.new("RGB", (size, size), (2, 5, 13))
        draw = ImageDraw.Draw(frame)
        if scene == 0:
            draw.ellipse((29, 29, 35, 35), fill=(255, 195, 70))
        for i, (x, y) in enumerate(self.pos):
            color = (80, 160 + i % 90, 255) if scene == 0 else (90 + i % 120, 190, 255)
            px, py = round(x), round(y)
            draw.rectangle((px, py, px + 1, py + 1), fill=color)
        return frame

    def _waves(self, size: int) -> Image.Image:
        next_wave = (
            np.roll(self.wave, 1, 0)
            + np.roll(self.wave, -1, 0)
            + np.roll(self.wave, 1, 1)
            + np.roll(self.wave, -1, 1)
        ) * 0.5 - self.previous
        next_wave *= 0.985
        next_wave[[0, -1], :] = 0
        next_wave[:, [0, -1]] = 0
        self.previous, self.wave = self.wave, next_wave
        if self.rng.random() < 0.12:
            x, y = self.rng.integers(8, 56, 2)
            self.wave[y, x] = 130
        value = np.clip(np.abs(self.wave) * 4, 0, 255).astype(np.uint8)
        rgb = np.zeros((64, 64, 3), dtype=np.uint8)
        rgb[:, :, 0] = value // 5
        rgb[:, :, 1] = value // 2
        rgb[:, :, 2] = value
        return Image.fromarray(rgb, mode="RGB").resize((size, size))

    def _pendulum_wave(self, size: int, dt: float) -> Image.Image:
        """Harmonic pendulum wave simulation (SmartMatrix Aurora PatternPendulumWave)."""
        num_pendulums = 16
        anchor_y = 2
        anchors_x = np.linspace(12, size - 13, num_pendulums)
        base_cycles = 10
        amplitude = 0.20
        lengths = np.linspace(26.0, 56.0, num_pendulums)

        self.pendulum_trail *= 0.86
        trail_uint8 = np.clip(self.pendulum_trail, 0, 255).astype(np.uint8)
        frame = Image.fromarray(trail_uint8, mode="RGB")
        draw = ImageDraw.Draw(frame)

        draw.line(
            (int(anchors_x[0]) - 3, anchor_y, int(anchors_x[-1]) + 3, anchor_y),
            fill=(24, 32, 48),
        )

        t = self.scene_timer
        for i in range(num_pendulums):
            freq = 2 * np.pi * (base_cycles + i) / self.PERIOD
            theta = amplitude * np.sin(freq * t)
            length = lengths[i]

            bx = anchors_x[i] + length * np.sin(theta)
            by = anchor_y + length * np.cos(theta)
            px, py = int(round(bx)), int(round(by))

            hue = i / num_pendulums
            color = (
                int(127.5 * (1.0 + np.sin(2 * np.pi * hue))),
                int(127.5 * (1.0 + np.sin(2 * np.pi * hue + 2 * np.pi / 3))),
                int(127.5 * (1.0 + np.sin(2 * np.pi * hue + 4 * np.pi / 3))),
            )

            draw.line((int(round(anchors_x[i])), anchor_y, px, py), fill=(20, 28, 44))

            if 0 <= px < size - 1 and 0 <= py < size - 1:
                self.pendulum_trail[py : py + 2, px : px + 2] = color

            draw.rectangle((px, py, px + 1, py + 1), fill=color)

        return frame

    def _sand(self, size: int, dt: float) -> Image.Image:
        """Granular sand piles and hourglass drain (Adafruit PixelDust cellular automaton)."""
        palette = np.array(
            [
                [4, 5, 12],  # 0: Empty background
                [255, 195, 60],  # 1: Amber Gold sand
                [235, 120, 40],  # 2: Copper Ochre sand
                [240, 75, 65],  # 3: Coral Sunset sand
                [50, 215, 185],  # 4: Cyan Quartz sand
            ],
            dtype=np.uint8,
        )

        is_draining = self.scene_timer >= 16.5
        if not is_draining and self.rng.random() < 0.85:
            spout_x_coords = (16, 32, 48)
            for spout_x in spout_x_coords:
                if self.rng.random() < 0.65 and self.sand_grid[2, spout_x] == 0:
                    sand_type = int(self.rng.integers(1, 5))
                    self.sand_grid[2, spout_x] = sand_type

        if is_draining:
            self.sand_grid[size - 1, 24:40] = 0
            self.sand_grid[size - 2, 28:36] = 0

        for y in range(size - 2, -1, -1):
            cols = np.where(self.sand_grid[y] > 0)[0]
            if len(cols) == 0:
                continue
            cols = self.rng.permutation(cols)
            for x in cols:
                grain = self.sand_grid[y, x]
                if self.sand_grid[y + 1, x] == 0:
                    self.sand_grid[y + 1, x] = grain
                    self.sand_grid[y, x] = 0
                else:
                    can_left = (x > 1) and (self.sand_grid[y + 1, x - 1] == 0)
                    can_right = (x < size - 2) and (self.sand_grid[y + 1, x + 1] == 0)
                    if can_left and can_right:
                        dx = -1 if self.rng.random() < 0.5 else 1
                        self.sand_grid[y + 1, x + dx] = grain
                        self.sand_grid[y, x] = 0
                    elif can_left:
                        self.sand_grid[y + 1, x - 1] = grain
                        self.sand_grid[y, x] = 0
                    elif can_right:
                        self.sand_grid[y + 1, x + 1] = grain
                        self.sand_grid[y, x] = 0

        frame_array = palette[self.sand_grid]
        return Image.fromarray(frame_array, mode="RGB")

    def _galaxy(self, size: int, dt: float) -> Image.Image:
        """Spiral galaxy and central vortex accretion (WLED PS Galaxy / PS Vortex)."""
        center = np.array([size / 2, size / 2], dtype=np.float64)
        delta = center - self.galaxy_pos
        dist_sq = np.sum(delta * delta, axis=1)
        dist = np.sqrt(dist_sq)

        acc_magnitude = 360.0 / (dist_sq + 18.0)
        acc = delta / np.maximum(dist[:, None], 1.0) * acc_magnitude[:, None]

        self.galaxy_vel += acc * dt
        self.galaxy_vel *= 0.9985
        self.galaxy_pos += self.galaxy_vel * dt

        in_core = dist < 4.2
        out_of_bounds = (
            (self.galaxy_pos[:, 0] < 1)
            | (self.galaxy_pos[:, 0] > size - 2)
            | (self.galaxy_pos[:, 1] < 1)
            | (self.galaxy_pos[:, 1] > size - 2)
        )
        respawn = in_core | out_of_bounds
        if np.any(respawn):
            num_respawn = int(np.sum(respawn))
            new_radii = self.rng.uniform(size / 2 - 6.0, size / 2 - 3.0, num_respawn)
            new_angles = self.rng.uniform(0, 2 * np.pi, num_respawn)

            self.galaxy_pos[respawn, 0] = center[0] + new_radii * np.cos(new_angles)
            self.galaxy_pos[respawn, 1] = center[1] + new_radii * np.sin(new_angles)

            speed = np.sqrt(340.0 / new_radii)
            self.galaxy_vel[respawn, 0] = -np.sin(new_angles) * speed
            self.galaxy_vel[respawn, 1] = np.cos(new_angles) * speed

        self.galaxy_trail *= 0.84
        px = np.clip(np.round(self.galaxy_pos[:, 0]).astype(int), 0, size - 1)
        py = np.clip(np.round(self.galaxy_pos[:, 1]).astype(int), 0, size - 1)

        for i in range(len(self.galaxy_pos)):
            self.galaxy_trail[py[i], px[i]] = np.maximum(
                self.galaxy_trail[py[i], px[i]], self.galaxy_colors[i]
            )

        frame = Image.fromarray(
            np.clip(self.galaxy_trail, 0, 255).astype(np.uint8), mode="RGB"
        )
        draw = ImageDraw.Draw(frame)

        cx, cy = int(center[0]), int(center[1])
        draw.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=(255, 210, 110))
        draw.ellipse((cx - 1, cy - 1, cx + 1, cy + 1), fill=(10, 12, 20))

        return frame

    def _flow_field(self, size: int, dt: float) -> Image.Image:
        """Streamline particle advection in curl force fields (Aurora PatternFlowField & Animartrix)."""
        t = self.scene_timer
        u = (self.flow_pos[:, 0] - size / 2.0) / 16.0
        v = (self.flow_pos[:, 1] - size / 2.0) / 16.0

        # Divergence-free incompressible velocity field via stream function curl
        vx = (
            -np.sin(u + 0.35 * t) * np.sin(v - 0.28 * t)
            + 0.25 * np.sin(1.3 * u - 0.6 * v + 0.4 * t)
        ) * 22.0
        vy = (
            -np.cos(u + 0.35 * t) * np.cos(v - 0.28 * t)
            - 0.70 * np.sin(1.3 * u - 0.6 * v + 0.4 * t)
        ) * 22.0

        self.flow_pos[:, 0] = np.mod(self.flow_pos[:, 0] + vx * dt, size)
        self.flow_pos[:, 1] = np.mod(self.flow_pos[:, 1] + vy * dt, size)

        # Decay previous flow trails
        self.flow_trail *= 0.88

        # Color based on flow velocity heading (smooth cyan/azure/electric purple spectrum)
        angles = np.arctan2(vy, vx)
        hues = (angles / (2 * np.pi) + 0.5) % 1.0

        r_vals = np.clip(30 + 70 * (1.0 + np.cos(2 * np.pi * hues)), 0, 255).astype(
            np.uint8
        )
        g_vals = np.clip(120 + 90 * (1.0 + np.sin(2 * np.pi * hues)), 0, 255).astype(
            np.uint8
        )
        b_vals = np.clip(
            200 + 55 * (1.0 + np.cos(2 * np.pi * hues + np.pi / 2)), 0, 255
        ).astype(np.uint8)

        px = np.clip(np.round(self.flow_pos[:, 0]).astype(int), 0, size - 2)
        py = np.clip(np.round(self.flow_pos[:, 1]).astype(int), 0, size - 2)

        for i in range(len(self.flow_pos)):
            color = [r_vals[i], g_vals[i], b_vals[i]]
            self.flow_trail[py[i] : py[i] + 2, px[i] : px[i] + 2] = np.maximum(
                self.flow_trail[py[i] : py[i] + 2, px[i] : px[i] + 2], color
            )

        return Image.fromarray(
            np.clip(self.flow_trail, 0, 255).astype(np.uint8), mode="RGB"
        )

    def _fire(self, size: int, dt: float) -> Image.Image:
        """Convective heat diffusion and buoyancy simulation (FastLED Fire2012 / Fire2018)."""
        if self.fire_heat.shape != (size, size):
            self.fire_heat = np.zeros((size, size), dtype=np.float32)

        # Convection: heat drifts upward with lateral jitter and smoothing
        offsets = self.rng.choice([-1, 0, 0, 1], size=(size - 1, size))
        cols = (np.arange(size) + offsets) % size
        rows = np.arange(1, size)[:, None]

        advected = self.fire_heat[rows, cols]
        smoothed = (
            advected * 0.70
            + np.roll(advected, 1, axis=1) * 0.15
            + np.roll(advected, -1, axis=1) * 0.15
        )

        # Subtle cooling gradient: flames taper gracefully near the top
        decay = self.rng.uniform(1.2, 4.4, size=(size - 1, size))
        cooling_gradient = np.linspace(1.25, 0.75, size - 1)[:, None]
        self.fire_heat[:-1, :] = np.maximum(smoothed - decay * cooling_gradient, 0.0)

        # Base ignition: nozzles spark fresh heat at the bottom
        base = self.rng.uniform(210.0, 255.0, size)
        sparks = self.rng.random(size) < 0.80
        self.fire_heat[size - 1, :] = np.where(
            sparks, base, self.fire_heat[size - 1, :] * 0.85
        )

        flare = self.rng.random(size) < 0.25
        self.fire_heat[size - 2, flare] = self.rng.uniform(190.0, 250.0, np.sum(flare))

        heat_indices = np.clip(self.fire_heat, 0, 255).astype(np.uint8)
        frame_rgb = self.fire_palette[heat_indices]
        return Image.fromarray(frame_rgb, mode="RGB")

    def _volcano(self, size: int, dt: float) -> Image.Image:
        """Ballistic volcanic particle fountain with splash rebound (WLED PS Volcano / Waterfall)."""
        center_x = size / 2.0
        nozzle_y = size - 2.0

        # Gravity and ballistic physics
        self.volcano_vel[:, 1] += 55.0 * dt
        self.volcano_vel[:, 0] *= 0.994
        self.volcano_pos += self.volcano_vel * dt
        self.volcano_temp -= 0.28 * dt

        # Ground collision and splash rebound
        hit_ground = (self.volcano_pos[:, 1] >= nozzle_y) & (self.volcano_vel[:, 1] > 0)
        still_hot = self.volcano_temp > 0.15
        bounce = hit_ground & still_hot
        if np.any(bounce):
            self.volcano_vel[bounce, 1] *= -0.38
            self.volcano_vel[bounce, 0] += self.rng.normal(0, 9.0, np.sum(bounce))
            self.volcano_pos[bounce, 1] = nozzle_y

        # Recycle extinct or out-of-bounds sparks into nozzle
        dead = (
            (self.volcano_temp <= 0)
            | (self.volcano_pos[:, 0] < 0)
            | (self.volcano_pos[:, 0] >= size)
            | (self.volcano_pos[:, 1] > size)
        )
        if np.any(dead):
            num_dead = int(np.sum(dead))
            self.volcano_pos[dead, 0] = center_x + self.rng.normal(0, 1.8, num_dead)
            self.volcano_pos[dead, 1] = nozzle_y
            self.volcano_vel[dead, 0] = self.rng.normal(0, 12.0, num_dead)
            self.volcano_vel[dead, 1] = self.rng.uniform(-78.0, -52.0, num_dead)
            self.volcano_temp[dead] = self.rng.uniform(0.85, 1.0, num_dead)

        # Decay particle trails
        self.volcano_trail *= 0.82

        px = np.clip(np.round(self.volcano_pos[:, 0]).astype(int), 0, size - 1)
        py = np.clip(np.round(self.volcano_pos[:, 1]).astype(int), 0, size - 1)

        # Color mapping based on temperature: white-hot -> orange -> red -> dark ember
        for i in range(len(self.volcano_pos)):
            temp = self.volcano_temp[i]
            if temp > 0.75:
                color = [255, 245, 180]
            elif temp > 0.45:
                color = [255, 135, 25]
            elif temp > 0.18:
                color = [215, 40, 15]
            else:
                color = [85, 14, 6]

            self.volcano_trail[py[i], px[i]] = np.maximum(
                self.volcano_trail[py[i], px[i]], color
            )

        frame = Image.fromarray(
            np.clip(self.volcano_trail, 0, 255).astype(np.uint8), mode="RGB"
        )
        draw = ImageDraw.Draw(frame)

        # Volcano caldera nozzle base
        cx = int(center_x)
        draw.polygon(
            [(cx - 7, size - 1), (cx, size - 3), (cx + 7, size - 1)], fill=(45, 18, 12)
        )
        return frame
