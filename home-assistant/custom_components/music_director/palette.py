"""Album-art palettes for music lighting.

Pure functions only: callers decode the image and pass RGB pixels. Colours
are clustered and shaded in OKLab/OKLCH so hue shifts and lightness ramps
look even on LEDs. Mostly grey or monochrome covers yield no palette, and
the caller falls back to a genre hue.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

RGB = tuple[int, int, int]
Lab = tuple[float, float, float]

CLUSTERS = 6
ITERATIONS = 12
VIVID_CHROMA = 0.06
ACCENT_MIN_HUE_GAP = 40.0
LED_LIGHTNESS = 0.70
LED_MAX_CHROMA = 0.22

# Hues (OKLCH degrees) for covers without a usable colour.
GENRE_HUES: tuple[tuple[tuple[str, ...], float], ...] = (
    (("hip hop", "hip-hop", "rap", "trap", "drill", "grime"), 300.0),
    (("r&b", "rnb", "soul", "neo soul", "funk"), 350.0),
    (("house", "techno", "edm", "electro", "electronic", "dance", "trance"), 215.0),
    (("metal", "rock", "punk", "grunge"), 30.0),
    (("pop",), 0.0),
    (("jazz", "blues", "swing", "big band"), 70.0),
    (("reggae", "reggaeton", "latin", "dancehall", "afro"), 140.0),
    (("ambient", "classical", "acoustic", "folk", "soundtrack", "score"), 180.0),
)
DEFAULT_HUE = 265.0


@dataclass(frozen=True)
class Palette:
    """A primary hue with an optional contrasting accent, in OKLCH degrees."""

    hue: float
    chroma: float
    accent_hue: float | None
    source: str


def _to_linear(channel: float) -> float:
    c = channel / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_srgb(channel: float) -> float:
    if channel <= 0.0031308:
        return 12.92 * channel
    return 1.055 * channel ** (1 / 2.4) - 0.055


def rgb_to_oklab(rgb: RGB) -> Lab:
    r, g, b = (_to_linear(c) for c in rgb)
    l_ = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m_ = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s_ = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (math.copysign(abs(v) ** (1 / 3), v) for v in (l_, m_, s_))
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def _oklab_to_linear(lab: Lab) -> tuple[float, float, float]:
    lightness, a, b = lab
    l_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (
        4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
        -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
        -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_,
    )


def oklch_to_rgb(lightness: float, chroma: float, hue: float) -> RGB:
    """Convert OKLCH to sRGB, lowering chroma until the colour is in gamut."""
    radians = math.radians(hue)
    linear = _oklab_to_linear((lightness, 0.0, 0.0))
    for _ in range(40):
        candidate = _oklab_to_linear(
            (lightness, chroma * math.cos(radians), chroma * math.sin(radians))
        )
        if all(-1e-4 <= c <= 1.0001 for c in candidate):
            linear = candidate
            break
        chroma *= 0.92
    channels = (min(1.0, max(0.0, _to_srgb(max(0.0, c)))) for c in linear)
    r, g, b = (round(c * 255) for c in channels)
    return (r, g, b)


def _hue(lab: Lab) -> float:
    return math.degrees(math.atan2(lab[2], lab[1])) % 360.0


def _chroma(lab: Lab) -> float:
    return math.hypot(lab[1], lab[2])


def hue_distance(first: float, second: float) -> float:
    delta = abs(first - second) % 360.0
    return min(delta, 360.0 - delta)


def _distance(first: Lab, second: Lab) -> float:
    return sum((x - y) ** 2 for x, y in zip(first, second, strict=True))


def kmeans(points: list[Lab], k: int = CLUSTERS) -> list[tuple[Lab, int]]:
    """Deterministic k-means++; returns (centroid, member count) pairs."""
    if not points:
        return []
    rng = random.Random(0)
    centroids = [points[rng.randrange(len(points))]]
    while len(centroids) < min(k, len(points)):
        weights = [min(_distance(p, c) for c in centroids) for p in points]
        total = sum(weights)
        if total <= 0:
            break
        centroids.append(rng.choices(points, weights=weights)[0])
    counts = [0] * len(centroids)
    for _ in range(ITERATIONS):
        sums = [[0.0, 0.0, 0.0] for _ in centroids]
        counts = [0] * len(centroids)
        for point in points:
            index = min(
                range(len(centroids)), key=lambda i: _distance(point, centroids[i])
            )
            counts[index] += 1
            for axis in range(3):
                sums[index][axis] += point[axis]
        updated = [
            (s[0] / n, s[1] / n, s[2] / n) if n else centroids[i]
            for i, (s, n) in enumerate(zip(sums, counts, strict=True))
        ]
        if updated == centroids:
            break
        centroids = updated
    return [(c, n) for c, n in zip(centroids, counts, strict=True) if n]


def extract_palette(pixels: list[RGB]) -> Palette | None:
    """Pick the most vivid well-represented colour, plus a contrasting accent."""
    if not pixels:
        return None
    clusters = kmeans([rgb_to_oklab(p) for p in pixels])
    total = sum(n for _, n in clusters)
    vivid = []
    for centroid, count in clusters:
        chroma = _chroma(centroid)
        if chroma < VIVID_CHROMA or not 0.25 <= centroid[0] <= 0.96:
            continue
        score = math.sqrt(count / total) * min(chroma, 0.25)
        vivid.append((score, _hue(centroid), chroma))
    if not vivid:
        return None
    vivid.sort(reverse=True)
    best_score, hue, chroma = vivid[0]
    accent = next(
        (
            h
            for s, h, _ in vivid[1:]
            if s >= best_score * 0.3 and hue_distance(h, hue) >= ACCENT_MIN_HUE_GAP
        ),
        None,
    )
    return Palette(hue=hue, chroma=chroma, accent_hue=accent, source="cover")


def genre_palette(genres: tuple[str, ...]) -> Palette:
    for genre in genres:
        lowered = genre.lower()
        for keywords, hue in GENRE_HUES:
            if any(keyword in lowered for keyword in keywords):
                return Palette(hue=hue, chroma=0.18, accent_hue=None, source="genre")
    return Palette(hue=DEFAULT_HUE, chroma=0.18, accent_hue=None, source="default")


def _led(palette: Palette, hue: float, lightness: float = LED_LIGHTNESS) -> RGB:
    chroma = min(LED_MAX_CHROMA, max(palette.chroma * 1.4, 0.14))
    return oklch_to_rgb(lightness, chroma, hue)


def area_colors(palette: Palette, count: int) -> list[RGB]:
    """Distinct shades for neighbouring areas: hue steps around the primary,
    every third area on the accent when the cover has one."""
    colours: list[RGB] = []
    spread = 28.0
    for index in range(count):
        offset = (index - (count - 1) / 2) * (spread / max(1, count - 1)) * 2
        lightness = (0.64, 0.74, 0.69)[index % 3]
        if palette.accent_hue is not None and index % 3 == 1:
            colours.append(_led(palette, palette.accent_hue, lightness))
        else:
            colours.append(_led(palette, (palette.hue + offset) % 360.0, lightness))
    return colours


def triad(palette: Palette) -> tuple[RGB, RGB, RGB]:
    """Low, mid and high colours for three-colour LedFx effects."""
    accent = (
        palette.accent_hue
        if palette.accent_hue is not None
        else (palette.hue + 35.0) % 360.0
    )
    return (
        _led(palette, palette.hue, 0.58),
        _led(palette, accent, 0.70),
        _led(palette, (palette.hue - 20.0) % 360.0, 0.84),
    )


def gradient(palette: Palette) -> str:
    """A CSS linear-gradient in the format LedFx gradient effects accept."""
    accent = (
        palette.accent_hue
        if palette.accent_hue is not None
        else (palette.hue + 30.0) % 360.0
    )
    stops = (
        (_led(palette, (palette.hue - 15.0) % 360.0, 0.55), 0),
        (_led(palette, palette.hue, 0.70), 35),
        (_led(palette, accent, 0.72), 70),
        (_led(palette, (palette.hue + 10.0) % 360.0, 0.82), 100),
    )
    parts = ", ".join(f"rgb({r}, {g}, {b}) {pos}%" for (r, g, b), pos in stops)
    return f"linear-gradient(90deg, {parts})"


def to_hex(rgb: RGB) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)
