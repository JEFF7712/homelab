"""Regression tests for the Spotify vinyl disc renderer."""

import colorsys
import unittest

from PIL import Image, ImageDraw

from modes.base import HAState, PlaybackState, RenderContext
from modes.spotify_vinyl import SpotifyVinylRenderer
from spotify_matrix_streamer import render_record


def quadrant_art(size: int = 128) -> Image.Image:
    img = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, size // 2, size // 2), fill=(255, 0, 0))
    draw.rectangle((size // 2, 0, size, size // 2), fill=(0, 255, 0))
    draw.rectangle((0, size // 2, size // 2, size), fill=(0, 0, 255))
    draw.rectangle((size // 2, size // 2, size, size), fill=(255, 255, 0))
    return img


def muted_art(size: int = 128) -> Image.Image:
    img = Image.new("RGB", (size, size), (110, 100, 92))
    draw = ImageDraw.Draw(img)
    draw.ellipse((10, 10, 90, 90), fill=(150, 110, 95))
    draw.ellipse((50, 50, 120, 120), fill=(95, 130, 140))
    draw.rectangle((0, size - 24, size, size), fill=(70, 66, 72))
    return img


def disc_mean_saturation(frame: Image.Image) -> float:
    pixels = frame.load()
    size = frame.size[0]
    total = 0.0
    count = 0
    for y in range(size):
        for x in range(size):
            dx = x - size / 2
            dy = y - size / 2
            radius = (dx * dx + dy * dy) ** 0.5
            if radius > size / 2 - 2 or radius < 8:
                continue
            r, g, b = pixels[x, y]
            _, _, saturation = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            total += saturation
            count += 1
    assert count > 0
    return total / count


def playing_context(art: Image.Image | None) -> RenderContext:
    return RenderContext(
        tick=0.0,
        dt=0.05,
        ha_state=HAState(),
        spotify_state=PlaybackState(available=True, playing=True),
        spotify_art=art,
    )


class SpotifyVinylTests(unittest.TestCase):
    def test_saturated_art_stays_vivid(self) -> None:
        renderer = SpotifyVinylRenderer()
        renderer.angle = 13.0
        frame = renderer.render(playing_context(quadrant_art()), size=64)
        self.assertEqual(frame.size, (64, 64))
        self.assertGreaterEqual(disc_mean_saturation(frame), 0.80)

    def test_muted_art_gets_color_lift(self) -> None:
        art = muted_art()
        vivid = SpotifyVinylRenderer()
        vivid.angle = 13.0
        neutral = SpotifyVinylRenderer(saturation=1.0, contrast=1.0)
        neutral.angle = 13.0
        vivid_sat = disc_mean_saturation(vivid.render(playing_context(art)))
        neutral_sat = disc_mean_saturation(neutral.render(playing_context(art)))
        self.assertGreater(vivid_sat, neutral_sat)

    def test_legacy_render_record_matches_renderer(self) -> None:
        art = muted_art()
        renderer = SpotifyVinylRenderer()
        self.assertEqual(
            render_record(art, 13.0).tobytes(),
            renderer._render_record(art, 13.0).tobytes(),
        )

    def test_idle_disc_renders_without_art(self) -> None:
        frame = SpotifyVinylRenderer().render(playing_context(None), size=64)
        self.assertEqual(frame.size, (64, 64))
        self.assertEqual(frame.mode, "RGB")
        pixels = frame.load()
        self.assertEqual(pixels[32, 32], (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
