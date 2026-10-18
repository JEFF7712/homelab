"""Auto-discovered Game Registry for LED Matrix Arcade."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from PIL import ImageDraw
    from .base import MultiplayerGame

logger = logging.getLogger("arcade_registry")

NON_GAME_MODULES = {
    "arcade_renderer",
    "base",
    "carousel",
    "high_scores",
    "lobby",
    "registry",
    "__init__",
}


@dataclass
class GameSpec:
    """Metadata specification and factory entry for an arcade game."""

    id: str
    name: str
    short_title: str
    badge: str
    desc: str
    supports_2p: bool
    color: tuple[int, int, int]
    game_cls: type[MultiplayerGame]
    icon_fn: Callable[[ImageDraw.ImageDraw, int, int], None] | None = None
    order: int = 100

    def to_config_dict(self) -> dict[str, Any]:
        """Format matching the JSON payload expected by mobile web controllers."""
        return {
            "id": self.id,
            "name": self.name,
            "badge": self.badge,
            "desc": self.desc,
            "supports_2p": self.supports_2p,
        }

    def to_carousel_item(self) -> dict[str, Any]:
        """Format matching the carousel renderer items."""
        return {
            "id": self.id,
            "title": self.name,
            "short_title": self.short_title,
            "badge": self.badge,
            "color": self.color,
            "icon_fn": self.icon_fn,
        }


class GameRegistry:
    """Registry coordinating game definitions, metadata, and factories."""

    def __init__(self) -> None:
        self._games: dict[str, GameSpec] = {}
        self._discovered = False

    def _ensure_discovered(self) -> None:
        if self._discovered:
            return
        self._discovered = True
        import importlib
        import pkgutil
        try:
            import modes.multiplayer as mp_pkg
            for _, module_name, is_pkg in pkgutil.iter_modules(mp_pkg.__path__):
                if not is_pkg and module_name not in NON_GAME_MODULES:
                    try:
                        importlib.import_module(f"modes.multiplayer.{module_name}")
                    except Exception as e:
                        logger.warning("Failed to auto-discover game module %s: %s", module_name, e)
        except Exception as e:
            logger.warning("Error auto-discovering games: %s", e)

    def register(self, spec: GameSpec) -> None:
        self._games[spec.id] = spec
        logger.debug("Registered arcade game: %s (%s)", spec.id, spec.name)

    def get(self, game_id: str) -> GameSpec | None:
        self._ensure_discovered()
        return self._games.get(game_id)

    def list_games(self) -> list[GameSpec]:
        self._ensure_discovered()
        return sorted(self._games.values(), key=lambda g: (g.order, g.id))

    def get_games_config(self) -> list[dict[str, Any]]:
        return [g.to_config_dict() for g in self.list_games()]

    def get_carousel_items(self) -> list[dict[str, Any]]:
        return [g.to_carousel_item() for g in self.list_games()]

    def create_game(self, game_id: str, solo: bool = False) -> MultiplayerGame:
        self._ensure_discovered()
        spec = self._games.get(game_id)
        if not spec:
            games = self.list_games()
            if games:
                spec = games[0]
            else:
                raise ValueError("No games registered in GameRegistry")

        cls = spec.game_cls
        if spec.supports_2p:
            return cls(solo_vs_ai=solo)
        return cls()


GAME_REGISTRY = GameRegistry()


def register_game(
    game_id: str,
    name: str,
    short_title: str,
    badge: str,
    desc: str,
    supports_2p: bool,
    color: tuple[int, int, int],
    icon_fn: Callable[[ImageDraw.ImageDraw, int, int], None] | None = None,
    order: int = 100,
) -> Callable[[type[MultiplayerGame]], type[MultiplayerGame]]:
    """Class decorator registering an arcade game with the central GAME_REGISTRY."""

    def decorator(cls: type[MultiplayerGame]) -> type[MultiplayerGame]:
        actual_icon_fn = icon_fn or getattr(cls, "draw_icon", None)
        spec = GameSpec(
            id=game_id,
            name=name,
            short_title=short_title,
            badge=badge,
            desc=desc,
            supports_2p=supports_2p,
            color=color,
            game_cls=cls,
            icon_fn=actual_icon_fn,
            order=order,
        )
        GAME_REGISTRY.register(spec)
        return cls

    return decorator
