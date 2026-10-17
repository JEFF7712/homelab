"""Base class for 2-Player LED Matrix Arcade games."""

from abc import ABC, abstractmethod

from PIL import ImageDraw


class MultiplayerGame(ABC):
    """Abstract interface for a multiplayer game engine."""

    def __init__(self, solo_vs_ai: bool = False) -> None:
        self.solo_vs_ai = solo_vs_ai
        self.winner: int = 0  # 0: ongoing, 1: P1, 2: P2

    @abstractmethod
    def reset(self) -> None:
        """Reset the game state for a new round/match."""

    @abstractmethod
    def update(
        self,
        dt: float,
        p1_inputs: set[str],
        p2_inputs: set[str],
        p1_events: list[str] | None = None,
        p2_events: list[str] | None = None,
    ) -> None:
        """Update physics and logic for the current frame."""

    @abstractmethod
    def render(self, draw: ImageDraw.ImageDraw) -> None:
        """Render the game frame onto a 64x64 canvas."""

    @abstractmethod
    def get_status_text(self) -> str:
        """Return a compact status string for the mobile controller header."""

    def is_game_over(self) -> bool:
        return self.winner != 0
