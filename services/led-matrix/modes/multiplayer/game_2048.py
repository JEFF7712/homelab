"""Interactive 2048 sliding-tile puzzle game for 64x64 LED Matrix."""

from __future__ import annotations

import random
from PIL import ImageDraw, ImageFont
from modes.text_utils import draw_boxed_message

from .base import MultiplayerGame
from .registry import register_game


# Crisp 3x5 pixel bitmap font for rendering numbers in small 12x12 LED matrix tiles
# Each character is defined by 5 rows of 3 bits (1 = pixel on, 0 = pixel off)
DIGITS_3x5: dict[str, list[int]] = {
    "0": [0b111, 0b101, 0b101, 0b101, 0b111],
    "1": [0b010, 0b110, 0b010, 0b010, 0b111],
    "2": [0b111, 0b001, 0b111, 0b100, 0b111],
    "3": [0b111, 0b001, 0b111, 0b001, 0b111],
    "4": [0b101, 0b101, 0b111, 0b001, 0b001],
    "5": [0b111, 0b100, 0b111, 0b001, 0b111],
    "6": [0b111, 0b100, 0b111, 0b101, 0b111],
    "7": [0b111, 0b001, 0b010, 0b010, 0b010],
    "8": [0b111, 0b101, 0b111, 0b101, 0b111],
    "9": [0b111, 0b101, 0b111, 0b001, 0b111],
    "k": [0b101, 0b110, 0b110, 0b101, 0b101],
}

TILE_COLORS: dict[int, tuple[int, int, int]] = {
    0: (22, 22, 30),
    2: (238, 228, 218),
    4: (237, 224, 200),
    8: (242, 177, 121),
    16: (245, 149, 99),
    32: (246, 124, 95),
    64: (246, 94, 59),
    128: (237, 207, 114),
    256: (237, 204, 97),
    512: (237, 200, 80),
    1024: (237, 197, 63),
    2048: (237, 194, 46),
}


