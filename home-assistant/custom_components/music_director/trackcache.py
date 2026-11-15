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
CACHE_SIZE = 1000
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

    def get(self, key: str) -> Entry | None:
        entry = self.entries.get(key)
        if entry is not None:
            self.entries.move_to_end(key)
        return entry

    def put(self, key: str, entry: Entry) -> None:
        self.entries[key] = entry
        self.entries.move_to_end(key)
        while len(self.entries) > self.size:
            self.entries.popitem(last=False)

    def find(self, key: str) -> Entry | None:
        """The entry for key, else one for the same song on the same album
        spelled another way (punctuation, featured artists)."""
        entry = self.get(key)
        if entry is not None:
            return entry
        artist, title, album = key.split("\n", 2)
        for other, candidate in reversed(self.entries.items()):
            other_artist, other_title, other_album = other.split("\n", 2)
            if normalize(album) == normalize(other_album) and same_track(
                title, artist, other_title, other_artist
            ):
                self.entries.move_to_end(other)
                return candidate
        return None

    def to_data(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "tracks": {
                key: {
                    "info": asdict(entry.info),
                    "cover": None if entry.cover is None else asdict(entry.cover),
                }
                for key, entry in self.entries.items()
            },
        }

    def load(self, data: Any) -> None:
        """Replace the entries with stored ones from the same code version;
        anything malformed is dropped."""
        self.entries.clear()
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
            self.put(key, entry)


class CacheStorage(Protocol):
    async def async_load(self) -> Any: ...

    def async_delay_save(self, data_func: Any, delay: float = 0) -> None: ...


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
