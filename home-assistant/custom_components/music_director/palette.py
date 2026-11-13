"""Album-art palettes for music lighting.

Pure functions only: callers decode the image and pass RGB pixels. Colours
are clustered and shaded in OKLab/OKLCH so hue shifts and lightness ramps
look even on LEDs.

Clusters of similar hue are scored together, so a cover-wide tint (a
muted blue across a dark cover) counts as one colour. Groups that read as
skin, brown or olive count for much less, so a portrait cover does not turn
the room amber. When no cover colour scores well enough, the caller falls
back to vibe_palette: a mood axis from deep blue (sad) through purple to
deep red (happy), nudged by genre, after music-colour association research
(Palmer et al., PNAS 2013).

Output vividness follows the track's intensity (0-1, from genre family,
energy and tempo; see rules.intensity). Intense tracks are steered toward
vivid reds and deep blues (oranges and yellows into reds, yellow-greens into
green, sky blues into deep blue) with boosted saturation; calm tracks keep
their cover's own hue and saturation. At every intensity, blues and reds
stay deep and the yellow-green band keeps a lightness floor so nothing
renders as mustard or olive (as Material's DislikeAnalyzer does).

How far the lights spread in hue follows the cover. A one-colour cover
gives every light that colour; a cover of related colours (within
RELATED_HUE_WIDTH) gives the lights those colours; a cover of unrelated
colours, and the vibe fallback, spread DEFAULT_SPREAD either side of the
primary. Intensity narrows any spread, so intense tracks show one sharp
colour.

Named colours (NAMED_HUES) serve the manual colour dropdown and per-song
overrides; they keep their hue at any intensity.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace

RGB = tuple[int, int, int]
Lab = tuple[float, float, float]

CLUSTERS = 6
ITERATIONS = 12
VIVID_CHROMA = 0.03
# Clusters at least this colourful join hue groups; greys and blacks do not.
TINT_CHROMA = 0.025
GROUP_HUE_WIDTH = 30.0
# A colour group counts toward the cover's spread when it holds at least this
# share of the cover's clean colour; the primary alone at SINGLE_SHARE or more
# makes it a one-colour cover.
SIGNIFICANT_SHARE = 0.10
SINGLE_SHARE = 0.75
# Significant groups all within this of the primary are a related palette.
RELATED_HUE_WIDTH = 60.0
# Hue offset either side of the primary for unrelated covers and vibes.
DEFAULT_SPREAD = 10.0
LED_LIGHTNESS = 0.70
# Calm tracks keep the cover's saturation, but never wash out to grey.
CALM_MIN_CHROMA = 0.09
# Fully intense tracks ask for this, past sRGB for most hues, so the light
# shows the most saturated version of its colour.
LED_MAX_CHROMA = 0.25
# A cover colour must score at least this (sqrt(share) * chroma * penalty) to
# beat the vibe fallback: about 1% of the cover in a vivid colour, or a tenth
# of it in a muted one.
MIN_COVER_SCORE = 0.015
# Weight for skin, brown and olive clusters.
MUDDY_WEIGHT = 0.15

# Hues (OKLCH degrees) for covers without a usable colour.
# Within one genre string the first match wins, so synthpop is synth, not pop.
GENRE_HUES: tuple[tuple[tuple[str, ...], float], ...] = (
    (("synth", "new wave", "retrowave", "outrun", "80s"), 262.0),
    (("hip hop", "hip-hop", "rap", "trap", "drill", "grime"), 18.0),
    (("r&b", "rnb", "soul", "neo soul", "funk"), 55.0),
    (("house", "techno", "edm", "electro", "electronic", "dance", "trance"), 235.0),
    (("metal", "rock", "punk", "grunge"), 18.0),
    (("pop",), 350.0),
    (("jazz", "blues", "swing", "big band"), 255.0),
    (("reggae", "reggaeton", "latin", "dancehall", "afro"), 145.0),
    (("ambient", "classical", "acoustic", "folk", "soundtrack", "score"), 210.0),
)
DEFAULT_HUE = 265.0
# The vibe mood axis runs from deep blue (sad) through purple to deep red (happy).
BLUE_HUE = 258.0
RED_HUE = 20.0
# How far the vibe hue moves toward the genre hue: most for calm tracks.
GENRE_PULL_CALM = 0.95
GENRE_PULL_INTENSE = 0.3

# Manual dropdown and per-song override colours (OKLCH hue degrees).
NAMED_HUES: dict[str, float] = {
    "red": 25.0,
    "orange": 55.0,
    "amber": 72.0,
    "pink": 350.0,
    "magenta": 330.0,
    "purple": 305.0,
    "violet": 285.0,
    "deep blue": 262.0,
    "blue": 245.0,
    "teal": 195.0,
    "green": 145.0,
}


@dataclass(frozen=True)
class Palette:
    """A primary hue and how far the lights spread from it, in OKLCH degrees."""

    hue: float
    chroma: float
    source: str
    # Other cover hues the lights take, most common first (related palettes).
    related: tuple[float, ...] = ()
    # Added to every output lightness: brighter for energetic songs.
    lift: float = 0.0
    # 0 keeps the cover's own look, 1 steers to vivid reds and deep blues.
    intensity: float = 0.5
    # Hue offset either side of the primary when there are no related hues.
    spread: float = 0.0


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


def muddy(lightness: float, chroma: float, hue: float) -> bool:
    """Skin tones, browns and olives: colours that look dull or dirty as light."""
    skin = 40.0 <= hue <= 85.0 and chroma <= 0.13 and 0.45 <= lightness <= 0.9
    brown = 20.0 <= hue <= 90.0 and lightness < 0.55 and chroma < 0.12
    olive = 90.0 < hue <= 125.0 and lightness < 0.72
    return skin or brown or olive


def _hue_groups(
    clusters: list[tuple[Lab, int]],
) -> list[tuple[float, float, float, float, bool]]:
    """Merge colourful clusters of similar hue. Returns (share, lightness,
    chroma, hue, muddy) per group, weighted by member share. Clusters are
    judged muddy one by one and only merge with their own kind, so dark red
    drips cannot drag a bright red into brown."""
    total = sum(n for _, n in clusters)
    members = [
        (n / total, c[0], _chroma(c), _hue(c), muddy(c[0], _chroma(c), _hue(c)))
        for c, n in clusters
        if _chroma(c) >= TINT_CHROMA and 0.15 <= c[0] <= 0.96
    ]
    # Seed groups from the most colourful-and-common clusters first.
    members.sort(key=lambda m: m[0] * m[2], reverse=True)
    groups: list[list[tuple[float, float, float, float, bool]]] = []
    for member in members:
        for group in groups:
            if (
                member[4] == group[0][4]
                and hue_distance(member[3], group[0][3]) <= GROUP_HUE_WIDTH
            ):
                group.append(member)
                break
        else:
            groups.append([member])
    merged = []
    for group in groups:
        share = sum(m[0] for m in group)
        x = sum(m[0] * math.cos(math.radians(m[3])) for m in group)
        y = sum(m[0] * math.sin(math.radians(m[3])) for m in group)
        merged.append(
            (
                share,
                sum(m[0] * m[1] for m in group) / share,
                sum(m[0] * m[2] for m in group) / share,
                math.degrees(math.atan2(y, x)) % 360.0,
                group[0][4],
            )
        )
    return merged


def extract_palette(pixels: list[RGB]) -> Palette | None:
    """Pick the most vivid well-represented colour group and read the cover's
    spread from the other clean groups (see the module docstring). Returns
    None when nothing on the cover is vivid and clean enough."""
    if not pixels:
        return None
    vivid = []
    for share, _, chroma, hue, dirty in _hue_groups(
        kmeans([rgb_to_oklab(p) for p in pixels])
    ):
        if chroma < VIVID_CHROMA:
            continue
        weight = MUDDY_WEIGHT if dirty else 1.0
        score = math.sqrt(share) * min(chroma, 0.25) * weight
        vivid.append((score, hue, chroma, share, dirty))
    vivid.sort(reverse=True)
    if not vivid or vivid[0][0] < MIN_COVER_SCORE:
        return None
    _, hue, chroma, share, _ = vivid[0]
    # Skin, brown and olive never light up, so they do not count as spread.
    clean = sum(g[3] for g in vivid if not g[4]) or share
    others = sorted(
        (g for g in vivid[1:] if not g[4] and g[3] >= clean * SIGNIFICANT_SHARE),
        key=lambda g: g[3],
        reverse=True,
    )
    if share >= clean * SINGLE_SHARE or not others:
        return Palette(hue=hue, chroma=chroma, source="cover")
    if all(hue_distance(g[1], hue) <= RELATED_HUE_WIDTH for g in others):
        related = tuple(g[1] for g in others)
        return Palette(hue=hue, chroma=chroma, source="cover", related=related)
    return Palette(hue=hue, chroma=chroma, source="cover", spread=DEFAULT_SPREAD)


def genre_hue(genres: tuple[str, ...]) -> float | None:
    for genre in genres:
        lowered = genre.lower()
        for keywords, hue in GENRE_HUES:
            if any(keyword in lowered for keyword in keywords):
                return hue
    return None


def _toward(hue: float, target: float, fraction: float) -> float:
    """Move hue a fraction of the shortest arc toward target."""
    delta = (target - hue + 180.0) % 360.0 - 180.0
    return (hue + delta * fraction) % 360.0


def vibe_palette(
    genres: tuple[str, ...] = (),
    energy: float | None = None,
    valence: float | None = None,
    tempo: float | None = None,
    intensity: float = 0.5,
) -> Palette:
    """Colour from the song instead of the cover. Valence picks a point on
    the mood axis (deep blue, purple, magenta, deep red) and the genre pulls
    it toward its own hue: strongly for calm tracks, so R&B stays warm
    orange, and weakly for intense ones, so mood decides. Without valence
    the genre hue is used as is. Energy (or tempo, 0-1) raises saturation
    and brightness. Lights spread DEFAULT_SPREAD around the hue."""
    base = genre_hue(genres)
    drive = energy if energy is not None else tempo
    drive = 0.5 if drive is None else min(1.0, max(0.0, drive))
    intensity = min(1.0, max(0.0, intensity))
    if valence is not None:
        mood = min(1.0, max(0.0, valence))
        hue = (BLUE_HUE + mood * (RED_HUE + 360.0 - BLUE_HUE)) % 360.0
        if base is not None:
            pull = GENRE_PULL_CALM + (GENRE_PULL_INTENSE - GENRE_PULL_CALM) * intensity
            hue = _toward(hue, base, pull)
    else:
        hue = DEFAULT_HUE if base is None else base
    known = base is not None or energy is not None or valence is not None
    return Palette(
        hue=hue,
        chroma=0.13 + 0.08 * drive,
        source="vibe" if known else "default",
        lift=round((drive - 0.5) * 0.08, 3),
        intensity=intensity,
        spread=DEFAULT_SPREAD,
    )


def named_palette(name: str, source: str = "manual") -> Palette | None:
    """A vivid single-colour palette for a NAMED_HUES colour (case, spaces
    and underscores ignored): every light shows that colour."""
    hue = NAMED_HUES.get(" ".join(name.lower().replace("_", " ").split()))
    if hue is None:
        return None
    return Palette(hue=hue, chroma=0.18, source=source, intensity=0.0)


def chosen_palette(rgb: RGB, source: str = "manual") -> Palette:
    """A single-colour palette for a colour picked on the colour wheel."""
    lab = rgb_to_oklab(rgb)
    return Palette(hue=_hue(lab), chroma=_chroma(lab), source=source, intensity=0.0)


def with_intensity(palette: Palette, intensity: float) -> Palette:
    return replace(palette, intensity=min(1.0, max(0.0, intensity)))


def lightness_floor(hue: float) -> float:
    """Minimum output lightness by hue. Yellows and yellow-greens look
    mustard or olive unless bright, and oranges look brown when dark; the
    floor peaks at yellow (100) and fades out by 45 and 150 degrees."""
    if not 45.0 <= hue <= 150.0:
        return 0.0
    if hue <= 100.0:
        return 0.66 + 0.20 * (hue - 45.0) / 55.0
    return 0.86 - 0.20 * (hue - 100.0) / 50.0


def steer(hue: float) -> float:
    """Output hue preference: oranges and yellows (30-105) become reds and
    corals (22-40), yellow-greens (105-135) become green (135-140), and
    azure and sky blues (200-255) become deep blue (240-262)."""
    if 30.0 < hue <= 105.0:
        return 22.0 + (hue - 30.0) * 18.0 / 75.0
    if 105.0 < hue < 135.0:
        return 135.0 + (hue - 105.0) * 5.0 / 30.0
    if 200.0 <= hue <= 255.0:
        return 240.0 + (hue - 200.0) * 22.0 / 55.0
    return hue


def lightness_ceiling(hue: float) -> float:
    """Blues and reds stay deep: pale blue reads as lavender and pale red as
    pink on LEDs."""
    if 220.0 <= hue <= 290.0:
        return 0.60
    if hue <= 30.0 or hue >= 350.0:
        return 0.64
    return 0.9


def boost(intensity: float) -> float:
    """0 below intensity 0.35 (cover look), rising to 1 at 0.8 (full boost)."""
    return min(1.0, max(0.0, (intensity - 0.35) / 0.45))


# Yellows are steered at least this much even on calm tracks, toward orange;
# chosen colours (manual dropdown, song overrides) are never steered.
YELLOW_BAND = (70.0, 110.0)
YELLOW_MIN_STEER = 0.6
CHOSEN_SOURCES = frozenset({"manual", "override"})


def _led(palette: Palette, hue: float, lightness: float = LED_LIGHTNESS) -> RGB:
    k = boost(palette.intensity)
    hue = hue % 360.0
    if palette.source not in CHOSEN_SOURCES and YELLOW_BAND[0] < hue < YELLOW_BAND[1]:
        k = max(k, YELLOW_MIN_STEER)
    hue = _toward(hue, steer(hue), k)
    faithful = max(palette.chroma * 1.15, CALM_MIN_CHROMA)
    chroma = min(LED_MAX_CHROMA, faithful + (LED_MAX_CHROMA - faithful) * k)
    lightness = max(lightness + palette.lift, lightness_floor(hue))
    lightness = min(lightness, lightness_ceiling(hue))
    return oklch_to_rgb(lightness, chroma, hue)


def spread_label(palette: Palette) -> str:
    """How the lights spread around the primary, for the dashboard."""
    width = 1.0 - boost(palette.intensity)
    if palette.source in CHOSEN_SOURCES:
        return "one chosen colour"
    if width == 0.0 and (palette.related or palette.spread):
        return "one sharp colour (intense)"
    if palette.related:
        return "the cover's colours"
    if palette.spread * width < 1.0:
        return "one colour"
    return f"±{round(palette.spread * width)}° around one colour"


def shade_hues(palette: Palette, count: int) -> list[float]:
    """One hue per slot: the primary alternating with the cover's related
    hues, or steps across the spread. Intensity narrows both toward the
    primary, down to one colour at full boost."""
    width = 1.0 - boost(palette.intensity)
    if palette.related:
        cycle = (palette.hue, *palette.related)
        return [
            _toward(palette.hue, cycle[i % len(cycle)], width) for i in range(count)
        ]
    step = 2.0 * palette.spread * width / max(1, count - 1)
    return [(palette.hue + (i - (count - 1) / 2) * step) % 360.0 for i in range(count)]


def area_colors(palette: Palette, count: int) -> list[RGB]:
    """Shades for neighbouring areas; lightness varies so areas sharing one
    hue still read apart."""
    return [
        _led(palette, hue, (0.64, 0.74, 0.69)[index % 3])
        for index, hue in enumerate(shade_hues(palette, count))
    ]


def triad(palette: Palette) -> tuple[RGB, RGB, RGB]:
    """Low, mid and high colours for three-colour LedFx effects."""
    low, mid, high = shade_hues(palette, 3)
    return (
        _led(palette, low, 0.58),
        _led(palette, mid, 0.70),
        _led(palette, high, 0.84),
    )


def solid_colour(palette: Palette) -> RGB:
    """The one colour the strip shows when set to solid."""
    return _led(palette, palette.hue)


def _css(stops: list[tuple[RGB, int]]) -> str:
    parts = ", ".join(f"rgb({r}, {g}, {b}) {pos}%" for (r, g, b), pos in stops)
    return f"linear-gradient(90deg, {parts})"


def solid_gradient(palette: Palette) -> str:
    """A single-colour gradient, for gradient effects on a solid strip."""
    colour = solid_colour(palette)
    return _css([(colour, 0), (colour, 100)])


def gradient(palette: Palette) -> str:
    """A CSS linear-gradient in the format LedFx gradient effects accept."""
    hues = shade_hues(palette, 4)
    return _css(
        [
            (_led(palette, hues[0], 0.55), 0),
            (_led(palette, hues[1], 0.70), 35),
            (_led(palette, hues[2], 0.72), 70),
            (_led(palette, hues[3], 0.82), 100),
        ]
    )


def to_hex(rgb: RGB) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)