@register_game(
    game_id="2048",
    name="2048",
    short_title="2048",
    badge="1P SOLO",
    desc="Slide and combine matching tiles to reach 2048",
    supports_2p=False,
    color=(237, 194, 46),
    order=9,
)
class Game2048(MultiplayerGame):
    """Playable 4x4 2048 puzzle engine optimized for 64x64 matrix."""

    GRID_SIZE = 4
    TILE_SIZE = 12
    GAP = 1
    OFFSET_X = 6
    OFFSET_Y = 10

    @staticmethod
    def draw_icon(draw: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
        # 2x2 grid representing 2048 tiles
        draw.rectangle([cx - 7, cy - 7, cx - 1, cy - 1], fill=(238, 228, 218))
        draw.rectangle([cx + 1, cy - 7, cx + 7, cy - 1], fill=(237, 224, 200))
        draw.rectangle([cx - 7, cy + 1, cx - 1, cy + 7], fill=(242, 177, 121))
        draw.rectangle([cx + 1, cy + 1, cx + 7, cy + 7], fill=(237, 194, 46))

    def __init__(self) -> None:
        super().__init__(solo_vs_ai=True)
        self.font = ImageFont.load_default(size=9)
        self.grid: list[list[int]] = [[0] * 4 for _ in range(4)]
        self.score = 0
        self.max_tile = 2
        self._prev_inputs: set[str] = set()
        self._move_cooldown = 0.0
        self.reset()

    def reset(self) -> None:
        self.winner = 0
        self.score = 0
        self.max_tile = 2
        self.grid = [[0] * 4 for _ in range(4)]
        self._prev_inputs.clear()
        self._move_cooldown = 0.0
        self._spawn_tile()
        self._spawn_tile()

    def _spawn_tile(self) -> bool:
        empty = [(r, c) for r in range(4) for c in range(4) if self.grid[r][c] == 0]
        if not empty:
            return False
        r, c = random.choice(empty)
        val = 4 if random.random() < 0.1 else 2
        self.grid[r][c] = val
        self.max_tile = max(self.max_tile, val)
        return True

    def get_final_score(self) -> int:
        return self.score

    def get_status_text(self) -> str:
        return f"SCORE: {self.score}  MAX: {self.max_tile}"

    def update(
        self,
        dt: float,
        p1_inputs: set[str],
        p2_inputs: set[str],
        p1_events: list[str] | None = None,
        p2_events: list[str] | None = None,
    ) -> None:
        if self.is_game_over() or dt <= 0:
            return

        if self._move_cooldown > 0:
            self._move_cooldown = max(0.0, self._move_cooldown - dt)

        # Detect directional inputs (edge-triggered or via event)
        direction: str | None = None
        for d in ("up", "down", "left", "right"):
            if p1_events and d in p1_events:
                direction = d
                break
            elif d in p1_inputs and d not in self._prev_inputs and self._move_cooldown <= 0:
                direction = d
                break

        self._prev_inputs = set(p1_inputs)

        if not direction:
            return

        moved, earned, big_merge = self._apply_move(direction)
        if moved:
            self.score += earned
            self._move_cooldown = 0.12
            if big_merge:
                self.play_sfx("laser")
            else:
                self.play_sfx("paddle")

            self._spawn_tile()

            # Check if game over (no empty spaces and no valid merges)
            if self._check_game_over():
                self.winner = 1
                self.play_sfx("game_over")

    def _slide_row_left(self, row: list[int]) -> tuple[list[int], int, bool]:
        """Compress and merge a single 1D row to the left."""
        non_zero = [v for v in row if v != 0]
        merged: list[int] = []
        skip = False
        earned = 0
        big_merge = False

        for i in range(len(non_zero)):
            if skip:
                skip = False
                continue
            if i + 1 < len(non_zero) and non_zero[i] == non_zero[i + 1]:
                new_val = non_zero[i] * 2
                merged.append(new_val)
                earned += new_val
                self.max_tile = max(self.max_tile, new_val)
                if new_val >= 128:
                    big_merge = True
                skip = True
            else:
                merged.append(non_zero[i])

        merged.extend([0] * (4 - len(merged)))
        return merged, earned, big_merge

    def _apply_move(self, direction: str) -> tuple[bool, int, bool]:
        """Perform board transformation, slide, and merge."""
        new_grid = [row[:] for row in self.grid]
        total_earned = 0
        any_big = False

        if direction == "left":
            for r in range(4):
                new_grid[r], earned, big = self._slide_row_left(new_grid[r])
                total_earned += earned
                any_big = any_big or big
        elif direction == "right":
            for r in range(4):
                reversed_row = new_grid[r][::-1]
                slid, earned, big = self._slide_row_left(reversed_row)
                new_grid[r] = slid[::-1]
                total_earned += earned
                any_big = any_big or big
        elif direction == "up":
            for c in range(4):
                col = [new_grid[r][c] for r in range(4)]
                slid, earned, big = self._slide_row_left(col)
                for r in range(4):
                    new_grid[r][c] = slid[r]
                total_earned += earned
                any_big = any_big or big
        elif direction == "down":
            for c in range(4):
                col = [new_grid[r][c] for r in range(4)][::-1]
                slid, earned, big = self._slide_row_left(col)
                slid = slid[::-1]
                for r in range(4):
                    new_grid[r][c] = slid[r]
                total_earned += earned
                any_big = any_big or big

        moved = new_grid != self.grid
        if moved:
            self.grid = new_grid

        return moved, total_earned, any_big

    def _check_game_over(self) -> bool:
        # Check any empty tile
        for r in range(4):
            for c in range(4):
                if self.grid[r][c] == 0:
                    return False

        # Check adjacent matching tiles
        for r in range(4):
            for c in range(4):
                val = self.grid[r][c]
                if r + 1 < 4 and self.grid[r + 1][c] == val:
                    return False
                if c + 1 < 4 and self.grid[r][c + 1] == val:
                    return False
        return True

    def _draw_3x5_text(self, draw: ImageDraw.ImageDraw, text: str, start_x: int, start_y: int, color: tuple[int, int, int]) -> None:
        """Render a string using the crisp 3x5 pixel font."""
        cur_x = start_x
        for ch in text:
            bitmap = DIGITS_3x5.get(ch)
            if bitmap:
                for row_idx, bits in enumerate(bitmap):
                    for col_idx in range(3):
                        if (bits >> (2 - col_idx)) & 1:
                            draw.point((cur_x + col_idx, start_y + row_idx), fill=color)
            cur_x += 4  # 3 pixels char + 1 pixel tracking

    def render(self, draw: ImageDraw.ImageDraw) -> None:
        draw.rectangle([0, 0, 63, 63], fill=(12, 12, 16))

        # Top score HUD
        draw.text((self.OFFSET_X, 0), f"SCORE {self.score}", fill=(240, 220, 180), font=self.font)

        # 4x4 Grid Tiles
        for r in range(4):
            for c in range(4):
                val = self.grid[r][c]
                x1 = self.OFFSET_X + c * (self.TILE_SIZE + self.GAP)
                y1 = self.OFFSET_Y + r * (self.TILE_SIZE + self.GAP)
                x2 = x1 + self.TILE_SIZE - 1
                y2 = y1 + self.TILE_SIZE - 1

                tile_color = TILE_COLORS.get(val, (237, 194, 46))
                draw.rectangle([x1, y1, x2, y2], fill=tile_color)

                if val > 0:
                    # Choose text representation and color
                    text_color = (119, 110, 101) if val in (2, 4) else (255, 255, 255)
                    if val >= 1024:
                        display_str = f"{val // 1000}k"
                    else:
                        display_str = str(val)

                    # Center the 3x5 text in the 12x12 tile
                    # Width = len * 4 - 1
                    total_w = len(display_str) * 4 - 1
                    text_x = x1 + max(0, (self.TILE_SIZE - total_w) // 2)
                    text_y = y1 + max(0, (self.TILE_SIZE - 5) // 2)
                    self._draw_3x5_text(draw, display_str, text_x, text_y, text_color)

        # Game Over Banner
        if self.is_game_over():
            draw_boxed_message(
                draw,
                title="GAME OVER",
                rect=(4, 23, 59, 41),
                bg_color=(10, 10, 16),
                border_color=(237, 194, 46),
                title_color=(255, 80, 80),
            )
