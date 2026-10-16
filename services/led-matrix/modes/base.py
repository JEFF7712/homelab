"""Base definitions for LED Matrix display mode renderers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from PIL import Image


@dataclass(frozen=True)
class HAState:
    connected: bool = False
    power: bool = True
    mode: str = "Auto"
    brightness: float = 0.7
    weather_temp: float = 72.0
    weather_condition: str = "sunny"
    weather_unit: str = "°F"


@dataclass(frozen=True)
class PlaybackState:
    available: bool = False
    playing: bool = False
    title: str = ""
    artist: str = ""
    artwork_url: str = ""
    updated_at: float = 0.0


@dataclass
class RenderContext:
    tick: float
    dt: float
    ha_state: HAState
    spotify_state: PlaybackState
    spotify_art: Image.Image | None
    brightness: float = 0.7
    rotation_speed: float = 45.0


class ModeRenderer(ABC):
    @abstractmethod
    def render(self, ctx: RenderContext, size: int = 64) -> Image.Image:
        """Render a single frame for the mode."""
        ...
