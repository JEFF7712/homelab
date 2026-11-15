"""What the director learned about each track, kept across restarts.

Each entry is the track's looked-up info (genres, tempo, mood) and its
cover palette. Entries are stored in Home Assistant storage, tagged with a
hash of the code that produced them (palette and metadata parsing), so a
deploy that changes colour extraction starts from an empty cache instead of
replaying stale colours.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from .metadata import TrackInfo, normalize, same_track
from .palette import Palette

CACHE_STORAGE_KEY = "music_director.tracks"
CACHE_STORAGE_VERSION = 1
# Room for every song in the library's playlists (about 7,500 in 2026-11).
CACHE_SIZE = 10000
SAVE_DELAY_SECONDS = 30


def code_version() -> str:
    """A hash of the modules whose output the cache holds."""
    here = Path(__file__).parent
    digest = hashlib.sha256()
    for name in ("palette.py", "metadata.py"):
        digest.update((here / name).read_bytes())
    return digest.hexdigest()[:16]


def track_key(artist: str, title: str, album: str) -> str:
    return f"{artist}\n{title}\n{album}"


def _album(key: str) -> str:
    return normalize(key.rsplit("\n", 1)[-1])


@dataclass(frozen=True)
class Entry:
    info: TrackInfo
    cover: Palette | None


def _info_from(data: dict[str, Any]) -> TrackInfo:
    return TrackInfo(
        **{
            **data,
            "genres": tuple(data.get("genres", ())),
            "sources": list(data.get("sources", ())),
        }
    )


def _palette_from(data: dict[str, Any] | None) -> Palette | None:
    if data is None:
        return None
    rgb = data.get("rgb")
    return Palette(
        **{
            **data,
            "related": tuple(data.get("related", ())),
            "rgb": tuple(rgb) if rgb else None,
        }
    )


class TrackCache:
    """Least recently used track entries, keyed by track_key."""

    def __init__(self, size: int = CACHE_SIZE, version: str = "") -> None:
        self.size = size
        self.version = version
        self.entries: OrderedDict[str, Entry] = OrderedDict()
        # Keys by normalised album, so find() checks one album, not them all.
        self._albums: dict[str, set[str]] = {}
        # Each entry serialised once, so saving thousands stays cheap.
        self._stored: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> Entry | None:
        entry = self.entries.get(key)
        if entry is not None:
            self.entries.move_to_end(key)
        return entry

    def put(self, key: str, entry: Entry, stored: dict[str, Any] | None = None) -> None:
        """Add or refresh an entry; stored is its saved form, when known."""
        self.entries[key] = entry
        self.entries.move_to_end(key)
        self._albums.setdefault(_album(key), set()).add(key)
        self._stored[key] = stored or {
            "info": asdict(entry.info),
            "cover": None if entry.cover is None else asdict(entry.cover),
        }
        while len(self.entries) > self.size:
            old, _ = self.entries.popitem(last=False)
            del self._stored[old]
            keys = self._albums.get(_album(old))
            if keys is not None:
                keys.discard(old)
                if not keys:
                    del self._albums[_album(old)]

    def clear(self) -> None:
        self.entries.clear()
        self._albums.clear()
        self._stored.clear()

    def find(self, key: str) -> Entry | None:
        """The entry for key, else one for the same song on the same album
        spelled another way (punctuation, featured artists)."""
        entry = self.get(key)
        if entry is not None:
            return entry
        artist, title, _ = key.split("\n", 2)
        for other in sorted(self._albums.get(_album(key), ())):
            other_artist, other_title, _ = other.split("\n", 2)
            if same_track(title, artist, other_title, other_artist):
                return self.get(other)
        return None

    def to_data(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "tracks": {key: self._stored[key] for key in self.entries},
        }

    def load(self, data: Any) -> None:
        """Replace the entries with stored ones from the same code version;
        anything malformed is dropped."""
        self.clear()
        if not isinstance(data, dict) or data.get("version") != self.version:
            return
        tracks = data.get("tracks")
        if not isinstance(tracks, dict):
            return
        for key, item in tracks.items():
            if not isinstance(key, str) or key.count("\n") != 2:
                continue
            try:
                entry = Entry(_info_from(item["info"]), _palette_from(item["cover"]))
            except (KeyError, TypeError, ValueError):
                continue
            self.put(key, entry, item)


class CacheStorage(Protocol):
    async def async_load(self) -> Any: ...

    def async_delay_save(self, data_func: Any, delay: float = 0) -> None: ...

    async def async_save(self, data: Any) -> None: ...


class TrackCacheStore:
    """A TrackCache persisted through a Home Assistant Store."""

    def __init__(self, storage: CacheStorage) -> None:
        self._storage = storage
        self.cache = TrackCache()

    async def async_load(self, version: str) -> None:
        self.cache.version = version
        self.cache.load(await self._storage.async_load())

    def put(self, key: str, entry: Entry) -> None:
        self.cache.put(key, entry)
        self._storage.async_delay_save(self.cache.to_data, SAVE_DELAY_SECONDS)

    async def async_save(self) -> None:
        """Write now; delayed saves wait for a quiet spell, which a long
        batch of puts never leaves."""
        await self._storage.async_save(self.cache.to_data())
