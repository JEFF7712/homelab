"""Interactive Tetris arcade game for 64x64 LED Matrix."""

from __future__ import annotations

import random

from PIL import ImageDraw, ImageFont

from .base import MultiplayerGame

COLS = 10
ROWS = 17
CELL = 3
BOARD_W = COLS * CELL
BOARD_H = ROWS * CELL
ORIGIN_X = (64 - BOARD_W) // 2
ORIGIN_Y = 11

SHAPES: dict[str, list[list[int]]] = {
    "I": [[0, 0, 0, 0], [1, 1, 1, 1], [0, 0, 0, 0], [0, 0, 0, 0]],
    "O": [[1, 1], [1, 1]],
    "T": [[0, 1, 0], [1, 1, 1], [0, 0, 0]],
    "S": [[0, 1, 1], [1, 1, 0], [0, 0, 0]],
    "Z": [[1, 1, 0], [0, 1, 1], [0, 0, 0]],
    "J": [[1, 0, 0], [1, 1, 1], [0, 0, 0]],
    "L": [[0, 0, 1], [1, 1, 1], [0, 0, 0]],
}

COLORS: dict[str, tuple[int, int, int]] = {
    "I": (60, 220, 230),
    "O": (240, 210, 60),
    "T": (180, 90, 230),
    "S": (90, 220, 110),
    "Z": (240, 80, 90),
    "J": (70, 120, 240),
    "L": (240, 150, 60),
}


def _rotate(matrix: list[list[int]]) -> list[list[int]]:
    n = len(matrix)
    return [[matrix[n - 1 - j][i] for j in range(n)] for i in range(n)]


def _cells(matrix: list[list[int]]) -> list[tuple[int, int]]:
    return [
        (x, y) for y, row in enumerate(matrix) for x, value in enumerate(row) if value
    ]


