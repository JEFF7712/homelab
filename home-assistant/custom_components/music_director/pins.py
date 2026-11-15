"""Album colour pins: song overrides set from the dashboard.

Pins live in Home Assistant storage, so they survive restarts and can be
changed or removed without a deploy. Colour overrides in
music_director.overrides seed the pins once each: after that the pin is the
source of truth, so removing a seeded pin from the dashboard sticks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from .metadata import SongOverride, find_override, normalize

STORAGE_KEY = "music_director.pins"
STORAGE_VERSION = 1


class PinStorage(Protocol):
    async def async_load(self) -> Any: ...

    def async_delay_save(self, data_func: Any, delay: float = 0) -> None: ...


def pin_key(pin: SongOverride) -> str:
    return "|".join(
        normalize(part or "") for part in (pin.artist, pin.album, pin.title)
    )


def primary_artist(artist: str) -> str:
    """The first credited artist: pins match the artist by prefix, so
    features and remixers on other tracks still match."""
    return artist.split(",")[0].strip()


@dataclass
class Pins:
    """Colour pins, plus the configured overrides already seeded."""

    pins: list[SongOverride] = field(default_factory=list)
    seeded: set[str] = field(default_factory=set)

    @classmethod
    def from_data(cls, data: Any) -> Pins:
        data = data if isinstance(data, dict) else {}
        pins = [
            SongOverride(**{k: v for k, v in item.items() if k in _FIELDS})
            for item in data.get("pins", [])
            if isinstance(item, dict) and item.get("color")
        ]
        return cls(pins, set(data.get("seeded", [])))

    def to_data(self) -> dict[str, Any]:
        return {
            "pins": [
                {k: v for k, v in asdict(pin).items() if v is not None}
                for pin in self.pins
            ],
            "seeded": sorted(self.seeded),
        }

    def seed(self, overrides: tuple[SongOverride, ...]) -> bool:
        """Add each configured colour override once. Returns True if any
        were added."""
        added = False
        for override in overrides:
            if not override.color:
                continue
            key = pin_key(override)
            if key in self.seeded:
                continue
            self.seeded.add(key)
            self.pins.append(
                SongOverride(
                    artist=override.artist,
                    album=override.album,
                    title=override.title,
                    color=override.color,
                )
            )
            added = True
        return added

    def find(self, title: str, artist: str, album: str) -> SongOverride | None:
        return find_override(tuple(self.pins), title, artist, album)

    def pin(self, title: str, artist: str, album: str, color: str) -> SongOverride:
        """Pin colour for the track's album (or the track, without an album),
        replacing any pin that matches it."""
        self.unpin(title, artist, album)
        pin = SongOverride(
            artist=primary_artist(artist) or None,
            album=album or None,
            title=None if album else title,
            color=color,
        )
        self.pins.append(pin)
        return pin

    def unpin(self, title: str, artist: str, album: str) -> bool:
        before = len(self.pins)
        self.pins = [p for p in self.pins if not p.matches(title, artist, album)]
        return len(self.pins) != before


_FIELDS = frozenset(("artist", "album", "title", "color"))


class PinStore:
    """Pins persisted through a Home Assistant Store."""

    def __init__(self, storage: PinStorage) -> None:
        self._storage = storage
        self.pins = Pins()

    async def async_load(self, overrides: tuple[SongOverride, ...]) -> None:
        self.pins = Pins.from_data(await self._storage.async_load())
        if self.pins.seed(overrides):
            self.save()

    def save(self) -> None:
        self._storage.async_delay_save(self.pins.to_data, 1)
