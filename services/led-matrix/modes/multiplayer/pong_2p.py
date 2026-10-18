"""Two-Player Competitive Pong for 64x64 LED Matrix."""

import random

from PIL import ImageDraw

from .base import MultiplayerGame

# Compact 3x5 bitmap numbers for score rendering
NUMS = {
    0: ["111", "101", "101", "101", "111"],
    1: ["010", "110", "010", "010", "111"],
    2: ["111", "001", "111", "100", "111"],
    3: ["111", "001", "111", "001", "111"],
    4: ["101", "101", "111", "001", "001"],
    5: ["111", "100", "111", "001", "111"],
    6: ["111", "100", "111", "101", "111"],
    7: ["111", "001", "010", "010", "010"],
    8: ["111", "101", "111", "101", "111"],
    9: ["111", "101", "111", "001", "111"],
}


class Pong2P(MultiplayerGame):
    """2-Player real-time competitive Pong."""

    def __init__(self, solo_vs_ai: bool = False, winning_score: int = 5) -> None:
        super().__init__(solo_vs_ai=solo_vs_ai)
        self.winning_score = winning_score
        self.paddle_h = 12
        self.paddle_w = 2

        self.p1_score = 0
        self.p2_score = 0

        self.p1_y = 26.0
        self.p2_y = 26.0

        self.ball_x = 31.0
        self.ball_y = 31.0
        self.ball_vx = 36.0
        self.ball_vy = 18.0

        self.serve_delay = 0.8
        self.particles: list[tuple[float, float, float, float, tuple[int, int, int], float]] = []
        self.trail: list[tuple[float, float]] = []

        self.reset()

    def reset(self) -> None:
        self.winner = 0
        self.p1_score = 0
        self.p2_score = 0
        self.p1_y = 26.0
        self.p2_y = 26.0
        self.reset_ball(serving_to=1 if random.random() < 0.5 else 2)

    def reset_ball(self, serving_to: int) -> None:
        self.ball_x = 31.0
        self.ball_y = 31.0
        speed = 36.0
        angle = random.uniform(-0.6, 0.6)
        direction = 1.0 if serving_to == 2 else -1.0
        self.ball_vx = speed * direction
        self.ball_vy = speed * angle
        self.serve_delay = 0.8
        self.trail.clear()

    def update(
        self,
        dt: float,
        p1_inputs: set[str],
        p2_inputs: set[str],
        p1_events: list[str] | None = None,
        p2_events: list[str] | None = None,
    ) -> None:
        # Update sparks
        new_particles = []
        for px, py, pvx, pvy, color, life in self.particles:
            life -= dt
            if life > 0:
                new_particles.append((px + pvx * dt, py + pvy * dt, pvx, pvy, color, life))
        self.particles = new_particles

        if self.is_game_over():
            return

        # Paddle speeds
        paddle_speed = 52.0

        # Player 1 controls (Up/Down)
        if "up" in p1_inputs:
            self.p1_y -= paddle_speed * dt
        if "down" in p1_inputs:
            self.p1_y += paddle_speed * dt
        self.p1_y = max(1.0, min(63.0 - self.paddle_h, self.p1_y))

        # Player 2 controls
        if self.solo_vs_ai:
            ai_speed = 42.0
            target_y = self.ball_y - self.paddle_h / 2.0
            if self.p2_y < target_y - 2.0:
                self.p2_y += ai_speed * dt
            elif self.p2_y > target_y + 2.0:
                self.p2_y -= ai_speed * dt
        else:
            if "up" in p2_inputs:
                self.p2_y -= paddle_speed * dt
            if "down" in p2_inputs:
                self.p2_y += paddle_speed * dt
        self.p2_y = max(1.0, min(63.0 - self.paddle_h, self.p2_y))

        # Serve pause
        if self.serve_delay > 0:
            self.serve_delay -= dt
            return

        # Motion trail
        self.trail.append((self.ball_x, self.ball_y))
        if len(self.trail) > 3:
            self.trail.pop(0)

        # Ball movement
        self.ball_x += self.ball_vx * dt
        self.ball_y += self.ball_vy * dt

        # Bounce off top/bottom walls
        if self.ball_y <= 1.0:
            self.ball_y = 1.0
            self.ball_vy = abs(self.ball_vy)
            self._spawn_sparks(self.ball_x, self.ball_y, (120, 120, 255), count=4)
            self.play_sfx("wall")
        elif self.ball_y >= 61.0:
            self.ball_y = 61.0
            self.ball_vy = -abs(self.ball_vy)
            self._spawn_sparks(self.ball_x, self.ball_y, (120, 120, 255), count=4)
            self.play_sfx("wall")

        # P1 paddle collision (x=2..4)
        if self.ball_x <= 4.0 and self.ball_vx < 0:
            if self.p1_y - 2.0 <= self.ball_y <= self.p1_y + self.paddle_h + 1.0:
                self.ball_x = 4.0
                speed = min(80.0, (self.ball_vx**2 + self.ball_vy**2) ** 0.5 * 1.06)
                offset = (self.ball_y - (self.p1_y + self.paddle_h / 2.0)) / (self.paddle_h / 2.0)
                offset = max(-1.0, min(1.0, offset))
                self.ball_vx = speed * (1.0 - 0.3 * abs(offset))
                self.ball_vy = speed * offset * 0.8
                self._spawn_sparks(4.0, self.ball_y, (0, 229, 255), count=7)
                self.play_sfx("paddle")

        # P2 paddle collision (x=59..61)
        if self.ball_x >= 58.0 and self.ball_vx > 0:
            if self.p2_y - 2.0 <= self.ball_y <= self.p2_y + self.paddle_h + 1.0:
                self.ball_x = 58.0
                speed = min(80.0, (self.ball_vx**2 + self.ball_vy**2) ** 0.5 * 1.06)
                offset = (self.ball_y - (self.p2_y + self.paddle_h / 2.0)) / (self.paddle_h / 2.0)
                offset = max(-1.0, min(1.0, offset))
                self.ball_vx = -speed * (1.0 - 0.3 * abs(offset))
                self.ball_vy = speed * offset * 0.8
                self._spawn_sparks(59.0, self.ball_y, (255, 145, 0), count=7)
                self.play_sfx("paddle")

        # Goal scored
        if self.ball_x < -2.0:
            # P2 scores
            self.p2_score += 1
            self._spawn_sparks(2.0, self.ball_y, (255, 145, 0), count=16)
            if self.p2_score >= self.winning_score:
                self.winner = 2
                self.play_sfx("game_over")
            else:
                self.play_sfx("score")
                self.reset_ball(serving_to=1)
        elif self.ball_x > 66.0:
            # P1 scores
            self.p1_score += 1
            self._spawn_sparks(62.0, self.ball_y, (0, 229, 255), count=16)
            if self.p1_score >= self.winning_score:
                self.winner = 1
                self.play_sfx("game_over")
            else:
                self.play_sfx("score")
                self.reset_ball(serving_to=2)

    def get_final_score(self) -> int:
        return self.p1_score


    def _spawn_sparks(self, x: float, y: float, color: tuple[int, int, int], count: int) -> None:
        for _ in range(count):
            speed = random.uniform(15.0, 50.0)
            pvx = speed * random.uniform(-1.0, 1.0)
            pvy = speed * random.uniform(-1.0, 1.0)
            life = random.uniform(0.2, 0.45)
            self.particles.append((x, y, pvx, pvy, color, life))

    def render(self, draw: ImageDraw.ImageDraw) -> None:
        # 1. Center dashed net
        for y in range(0, 64, 4):
            draw.line([(31, y), (31, y + 2)], fill=(30, 30, 55))

        # 2. Score display at top
        self._draw_digit(draw, self.p1_score, 24, 2, (0, 229, 255))
        self._draw_digit(draw, self.p2_score, 37, 2, (255, 145, 0))

        # 3. Paddles
        p1_top = int(self.p1_y)
        draw.rectangle([2, p1_top, 3, p1_top + self.paddle_h], fill=(0, 229, 255))

        p2_top = int(self.p2_y)
        draw.rectangle([60, p2_top, 61, p2_top + self.paddle_h], fill=(255, 145, 0))

        # 4. Ball motion trail
        for i, (tx, ty) in enumerate(self.trail):
            alpha_frac = (i + 1) / (len(self.trail) + 1)
            trail_col = (int(80 * alpha_frac), int(120 * alpha_frac), int(200 * alpha_frac))
            draw.point((int(tx), int(ty)), fill=trail_col)

        # 5. Ball
        bx = int(self.ball_x)
        by = int(self.ball_y)
        draw.rectangle([bx, by, bx + 1, by + 1], fill=(255, 255, 255))

        # 6. Sparks
        for px, py, _, _, color, _ in self.particles:
            if 0 <= px < 64 and 0 <= py < 64:
                draw.point((int(px), int(py)), fill=color)

    def _draw_digit(self, draw: ImageDraw.ImageDraw, val: int, x: int, y: int, color: tuple[int, int, int]) -> None:
        grid = NUMS.get(val, NUMS[0])
        for r, row in enumerate(grid):
            for c, char in enumerate(row):
                if char == "1":
                    draw.point((x + c, y + r), fill=color)

    def get_status_text(self) -> str:
        return f"P1: {self.p1_score}  |  P2: {self.p2_score}"
