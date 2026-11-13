"""Choose a LedFx effect for a track from its genre and audio features.

Each track maps to a family of effects; the effect within a family is a
stable hash of the track, so a replay gets the same look, and consecutive
tracks never repeat the same effect when the family has an alternative.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from typing import Any

from .palette import Palette, gradient, solid_colour, solid_gradient, to_hex, triad

FAMILIES: dict[str, tuple[str, ...]] = {
    "calm": ("melt", "lava_lamp"),
    "smooth": ("melt", "energy"),
    "bass": ("power", "energy"),
    "drive": ("scroll", "bands", "strobe"),
    "rock": ("wavelength", "bands"),
    "default": ("energy",),
}

# Every effect the director can set, in family order; the manual override
# dropdown offers exactly these.
EFFECTS: tuple[str, ...] = tuple(
    dict.fromkeys(effect for effects in FAMILIES.values() for effect in effects)
)

# BPM mapped onto each effect's speed range: 70 BPM is slowest, 160 fastest.
SLOW_BPM = 70.0
FAST_BPM = 160.0

# Ordered: within one genre string the first matching family wins.
GENRE_FAMILIES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        (
            "ambient",
            "classical",
            "acoustic",
            "folk",
            "soundtrack",
            "film score",
            "lo-fi",
            "lofi",
            "chill",
            "new age",
            "piano",
        ),
        "calm",
    ),
    (
        (
            "house",
            "techno",
            "edm",
            "electronic",
            "electro",
            "dance",
            "trance",
            "dubstep",
            "drum and bass",
            "garage",
            "disco",
        ),
        "drive",
    ),
    (("metal", "rock", "punk", "grunge", "hardcore"), "rock"),
    (
        (
            "hip hop",
            "hip-hop",
            "rap",
            "trap",
            "drill",
            "grime",
            "reggaeton",
            "dancehall",
        ),
        "bass",
    ),
    (("r&b", "rnb", "soul", "funk", "jazz", "reggae", "blues"), "smooth"),
)

GRADIENT_EFFECTS = frozenset(
    {"melt", "lava_lamp", "power", "bands", "wavelength", "strobe"}
)
TRIAD_EFFECTS = frozenset({"energy", "scroll"})


@dataclass(frozen=True)
class Features:
    genres: tuple[str, ...] = ()
    bpm: float | None = None
    energy: float | None = None
    danceability: float | None = None
    acousticness: float | None = None
    valence: float | None = None


def genre_family(genres: tuple[str, ...]) -> str | None:
    """Family of the first genre that maps to one; sources list the primary
    genre first, and the keyword table breaks ties within one genre."""
    for genre in genres:
        lowered = genre.lower()
        for keywords, family in GENRE_FAMILIES:
            if any(keyword in lowered for keyword in keywords):
                return family
    return None


def classify(features: Features) -> str:
    family = genre_family(features.genres)
    energy = features.energy
    if energy is not None:
        acoustic = features.acousticness or 0.0
        if energy < 0.35 or (acoustic > 0.7 and energy < 0.5):
            return "calm"
        if family == "bass" and energy < 0.5:
            return "smooth"
        danceable = (features.danceability or 0.0) >= 0.6
        in_club_tempo = features.bpm is not None and 110 <= features.bpm <= 140
        if family in (None, "smooth") and energy > 0.75 and in_club_tempo and danceable:
            return "drive"
    if family is not None:
        return family
    if energy is not None and energy > 0.7:
        return "drive"
    if features.bpm is not None and features.bpm < 85 and energy is not None:
        return "calm"
    return "default"


# How intense each family is before audio features: drives colour vividness.
FAMILY_INTENSITY: dict[str, float] = {
    "calm": 0.15,
    "smooth": 0.3,
    "default": 0.5,
    "bass": 0.75,
    "rock": 0.8,
    "drive": 0.85,
}


def intensity(features: Features, family: str) -> float:
    """0 (calm) to 1 (intense): the genre's family prior (else the chosen
    family's) blended with energy, or with tempo when ReccoBeats has no
    features. The genre sets the prior, so mellow rap that plays a smooth
    effect still rates as fairly intense; energy pulls it down."""
    prior = FAMILY_INTENSITY[genre_family(features.genres) or family]
    if features.energy is not None:
        return round(0.5 * prior + 0.5 * _unit(features.energy), 3)
    tempo = tempo_level(features.bpm)
    if tempo is not None:
        return round(0.7 * prior + 0.3 * tempo, 3)
    return prior


def choose_effect(family: str, track_key: str, previous: str | None) -> str:
    candidates = FAMILIES[family]
    index = zlib.crc32(track_key.encode("utf-8")) % len(candidates)
    if candidates[index] == previous and len(candidates) > 1:
        index = (index + 1) % len(candidates)
    return candidates[index]


def _unit(value: float) -> float:
    return min(1.0, max(0.0, value))


def tempo_level(bpm: float | None) -> float | None:
    """0 at SLOW_BPM or below, 1 at FAST_BPM or above."""
    if bpm is None:
        return None
    return _unit((bpm - SLOW_BPM) / (FAST_BPM - SLOW_BPM))


def dynamics(effect: str, features: Features) -> dict[str, Any]:
    """Speed from tempo and responsiveness from energy, inside each LedFx
    schema range; unknown inputs leave the LedFx default in place."""
    config: dict[str, Any] = {}
    tempo = tempo_level(features.bpm)
    if tempo is not None:
        if effect == "melt":
            config["speed"] = round(0.2 + 0.6 * tempo, 3)  # schema 0.001-1
        elif effect == "lava_lamp":
            config["speed"] = round(3.0 + 9.0 * tempo, 2)  # schema 0.1-15
        elif effect == "scroll":
            config["speed"] = round(2 + 6 * tempo)  # integer, schema 1-10
    if features.energy is not None:
        energy = _unit(features.energy)
        if effect == "melt":
            config["reactivity"] = round(0.3 + 0.6 * energy, 3)  # 0.0001-1
        elif effect == "lava_lamp":
            config["reactivity"] = round(0.15 + 0.6 * energy, 3)  # 0.00001-0.9
        elif effect == "energy":
            config["sensitivity"] = round(0.45 + 0.45 * energy, 3)  # 0.3-0.99
        elif effect == "power":
            config["bass_decay_rate"] = round(0.03 + 0.12 * energy, 3)  # 0-1
        elif effect == "scroll":
            config["decay"] = round(0.99 - 0.06 * energy, 3)  # 0.8-1
    return config


def effect_config(
    effect: str,
    palette: Palette,
    features: Features | None = None,
    solid: bool = False,
) -> dict[str, Any]:
    """Colours plus tempo and energy dynamics for the effect; LedFx fills
    every other key with its schema default. Solid gives every colour slot
    the same one colour."""
    colours = (solid_colour(palette),) * 3 if solid else triad(palette)
    if effect in GRADIENT_EFFECTS:
        config: dict[str, Any] = {
            "gradient": solid_gradient(palette) if solid else gradient(palette)
        }
        if effect == "power":
            config["sparks_color"] = to_hex(colours[2])
    elif effect in TRIAD_EFFECTS:
        lows, mids, high = colours
        config = {
            "color_lows": to_hex(lows),
            "color_mids": to_hex(mids),
            "color_high": to_hex(high),
        }
    else:
        raise ValueError(f"no colour mapping for LedFx effect {effect!r}")
    if features is not None:
        config.update(dynamics(effect, features))
    return config
