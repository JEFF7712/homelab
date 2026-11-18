"""Spotify Vinyl mode renderer (spinning album art disc)."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageEnhance, ImageOps

from modes.base import ModeRenderer, RenderContext

# The 64x64 panel's four-bit color depth mutes album art, so the fitted disc
# art gets a fixed saturation/contrast lift before rotation. Pure primaries
# are unaffected (they clamp), while muted photographic art reads as vivid.
VINYL_SATURATION = 1.25
VINYL_CONTRAST = 1.12

_GEOMETRY_CACHE: dict[int, tuple[int, int, Image.Image, Image.Image]] = {}
_IDLE_CACHE: dict[int, Image.Image] = {}


def compensate_disc_color(
    art: Image.Image, saturation: float, contrast: float
) -> Image.Image:
    if saturation != 1.0:
        art = ImageEnhance.Color(art).enhance(saturation)
    if contrast != 1.0:
        art = ImageEnhance.Contrast(art).enhance(contrast)
    return art


def _disc_geometry(size: int) -> tuple[int, int, Image.Image, Image.Image]:
    cached = _GEOMETRY_CACHE.get(size)
    if cached is not None:
        return cached

    margin = max(2, size // 32)
    disc_size = size - margin * 2

    disc_mask = Image.new("L", (disc_size, disc_size), 0)
    mask_draw = ImageDraw.Draw(disc_mask)
    mask_draw.ellipse((0, 0, disc_size - 1, disc_size - 1), fill=255)

    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
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

    result = (margin, disc_size, disc_mask, overlay)
    _GEOMETRY_CACHE[size] = result
    return result


class SpotifyVinylRenderer(ModeRenderer):
    def __init__(
        self,
        saturation: float = VINYL_SATURATION,
        contrast: float = VINYL_CONTRAST,
    ) -> None:
        self.angle = 0.0
        self.saturation = saturation
        self.contrast = contrast
        self._fitted_cache: list[tuple[Image.Image, int, Image.Image]] = []

    def _prepare_disc_art(self, art: Image.Image, disc_size: int) -> Image.Image:
        for cached_art, cached_size, prepared in self._fitted_cache:
            if cached_art is art and cached_size == disc_size:
                return prepared

        fitted = ImageOps.fit(
            art, (disc_size, disc_size), method=Image.Resampling.LANCZOS
        )
        prepared = compensate_disc_color(
            fitted, self.saturation, self.contrast
        ).convert("RGBA")
        self._fitted_cache.append((art, disc_size, prepared))
        if len(self._fitted_cache) > 6:
            self._fitted_cache.pop(0)
        return prepared

    def render(self, ctx: RenderContext, size: int = 64) -> Image.Image:
        # Advance angle if music is playing
        if ctx.spotify_state.available and ctx.spotify_state.playing:
            self.angle = (self.angle + ctx.dt * ctx.rotation_speed) % 360.0

        art = ctx.spotify_art if ctx.spotify_state.available else None
        return self._render_record(art, self.angle, size)

    def _render_record(
        self, art: Image.Image | None, angle: float, size: int = 64
    ) -> Image.Image:
        if art is None:
            return self._render_idle_disc(size)

        margin, disc_size, disc_mask, overlay = _disc_geometry(size)
        art_square = self._prepare_disc_art(art, disc_size)
        rotated = art_square.rotate(angle, resample=Image.Resampling.BICUBIC)

        frame = Image.new("RGBA", (size, size), (0, 0, 0, 255))
        frame.paste(rotated, (margin, margin), disc_mask)
        return Image.alpha_composite(frame, overlay).convert("RGB")

    def _render_idle_disc(self, size: int = 64) -> Image.Image:
        cached = _IDLE_CACHE.get(size)
        if cached is not None:
            return cached
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
        _IDLE_CACHE[size] = frame
        return frame
