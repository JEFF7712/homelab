"""Track metadata parsing and merging for music_director.

Pure functions over the JSON bodies of the local library lookup, ReccoBeats
and Last.fm, so the precedence rules can be tested without the network.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from .rules import Features

LASTFM_MIN_TAG_COUNT = 10
LASTFM_MAX_TAGS = 5
SPOTIFY_TRACK_URI = re.compile(r"^spotify:track:([A-Za-z0-9]{22})$")
# Tags that describe listening habits or eras rather than a genre.
LASTFM_NOISE = re.compile(
    r"^(seen live|favou?rites?|my favou?rites?|love|loved|awesome|beautiful|"
    r"cool|best|amazing|\d{2,4}s?|under \d+ listeners)$"
)


def normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", ascii_only.lower())


def same_track(title: str, artist: str, other_title: str, other_artist: str) -> bool:
    """True when two players report the same song; Spotify Connect titles can
    differ from the account entity only in punctuation and featuring."""
    if not title or not other_title:
        return False
    first, second = normalize(title), normalize(other_title)
    if first != second and not (first.startswith(second) or second.startswith(first)):
        return False
    a, b = normalize(artist), normalize(other_artist)
    return not a or not b or a in b or b in a


def spotify_track_id(content_id: str | None) -> str | None:
    match = SPOTIFY_TRACK_URI.match(content_id or "")
    return match.group(1) if match else None


@dataclass
class TrackInfo:
    """Everything known about the playing track, with where it came from."""

    genres: tuple[str, ...] = ()
    bpm: float | None = None
    energy: float | None = None
    danceability: float | None = None
    acousticness: float | None = None
    valence: float | None = None
    isrc: str | None = None
    sources: list[str] = field(default_factory=list)

    def features(self) -> Features:
        return Features(
            genres=self.genres,
            bpm=self.bpm,
            energy=self.energy,
            danceability=self.danceability,
            acousticness=self.acousticness,
            valence=self.valence,
        )


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and value > 0 else None


def apply_reccobeats(info: TrackInfo, body: Any) -> None:
    content = body.get("content") if isinstance(body, dict) else None
    if not content or not isinstance(content[0], dict):
        return
    item = content[0]
    info.bpm = _number(item.get("tempo")) or info.bpm
    info.energy = _number(item.get("energy"))
    info.danceability = _number(item.get("danceability"))
    info.acousticness = _number(item.get("acousticness"))
    info.valence = _number(item.get("valence"))
    isrc = item.get("isrc")
    if isinstance(isrc, str) and isrc:
        info.isrc = isrc.upper()
    info.sources.append("reccobeats")


def apply_library(info: TrackInfo, body: Any) -> None:
    match = body.get("match") if isinstance(body, dict) else None
    if not isinstance(match, dict):
        return
    genres = tuple(g for g in match.get("genres") or () if isinstance(g, str) and g)
    if genres:
        info.genres = genres
    if info.bpm is None:
        info.bpm = _number(match.get("bpm"))
    info.sources.append(f"library:{body.get('matched_by') or 'match'}")


def lastfm_genres(body: Any, artist: str = "") -> tuple[str, ...]:
    tags = (
        (body or {}).get("toptags", {}).get("tag", []) if isinstance(body, dict) else []
    )
    if isinstance(tags, dict):
        tags = [tags]
    artist_key = normalize(artist)
    genres: list[str] = []
    for tag in tags:
        name = str(tag.get("name", "")).strip().lower()
        try:
            count = int(tag.get("count", 0))
        except (TypeError, ValueError):
            count = 0
        if count < LASTFM_MIN_TAG_COUNT or not name or LASTFM_NOISE.match(name):
            continue
        if artist_key and normalize(name) == artist_key:
            continue
        if name not in genres:
            genres.append(name)
        if len(genres) == LASTFM_MAX_TAGS:
            break
    return tuple(genres)


def apply_lastfm(info: TrackInfo, body: Any, artist: str) -> None:
    genres = lastfm_genres(body, artist)
    if genres and not info.genres:
        info.genres = genres
        info.sources.append("lastfm")
