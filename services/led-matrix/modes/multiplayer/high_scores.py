"""Persistent High Score Manager for LED Matrix Arcade."""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger("arcade_high_scores")

DEFAULT_SCORES: dict[str, int] = {
    "tetris": 0,
    "snake": 0,
    "breakout": 0,
    "pong": 0,
    "tron": 0,
}


class HighScoreManager:
    """Thread-safe persistent storage for arcade personal bests and high scores."""

    def __init__(self, primary_path: str | None = None, fallback_path: str | None = None) -> None:
        self.primary_path = primary_path or os.environ.get(
            "ARCADE_HIGH_SCORES_FILE", "/data/arcade_high_scores.json"
        )
        if primary_path is not None and fallback_path is None:
            self.fallback_path = f"{self.primary_path}.fallback"
        else:
            self.fallback_path = fallback_path or "/tmp/arcade_high_scores.json"
        self.active_path = self.primary_path
        self._lock = threading.Lock()
        self.scores: dict[str, int] = dict(DEFAULT_SCORES)
        self._load()

    def _load(self) -> None:
        for path in (self.primary_path, self.fallback_path):
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    for k, v in data.items():
                        if isinstance(v, int):
                            self.scores[k] = v
                    self.active_path = path
                    logger.info("Loaded arcade high scores from %s: %s", path, self.scores)
                    return
            except Exception as e:
                logger.warning("Failed to load high scores from %s: %s", path, e)

        # Neither file exists yet; choose writable path
        self.active_path = self._determine_writable_path()
        logger.info("Initialized high scores with defaults. Active path: %s", self.active_path)

    def _determine_writable_path(self) -> str:
        primary_dir = os.path.dirname(self.primary_path) or "."
        try:
            os.makedirs(primary_dir, exist_ok=True)
            test_file = os.path.join(primary_dir, ".write_test")
            with open(test_file, "w") as f:
                f.write("1")
            os.remove(test_file)
            return self.primary_path
        except (OSError, PermissionError):
            return self.fallback_path

    def _save_unlocked(self) -> None:
        targets = [self.active_path]
        if self.active_path != self.fallback_path:
            targets.append(self.fallback_path)

        for target in targets:
            try:
                target_dir = os.path.dirname(target) or "."
                os.makedirs(target_dir, exist_ok=True)
                tmp_target = f"{target}.tmp"
                with open(tmp_target, "w", encoding="utf-8") as f:
                    json.dump(self.scores, f, indent=2)
                os.replace(tmp_target, target)
                self.active_path = target
                return
            except (OSError, PermissionError) as e:
                logger.warning("Failed writing high scores to %s (%s); trying fallback", target, e)
                continue

    def get_score(self, game_id: str) -> int:
        with self._lock:
            return self.scores.get(game_id, 0)

    def get_all(self) -> dict[str, int]:
        with self._lock:
            return dict(self.scores)

    def update_score(self, game_id: str, score: int) -> tuple[bool, int]:
        """Update high score if new score is higher. Returns (is_new_high, current_high)."""
        with self._lock:
            current = self.scores.get(game_id, 0)
            if score > current:
                self.scores[game_id] = score
                self._save_unlocked()
                logger.info("NEW HIGH SCORE for %s: %d (previous: %d)", game_id, score, current)
                return True, score
            return False, current
