"""Spotify Vinyl mode renderer (spinning album art disc)."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageOps

from modes.base import ModeRenderer, RenderContext


class SpotifyVinylRenderer(ModeRenderer):
    def __init__(self) -> None:
        self.angle = 0.0

    def render(self, ctx: RenderContext, size: int = 64) -> Image.Image:
        # Advance angle if music is playing
        if ctx.spotify_state.available and ctx.spotify_state.playing:
            self.angle = (self.angle + ctx.dt * ctx.rotation_speed) % 360.0

        art = ctx.spotify_art if ctx.spotify_state.available else None
        return self._render_record(art, self.angle, size)

    def _render_record(
        self, art: Image.Image | None, angle: float, size: int = 64
    ) -> Image.Image:
        frame = Image.new("RGBA", (size, size), (0, 0, 0, 255))
        if art is None:
            # Idle record without album art
            return self._render_idle_disc(size)

        margin = max(2, size // 32)
        disc_size = size - margin * 2

        # Crop album art to disk, rotate by angle
        art_square = ImageOps.fit(
            art, (disc_size, disc_size), method=Image.Resampling.LANCZOS
        )
        rotated = art_square.rotate(angle, resample=Image.Resampling.BICUBIC)

        # Circular mask
        disc_mask = Image.new("L", (disc_size, disc_size), 0)
        mask_draw = ImageDraw.Draw(disc_mask)
        mask_draw.ellipse((0, 0, disc_size - 1, disc_size - 1), fill=255)
        frame.paste(rotated.convert("RGBA"), (margin, margin), disc_mask)

        # Draw vinyl edges and center spindle label
        draw = ImageDraw.Draw(frame, "RGBA")
        outer = (margin, margin, size - margin - 1, size - margin - 1)
        draw.ellipse(outer, outline=(6, 6, 6, 255), width=max(1, size // 32))

        center = size // 2
        label_radius = max(5, size // 11)
        hole_radius = max(2, size // 25)

        draw.ellipse(
            (
                center - label_radius,
                center - label_radius,
                center + label_radius,
                center + label_radius,
            ),
            fill=(16, 16, 16, 210),
            outline=(220, 220, 220, 90),
        )
        draw.ellipse(
            (
                center - hole_radius,
                center - hole_radius,
                center + hole_radius,
                center + hole_radius,
            ),
            fill=(0, 0, 0, 255),
        )
        return frame.convert("RGB")

    def _render_idle_disc(self, size: int = 64) -> Image.Image:
        frame = Image.new("RGB", (size, size), (0, 0, 0))
        draw = ImageDraw.Draw(frame)
        margin = max(2, size // 32)
        draw.ellipse(
            (margin, margin, size - margin - 1, size - margin - 1),
            outline=(40, 40, 40),
            width=1,
        )
        center = size // 2
        label_radius = max(5, size // 11)
        hole_radius = max(2, size // 25)
        draw.ellipse(
            (
                center - label_radius,
                center - label_radius,
                center + label_radius,
                center + label_radius,
            ),
            outline=(60, 60, 60),
            fill=(20, 20, 20),
        )
        draw.ellipse(
            (
                center - hole_radius,
                center - hole_radius,
                center + hole_radius,
                center + hole_radius,
            ),
            fill=(0, 0, 0),
        )
        return frame
