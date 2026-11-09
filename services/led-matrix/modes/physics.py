"""Gravity, collisions, wave propagation, harmonic pendulums, sand, and galaxy vortex on the matrix."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from modes.base import ModeRenderer, RenderContext


class PhysicsRenderer(ModeRenderer):
    PERIOD = 20.0
    NUM_SCENES = 6

    def __init__(self) -> None:
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

        self.last_scene = -1
        self.rng = rng
        self.scene_timer = 0.0
        self.current_scene = 0

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
        # Logarithmic spiral winding angle with slight angular noise
        angles = arm_angles + 1.8 * np.log(radii / 5.0) + self.rng.normal(0, 0.16, n)

        self.galaxy_pos[:, 0] = center[0] + radii * np.cos(angles)
        self.galaxy_pos[:, 1] = center[1] + radii * np.sin(angles)

        # Orbital velocity: v_t = sqrt(GM / r) with slight inward migration
        tangent_x = -np.sin(angles)
        tangent_y = np.cos(angles)
        radial_x = -np.cos(angles)
        radial_y = -np.sin(angles)
        speed = np.sqrt(340.0 / np.maximum(radii, 4.0))

        self.galaxy_vel[:, 0] = tangent_x * speed + radial_x * 0.4
        self.galaxy_vel[:, 1] = tangent_y * speed + radial_y * 0.4

        # Colors: core/inner = warm white/gold, mid = azure cyan, outer = violet magenta
        for i, r in enumerate(radii):
            if r < 12.0:
                self.galaxy_colors[i] = (255, 230, 180)
            elif r < 20.0:
                self.galaxy_colors[i] = (80, 190, 255)
            else:
                self.galaxy_colors[i] = (200, 110, 255)

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
                self.current_scene = (self.current_scene + steps) % self.NUM_SCENES
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

        if scene == 2:
            return self._waves(size)
        if scene == 3:
            return self._pendulum_wave(size, dt)
        if scene == 4:
            return self._sand(size, dt)
        if scene == 5:
            return self._galaxy(size, dt)
        return self._particles(scene, dt, size)

    def _particles(self, scene: int, dt: float, size: int) -> Image.Image:
        if scene == 0:
            delta = np.array([size / 2, size / 2]) - self.pos
            radius = np.maximum(np.linalg.norm(delta, axis=1), 5)
            self.vel += delta / radius[:, None] * (220 / radius[:, None]) * dt
        else:
            self.vel[:, 1] += 42 * dt
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
            self.vel += push * 20 * dt
        if scene == 1:
            self.vel *= 0.998
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
        anchor_y = 3
        anchors_x = np.linspace(6, size - 7, num_pendulums)
        base_cycles = 10
        amplitude = 0.48

        # Fade previous trails for glowing ribbon persistence
        self.pendulum_trail *= 0.86
        trail_uint8 = np.clip(self.pendulum_trail, 0, 255).astype(np.uint8)
        frame = Image.fromarray(trail_uint8, mode="RGB")
        draw = ImageDraw.Draw(frame)

        # Subtle top anchor mounting bar
        draw.line((4, anchor_y, size - 5, anchor_y), fill=(24, 32, 48))

        t = self.scene_timer
        for i in range(num_pendulums):
            freq = 2 * np.pi * (base_cycles + i) / self.PERIOD
            theta = amplitude * np.sin(freq * t)
            length = 21.0 + i * 1.55

            bx = anchors_x[i] + length * np.sin(theta)
            by = anchor_y + length * np.cos(theta)
            px, py = int(round(bx)), int(round(by))

            # Spectral rainbow palette across pendulums
            hue = i / num_pendulums
            color = (
                int(127.5 * (1.0 + np.sin(2 * np.pi * hue))),
                int(127.5 * (1.0 + np.sin(2 * np.pi * hue + 2 * np.pi / 3))),
                int(127.5 * (1.0 + np.sin(2 * np.pi * hue + 4 * np.pi / 3))),
            )

            # Draw thin pendulum suspension stem
            draw.line((int(round(anchors_x[i])), anchor_y, px, py), fill=(20, 28, 44))

            # Deposit glowing light into phosphor trail buffer
            if 0 <= px < size - 1 and 0 <= py < size - 1:
                self.pendulum_trail[py : py + 2, px : px + 2] = color

            # Draw crisp glowing bob
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

        # Emit new grains while not in final drain phase
        is_draining = self.scene_timer >= 16.5
        if not is_draining and self.rng.random() < 0.85:
            spout_x_coords = (16, 32, 48)
            for spout_x in spout_x_coords:
                if self.rng.random() < 0.65 and self.sand_grid[2, spout_x] == 0:
                    sand_type = int(self.rng.integers(1, 5))
                    self.sand_grid[2, spout_x] = sand_type

        # During drain phase, clear bottom center funnel to empty the sand piles
        if is_draining:
            self.sand_grid[size - 1, 24:40] = 0
            self.sand_grid[size - 2, 28:36] = 0

        # Update sand physics from bottom to top
        for y in range(size - 2, -1, -1):
            cols = np.where(self.sand_grid[y] > 0)[0]
            if len(cols) == 0:
                continue
            # Shuffle columns to prevent directional bias
            cols = self.rng.permutation(cols)
            for x in cols:
                grain = self.sand_grid[y, x]
                # Fall straight down if clear
                if self.sand_grid[y + 1, x] == 0:
                    self.sand_grid[y + 1, x] = grain
                    self.sand_grid[y, x] = 0
                else:
                    # Slide diagonally at angle of repose
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

        # Gravitational core acceleration with softening radius
        acc_magnitude = 360.0 / (dist_sq + 18.0)
        acc = delta / np.maximum(dist[:, None], 1.0) * acc_magnitude[:, None]

        self.galaxy_vel += acc * dt
        # Subtle tangential conservation & orbital drag
        self.galaxy_vel *= 0.9985
        self.galaxy_pos += self.galaxy_vel * dt

        # Respawn stars that fall into the central black hole or escape screen bounds
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

        # Decay star trails
        self.galaxy_trail *= 0.84
        px = np.clip(np.round(self.galaxy_pos[:, 0]).astype(int), 0, size - 1)
        py = np.clip(np.round(self.galaxy_pos[:, 1]).astype(int), 0, size - 1)

        # Deposit star luminance onto trail buffer
        for i in range(len(self.galaxy_pos)):
            self.galaxy_trail[py[i], px[i]] = np.maximum(
                self.galaxy_trail[py[i], px[i]], self.galaxy_colors[i]
            )

        frame = Image.fromarray(
            np.clip(self.galaxy_trail, 0, 255).astype(np.uint8), mode="RGB"
        )
        draw = ImageDraw.Draw(frame)

        # Draw glowing central core / accretion boundary
        cx, cy = int(center[0]), int(center[1])
        draw.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=(255, 210, 110))
        draw.ellipse((cx - 1, cy - 1, cx + 1, cy + 1), fill=(10, 12, 20))

        return frame
