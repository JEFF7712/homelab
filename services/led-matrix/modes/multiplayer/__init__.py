"""Multiplayer arcade package for LED Matrix."""

from .arcade_renderer import MultiplayerArcadeRenderer
from .base import MultiplayerGame
from .breakout_game import InteractiveBreakout
from .carousel import CarouselRenderer
from .high_scores import HighScoreManager
from .lobby import LobbyRenderer
from .pong_2p import Pong2P
from .registry import GAME_REGISTRY, GameSpec, register_game
from .snake_game import InteractiveSnake
from .tetris_game import InteractiveTetris
from .tron_2p import Tron2P

__all__ = [
    "CarouselRenderer",
    "GAME_REGISTRY",
    "GameSpec",
    "HighScoreManager",
    "InteractiveBreakout",
    "InteractiveSnake",
    "InteractiveTetris",
    "LobbyRenderer",
    "MultiplayerArcadeRenderer",
    "MultiplayerGame",
    "Pong2P",
    "Tron2P",
    "register_game",
]