class InteractiveTetris(MultiplayerGame):
    """Playable Tetris block puzzle controlled via mobile gamepad."""

    def __init__(self) -> None:
        super().__init__(solo_vs_ai=True)
        self.font = ImageFont.load_default(size=9)
        self.board: list[list[str | None]] = [[None] * COLS for _ in range(ROWS)]
        self.score = 0
        self.lines = 0
        self.fall_timer = 0.0
        self.fall_interval = 0.45
        self.piece_name = "T"
        self.piece_matrix: list[list[int]] = []
        self.piece_x = 0
        self.piece_y = 0
        self.next_piece_name = "I"
        self.clearing_rows: list[int] = []
        self.clear_timer = 0.0
        self.reset()

    def reset(self) -> None:
        self.winner = 0
        self.board = [[None] * COLS for _ in range(ROWS)]
        self.score = 0
        self.lines = 0
        self.fall_timer = 0.0
        self.clearing_rows.clear()
        self.clear_timer = 0.0
        self.next_piece_name = random.choice(list(SHAPES))
        self._spawn()

    def _spawn(self) -> bool:
        self.piece_name = self.next_piece_name
        self.next_piece_name = random.choice(list(SHAPES))
        self.piece_matrix = [row[:] for row in SHAPES[self.piece_name]]
        w = len(self.piece_matrix[0])
        self.piece_x = (COLS - w) // 2
        self.piece_y = 0

        # If spawn position collides immediately, game over!
        if self._collide(self.piece_matrix, self.piece_x, self.piece_y):
            self.winner = 1
            return False
        return True

    def _collide(self, matrix: list[list[int]], px: int, py: int) -> bool:
        for cx, cy in _cells(matrix):
            x = px + cx
            y = py + cy
            if x < 0 or x >= COLS or y >= ROWS:
                return True
            if y >= 0 and self.board[y][x] is not None:
                return True
        return False

    def _lock_piece(self) -> None:
        for cx, cy in _cells(self.piece_matrix):
            x = self.piece_x + cx
            y = self.piece_y + cy
            if 0 <= y < ROWS and 0 <= x < COLS:
                self.board[y][x] = self.piece_name

        # Check line clears
        cleared = []
        for y in range(ROWS):
            if all(self.board[y][x] is not None for x in range(COLS)):
                cleared.append(y)

        if cleared:
            self.clearing_rows = cleared
            self.clear_timer = 0.2
            count = len(cleared)
            self.lines += count
            pts = {1: 100, 2: 300, 3: 500, 4: 800}.get(count, count * 200)
            self.score += pts
        else:
            self._spawn()

    def update(
        self,
        dt: float,
        p1_inputs: set[str],
        p2_inputs: set[str],
        p1_events: list[str] | None = None,
        p2_events: list[str] | None = None,
    ) -> None:
        if self.is_game_over():
            return

        if self.clear_timer > 0:
            self.clear_timer -= dt
            if self.clear_timer <= 0:
                cleared_count = len(self.clearing_rows)
                remaining = [
                    row
                    for y, row in enumerate(self.board)
                    if y not in self.clearing_rows
                ]
                self.board = [[None] * COLS for _ in range(cleared_count)] + remaining
                self.clearing_rows.clear()
                self._spawn()
            return

        # Handle discrete player inputs
        if p1_events:
            for ev in p1_events:
                if ev == "left":
                    if not self._collide(
                        self.piece_matrix, self.piece_x - 1, self.piece_y
                    ):
                        self.piece_x -= 1
                elif ev == "right":
                    if not self._collide(
                        self.piece_matrix, self.piece_x + 1, self.piece_y
                    ):
                        self.piece_x += 1
                elif ev in ("up", "rotate", "action_a"):
                    rotated = _rotate(self.piece_matrix)
                    if not self._collide(rotated, self.piece_x, self.piece_y):
                        self.piece_matrix = rotated
                    elif not self._collide(rotated, self.piece_x - 1, self.piece_y):
                        self.piece_x -= 1
                        self.piece_matrix = rotated
                    elif not self._collide(rotated, self.piece_x + 1, self.piece_y):
                        self.piece_x += 1
                        self.piece_matrix = rotated
                elif ev in ("drop", "hard_drop", "action_b"):
                    # Hard drop
                    while not self._collide(
                        self.piece_matrix, self.piece_x, self.piece_y + 1
                    ):
                        self.piece_y += 1
                    self.score += 4
                    self._lock_piece()
                    return
                elif ev == "down" and not self._collide(
                    self.piece_matrix, self.piece_x, self.piece_y + 1
                ):
                    # Soft drop
                    self.piece_y += 1
                    self.score += 1

        # Fall timer (accelerates with lines cleared)
        speed = max(0.12, self.fall_interval - (self.lines // 5) * 0.04)
        if "down" in p1_inputs:
            speed = 0.06  # Fast soft-drop when holding down

        self.fall_timer += dt
        if self.fall_timer >= speed:
            self.fall_timer = 0.0
            if not self._collide(self.piece_matrix, self.piece_x, self.piece_y + 1):
                self.piece_y += 1
            else:
                self._lock_piece()

    def render(self, draw: ImageDraw.ImageDraw) -> None:
        # Background
        draw.rectangle([0, 0, 63, 63], fill=(6, 8, 16))

        # Board border
        bx0 = ORIGIN_X - 1
        by0 = ORIGIN_Y - 1
        bx1 = ORIGIN_X + BOARD_W
        by1 = ORIGIN_Y + BOARD_H
        draw.rectangle([bx0, by0, bx1, by1], outline=(35, 45, 70), width=1)

        # Draw placed blocks
        for y in range(ROWS):
            is_clearing = y in self.clearing_rows
            for x in range(COLS):
                cell = self.board[y][x]
                if cell:
                    color = (255, 255, 255) if is_clearing else COLORS[cell]
                    px = ORIGIN_X + x * CELL
                    py = ORIGIN_Y + y * CELL
                    draw.rectangle([px, py, px + CELL - 1, py + CELL - 1], fill=color)

        # Draw active piece
        if not self.clearing_rows:
            color = COLORS[self.piece_name]
            for cx, cy in _cells(self.piece_matrix):
                x = self.piece_x + cx
                y = self.piece_y + cy
                if 0 <= y < ROWS:
                    px = ORIGIN_X + x * CELL
                    py = ORIGIN_Y + y * CELL
                    draw.rectangle([px, py, px + CELL - 1, py + CELL - 1], fill=color)

        # Top score HUD
        draw.text((2, 0), f"{self.score}", fill=(180, 210, 255), font=self.font)

        # Next piece preview in top right
        next_color = COLORS[self.next_piece_name]
        next_mat = SHAPES[self.next_piece_name]
        for cx, cy in _cells(next_mat):
            px = 52 + cx * 2
            py = 14 + cy * 2
            draw.rectangle([px, py, px + 1, py + 1], fill=next_color)

    def get_status_text(self) -> str:
        return f"SCORE: {self.score} | LINES: {self.lines}"
