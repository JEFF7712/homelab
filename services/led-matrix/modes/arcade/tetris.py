"""Autonomous AI Tetris simulation on 64x64 RGB matrix."""

from __future__ import annotations

import random

from PIL import Image, ImageDraw, ImageFont

from .base import ArcadeGame

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


def _variants(name: str) -> list[list[tuple[int, int]]]:
    matrix = SHAPES[name]
    seen: set[tuple[tuple[int, int], ...]] = set()
    out: list[list[tuple[int, int]]] = []
    for _ in range(4):
        cells = _cells(matrix)
        min_x = min(c[0] for c in cells)
        min_y = min(c[1] for c in cells)
        norm = tuple(sorted((x - min_x, y - min_y) for x, y in cells))
        if norm not in seen:
            seen.add(norm)
            out.append(list(norm))
        matrix = _rotate(matrix)
    return out


VARIANTS: dict[str, list[list[tuple[int, int]]]] = {k: _variants(k) for k in SHAPES}


class TetrisGame(ArcadeGame):
    name = "TETRIS AI"

    def __init__(self) -> None:
        self.font = ImageFont.load_default(size=9)
        self.fall_interval = 0.09
        self.fall_timer = 0.0
        self.flash_timer = 0.0
        self.reset()

    def reset(self) -> None:
        self.board: list[list[str | None]] = [[None] * COLS for _ in range(ROWS)]
        self.score = 0
        self.lines = 0
        self.flash_timer = 0.0
        self.fall_timer = 0.0
        self.next_piece = random.choice(list(SHAPES))
        self._spawn()

    def _spawn(self) -> None:
        self.piece = self.next_piece
        self.next_piece = random.choice(list(SHAPES))
        base = VARIANTS[self.piece][0]
        self.piece_cells = list(base)
        self.piece_w = max(c[0] for c in base) + 1
        self.piece_x = (COLS - self.piece_w) // 2
        self.piece_y = 0
        variant, x = self._best_placement(self.piece)
        self.piece_cells = variant
        self.piece_x = x

    def _collide(self, cells: list[tuple[int, int]], px: int, py: int) -> bool:
        for cx, cy in cells:
            x = px + cx
            y = py + cy
            if x < 0 or x >= COLS or y >= ROWS:
                return True
            if y >= 0 and self.board[y][x] is not None:
                return True
        return False

    def _best_placement(self, name: str) -> tuple[list[tuple[int, int]], int]:
        best_score: float | None = None
        best: tuple[list[tuple[int, int]], int] = (VARIANTS[name][0], 0)
        for cells in VARIANTS[name]:
            width = max(c[0] for c in cells) + 1
            for px in range(COLS - width + 1):
                py = 0
                while not self._collide(cells, px, py + 1):
                    py += 1
                if self._collide(cells, px, py):
                    continue
                board = [row[:] for row in self.board]
                for cx, cy in cells:
                    if py + cy >= 0:
                        board[py + cy][px + cx] = name
                score = self._score_board(board)
                if best_score is None or score > best_score:
                    best_score = score
                    best = (list(cells), px)
        return best

    def _score_board(self, board: list[list[str | None]]) -> float:
        heights = [0] * COLS
        holes = 0
        for x in range(COLS):
            top = ROWS
            for y in range(ROWS):
                if board[y][x] is not None:
                    top = y
                    break
            heights[x] = ROWS - top if top < ROWS else 0
            seen = False
            for y in range(top, ROWS):
                if board[y][x] is not None:
                    seen = True
                elif seen:
                    holes += 1
        full = sum(1 for row in board if all(cell is not None for cell in row))
        agg = sum(heights)
        bumpiness = sum(abs(heights[i] - heights[i + 1]) for i in range(COLS - 1))
        return 0.76 * full - 0.51 * agg - 0.36 * holes - 0.18 * bumpiness

    def _lock(self) -> None:
        for cx, cy in self.piece_cells:
            y = self.piece_y + cy
            x = self.piece_x + cx
            if 0 <= y < ROWS and 0 <= x < COLS:
                self.board[y][x] = self.piece
        cleared = {y for y in range(ROWS) if all(self.board[y][x] for x in range(COLS))}
        if cleared:
            remaining = [row for y, row in enumerate(self.board) if y not in cleared]
            self.board = [[None] * COLS for _ in range(len(cleared))] + remaining
            self.lines += len(cleared)
            self.score += [0, 100, 300, 500, 800][len(cleared)]
            self.flash_timer = 0.18
        self._spawn()
        if self._collide(self.piece_cells, self.piece_x, self.piece_y):
            self.reset()

    def update(self, dt: float) -> None:
        dt = min(dt, 0.05)
        if self.flash_timer > 0:
            self.flash_timer -= dt
        self.fall_timer += dt
        if self.fall_timer < self.fall_interval:
            return
        self.fall_timer = 0.0
        if not self._collide(self.piece_cells, self.piece_x, self.piece_y + 1):
            self.piece_y += 1
        else:
            self._lock()

    def draw(
        self, frame: Image.Image, draw: ImageDraw.ImageDraw, size: int = 64
    ) -> None:
        draw.rectangle([(0, 0), (size - 1, size - 1)], fill=(4, 6, 12))

        draw.text((2, 0), f"S{self.score}", fill=(150, 190, 230), font=self.font)
        draw.text(
            (size - 20, 0), f"L{self.lines}", fill=(150, 190, 230), font=self.font
        )

        draw.rectangle(
            [
                (ORIGIN_X - 1, ORIGIN_Y - 1),
                (ORIGIN_X + BOARD_W, ORIGIN_Y + BOARD_H),
            ],
            outline=(40, 55, 80),
            width=1,
        )

        for y in range(ROWS):
            for x in range(COLS):
                name = self.board[y][x]
                if name is None:
                    continue
                px = ORIGIN_X + x * CELL
                py = ORIGIN_Y + y * CELL
                draw.rectangle(
                    [(px, py), (px + CELL - 1, py + CELL - 1)], fill=COLORS[name]
                )

        color = COLORS[self.piece]
        for cx, cy in self.piece_cells:
            y = self.piece_y + cy
            if y < 0:
                continue
            px = ORIGIN_X + (self.piece_x + cx) * CELL
            py = ORIGIN_Y + y * CELL
            draw.rectangle([(px, py), (px + CELL - 1, py + CELL - 1)], fill=color)

        preview = VARIANTS[self.next_piece][0]
        pw = max(c[0] for c in preview) + 1
        box_x = size - 2 - pw * 3
        box_y = 16
        for cx, cy in preview:
            px = box_x + cx * 3
            py = box_y + cy * 3
            draw.rectangle([(px, py), (px + 2, py + 2)], fill=COLORS[self.next_piece])

        if self.flash_timer > 0:
            draw.rectangle(
                [
                    (ORIGIN_X - 1, ORIGIN_Y - 1),
                    (ORIGIN_X + BOARD_W, ORIGIN_Y + BOARD_H),
                ],
                outline=(240, 240, 180),
                width=1,
            )
