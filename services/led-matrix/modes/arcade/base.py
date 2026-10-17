"""Base definitions for arcade games in the LED matrix attract mode."""

from __future__ import annotations

from abc import ABC, abstractmethod

from PIL import Image, ImageDraw


class ArcadeGame(ABC):
    name: str = "Arcade"

    @abstractmethod
    def reset(self) -> None:
        """Reset game state."""
        ...

    @abstractmethod
    def update(self, dt: float) -> None:
        """Update game simulation state given delta time in seconds."""
        ...

    @abstractmethod
    def draw(
        self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64
    ) -> None:
        """Render current game graphics onto the frame."""
        ...
