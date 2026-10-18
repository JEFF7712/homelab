"""Multiplayer arcade package for LED Matrix."""

from .air_hockey_game import AirHockeyGame
from .arcade_renderer import MultiplayerArcadeRenderer
from .asteroids_game import AsteroidsGame
from .base import MultiplayerGame
from .breakout_game import InteractiveBreakout
from .carousel import CarouselRenderer
from .flappy_game import FlappyBirdGame
from .game_2048 import Game2048
from .high_scores import HighScoreManager
from .lobby import LobbyRenderer
from .pong_2p import Pong2P
from .registry import GAME_REGISTRY, GameSpec, register_game
from .snake_game import InteractiveSnake
from .tetris_game import InteractiveTetris
from .tron_2p import Tron2P

__all__ = [
    "AirHockeyGame",
    "AsteroidsGame",
    "CarouselRenderer",
    "FlappyBirdGame",
    "GAME_REGISTRY",
    "Game2048",
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


