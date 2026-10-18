from __future__ import annotations

import random
import unittest

from modes.arcade import DoodleJumpGame, FroggerGame, QbertGame, RetroArcadeRenderer
from modes.text_utils import draw_boxed_message, draw_fitted_text, draw_scoreboard
from PIL import Image, ImageDraw


class TextBoundsTests(unittest.TestCase):
    def test_visible_ink_stays_inside_width_and_height(self) -> None:
        for text in (
            "GAME OVER",
            "SPACE INVADERS",
            "gyp",
            "SCORE 999999999999999999",
            "1",
        ):
            for width, height in ((1, 1), (8, 5), (24, 7), (48, 12)):
                for align in ("left", "center", "right"):
                    with self.subTest(
                        text=text, width=width, height=height, align=align
                    ):
                        image = Image.new("RGB", (80, 40))
                        rect = (10, 10, 10 + width, 10 + height)
                        bounds = draw_fitted_text(
                            ImageDraw.Draw(image), text, rect, align=align
                        )
                        visible = image.getbbox()
                        if visible is None:
                            continue
                        self.assertEqual(bounds, visible)
                        self.assertGreaterEqual(bounds[0], rect[0])
                        self.assertGreaterEqual(bounds[1], rect[1])
                        self.assertLessEqual(bounds[2], rect[2])
                        self.assertLessEqual(bounds[3], rect[3])
                        self.assertLessEqual(
                            abs(bounds[1] + bounds[3] - rect[1] - rect[3]), 1
                        )
                        if align == "center":
                            self.assertLessEqual(
                                abs(bounds[0] + bounds[2] - rect[0] - rect[2]), 1
                            )
                        elif align == "right":
                            self.assertEqual(bounds[2], rect[2])
                        else:
                            self.assertEqual(bounds[0], rect[0])

    def test_modal_text_cannot_touch_border_or_other_row(self) -> None:
        image = Image.new("RGB", (64, 64))
        draw_boxed_message(
            ImageDraw.Draw(image),
            "A VERY LONG GAME OVER TITLE",
            "HIGH SCORE 9999999999999999",
            rect=(4, 16, 59, 47),
            border_width=2,
            bg_color=(0, 0, 0),
            border_color=(0, 0, 255),
            title_color=(255, 0, 0),
            sub_color=(0, 255, 0),
        )
        title = image.getchannel("R").getbbox()
        subtitle = image.getchannel("G").getbbox()
        self.assertIsNotNone(title)
        self.assertIsNotNone(subtitle)
        assert title is not None and subtitle is not None
        self.assertGreaterEqual(title[0], 8)
        self.assertLessEqual(title[2], 56)
        self.assertGreaterEqual(title[1], 20)
        self.assertLess(title[3], subtitle[1])
        self.assertLessEqual(subtitle[3], 44)

    def test_score_and_title_have_separate_hud_regions(self) -> None:
        image = Image.new("RGB", (64, 64))
        draw_scoreboard(
            ImageDraw.Draw(image),
            "S999999999999999999",
            "ASTEROIDS",
            left_color=(255, 0, 0),
            right_color=(0, 255, 0),
        )
        score = image.getchannel("R").getbbox()
        title = image.getchannel("G").getbbox()
        assert score is not None and title is not None
        self.assertLess(score[2], title[0])
        self.assertLessEqual(title[2], 62)
        self.assertLessEqual(max(score[3], title[3]), 9)


class AutonomousArcadeTests(unittest.TestCase):
    def test_doodle_climbs_and_keeps_platform_storage_bounded(self) -> None:
        for seed in range(3):
            game = DoodleJumpGame(random.Random(seed))
            for _ in range(1200):
                game.update(0.05)
                self.assertLess(len(game.platforms), 16)
                self.assertTrue(0 <= game.x < 64)
            self.assertGreater(game.camera, 500)
            self.assertGreater(game.landings, 40)
            self.assertGreater(game.score, 5000)

    def test_doodle_fall_has_game_over_and_restarts(self) -> None:
        game = DoodleJumpGame(random.Random(0))
        game.y = -20
        game.update(0.05)
        self.assertGreater(game.game_over_timer, 0)
        for _ in range(25):
            game.update(0.05)
        self.assertEqual(game.game_over_timer, 0)
        self.assertGreater(game.y, 0)

    def test_qbert_clears_pyramids_while_coily_chases(self) -> None:
        for seed in range(3):
            game = QbertGame(random.Random(seed))
            max_level = 1
            snake_positions = set()
            for _ in range(1200):
                game.update(0.05)
                max_level = max(max_level, game.level)
                snake_positions.add(game.coily)
                self.assertTrue(0 <= game.position[1] <= game.position[0] < game.ROWS)
            self.assertGreater(max_level, 1)
            self.assertGreater(len(snake_positions), 4)

    def test_frogger_completes_crossings_and_all_five_homes(self) -> None:
        game = FroggerGame()
        max_level = 1
        road_rows = {o.y for o in game.obstacles if not o.is_log and not o.is_turtle}
        self.assertEqual(len(road_rows), 5)
        self.assertTrue(any(o.is_alligator for o in game.obstacles))
        for _ in range(2400):
            game.update(0.05)
            max_level = max(max_level, game.level)
        self.assertGreater(max_level, 1)

    def test_new_games_are_in_the_ai_rotation(self) -> None:
        renderer = RetroArcadeRenderer(randomize=False)
        names = {game.name for game in renderer.games}
        self.assertTrue({"FROGGER", "ASTEROIDS", "DOODLE JUMP", "Q*BERT"} <= names)
        for game in renderer.games:
            game.update(0.05)
            image = Image.new("RGB", (64, 64))
            game.draw(image, ImageDraw.Draw(image))
            self.assertIsNotNone(image.getbbox())


if __name__ == "__main__":
    unittest.main()
