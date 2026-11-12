"""Tests for the music_director palette, effect rules and metadata merging.

The package __init__ and director need Home Assistant, so a stub package
exposes only the pure modules.
"""

from __future__ import annotations

import importlib
import re
import sys
import types
import unittest
from pathlib import Path

import yaml

from scripts.home_assistant.canonical import DuplicateKeySafeLoader

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "home-assistant" / "custom_components" / "music_director"


def load(name: str) -> types.ModuleType:
    if "music_director" not in sys.modules:
        package = types.ModuleType("music_director")
        package.__path__ = [str(PACKAGE_DIR)]
        sys.modules["music_director"] = package
    return importlib.import_module(f"music_director.{name}")


palette = load("palette")
rules = load("rules")
metadata = load("metadata")

CSS_GRADIENT = re.compile(
    r"^linear-gradient\(90deg(, rgb\(\d{1,3}, \d{1,3}, \d{1,3}\) \d{1,3}%)+\)$"
)


def cover(*parts: tuple[tuple[int, int, int], int]) -> list[tuple[int, int, int]]:
    pixels: list[tuple[int, int, int]] = []
    for colour, count in parts:
        pixels.extend([colour] * count)
    return pixels


def hue_of(rgb: tuple[int, int, int]) -> float:
    lab = palette.rgb_to_oklab(rgb)
    return palette._hue(lab)


class TestPalette(unittest.TestCase):
    def test_vivid_minority_beats_dominant_neutral_background(self) -> None:
        # Mostly beige background with a red subject: the red must win.
        result = palette.extract_palette(
            cover(((222, 210, 190), 1800), ((200, 30, 40), 500))
        )
        assert result is not None
        self.assertEqual(result.source, "cover")
        self.assertLess(palette.hue_distance(result.hue, hue_of((200, 30, 40))), 10)

    def test_grey_and_black_covers_have_no_palette(self) -> None:
        self.assertIsNone(
            palette.extract_palette(cover(((10, 10, 10), 1500), ((128, 128, 128), 800)))
        )
        self.assertIsNone(palette.extract_palette([]))

    def test_accent_requires_a_distinct_hue(self) -> None:
        two_tone = palette.extract_palette(
            cover(((20, 60, 200), 1200), ((240, 160, 20), 900))
        )
        assert two_tone is not None
        self.assertIsNotNone(two_tone.accent_hue)
        assert two_tone.accent_hue is not None
        self.assertGreaterEqual(
            palette.hue_distance(two_tone.hue, two_tone.accent_hue), 40
        )
        shades = palette.extract_palette(
            cover(((20, 60, 200), 1200), ((40, 80, 220), 900))
        )
        assert shades is not None
        self.assertIsNone(shades.accent_hue)

    def test_extraction_is_deterministic(self) -> None:
        pixels = cover(((30, 140, 90), 900), ((240, 120, 40), 700), ((15, 15, 30), 700))
        self.assertEqual(
            palette.extract_palette(pixels), palette.extract_palette(pixels)
        )

    def test_genre_fallback_uses_the_primary_genre(self) -> None:
        self.assertEqual(palette.genre_palette(("Rap", "Hip Hop")).source, "genre")
        rnb = palette.genre_palette(("R&B",))
        rock = palette.genre_palette(("Rock",))
        self.assertNotEqual(rnb.hue, rock.hue)
        self.assertEqual(palette.genre_palette(()).source, "default")
        self.assertEqual(palette.genre_palette(("polka",)).hue, palette.DEFAULT_HUE)

    def test_area_shades_are_distinct_valid_and_near_the_primary(self) -> None:
        base = palette.Palette(hue=30.0, chroma=0.15, accent_hue=None, source="cover")
        colours = palette.area_colors(base, 6)
        self.assertEqual(len(colours), 6)
        self.assertEqual(len(set(colours)), 6)
        for colour in colours:
            self.assertTrue(all(0 <= channel <= 255 for channel in colour))
            self.assertLess(palette.hue_distance(hue_of(colour), 30.0), 40)

    def test_accent_reaches_some_areas(self) -> None:
        base = palette.Palette(hue=30.0, chroma=0.15, accent_hue=250.0, source="cover")
        hues = [hue_of(c) for c in palette.area_colors(base, 6)]
        self.assertTrue(any(palette.hue_distance(h, 250.0) < 15 for h in hues))

    def test_out_of_gamut_requests_are_clamped(self) -> None:
        for hue in range(0, 360, 15):
            rgb = palette.oklch_to_rgb(0.7, 0.4, float(hue))
            self.assertTrue(all(0 <= c <= 255 for c in rgb))

    def test_gradient_matches_ledfx_format(self) -> None:
        base = palette.Palette(hue=200.0, chroma=0.1, accent_hue=None, source="genre")
        self.assertRegex(palette.gradient(base), CSS_GRADIENT)
        self.assertEqual(palette.to_hex((255, 0, 16)), "#ff0010")


class TestRules(unittest.TestCase):
    def test_primary_genre_decides_the_family(self) -> None:
        self.assertEqual(
            rules.classify(rules.Features(genres=("Rap", "Hip Hop"))), "bass"
        )
        self.assertEqual(
            rules.classify(rules.Features(genres=("Electro", "Rap"))), "drive"
        )
        self.assertEqual(rules.classify(rules.Features(genres=("R&B",))), "smooth")
        self.assertEqual(rules.classify(rules.Features(genres=("indie rock",))), "rock")
        self.assertEqual(rules.classify(rules.Features(genres=("ambient",))), "calm")
        self.assertEqual(rules.classify(rules.Features()), "default")

    def test_audio_features_refine_the_genre(self) -> None:
        quiet = rules.Features(genres=("Rap",), energy=0.2)
        self.assertEqual(rules.classify(quiet), "calm")
        mellow_rap = rules.Features(genres=("Rap",), energy=0.45)
        self.assertEqual(rules.classify(mellow_rap), "smooth")
        club = rules.Features(genres=("Pop",), energy=0.85, bpm=124, danceability=0.8)
        self.assertEqual(rules.classify(club), "drive")
        unplugged = rules.Features(genres=("Rock",), energy=0.45, acousticness=0.9)
        self.assertEqual(rules.classify(unplugged), "calm")
        loud_unknown = rules.Features(energy=0.9)
        self.assertEqual(rules.classify(loud_unknown), "drive")

    def test_effect_choice_is_stable_and_avoids_repeats(self) -> None:
        first = rules.choose_effect("bass", "artist\ntitle\nalbum", None)
        self.assertEqual(
            first, rules.choose_effect("bass", "artist\ntitle\nalbum", None)
        )
        self.assertIn(first, rules.FAMILIES["bass"])
        self.assertNotEqual(
            first, rules.choose_effect("bass", "artist\ntitle\nalbum", first)
        )
        self.assertEqual(rules.choose_effect("default", "x", "energy"), "energy")

    def test_every_family_effect_has_a_colour_mapping(self) -> None:
        base = palette.Palette(hue=120.0, chroma=0.12, accent_hue=None, source="cover")
        for effects in rules.FAMILIES.values():
            for effect in effects:
                config = rules.effect_config(effect, base)
                if effect in rules.GRADIENT_EFFECTS:
                    self.assertRegex(config["gradient"], CSS_GRADIENT)
                else:
                    self.assertEqual(
                        set(config), {"color_lows", "color_mids", "color_high"}
                    )
        self.assertIn("sparks_color", rules.effect_config("power", base))
        with self.assertRaises(ValueError):
            rules.effect_config("fire", base)


class TestDynamics(unittest.TestCase):
    BASE = palette.Palette(hue=120.0, chroma=0.12, accent_hue=None, source="cover")
    # LedFx schema ranges for the keys the director sets.
    RANGES = {
        ("melt", "speed"): (0.001, 1.0),
        ("lava_lamp", "speed"): (0.1, 15.0),
        ("scroll", "speed"): (1, 10),
        ("melt", "reactivity"): (0.0001, 1.0),
        ("lava_lamp", "reactivity"): (0.00001, 0.9),
        ("energy", "sensitivity"): (0.3, 0.99),
        ("power", "bass_decay_rate"): (0.0, 1.0),
        ("scroll", "decay"): (0.8, 1.0),
    }

    def test_faster_songs_run_faster(self) -> None:
        for effect in ("melt", "lava_lamp", "scroll"):
            slow = rules.effect_config(effect, self.BASE, rules.Features(bpm=60))
            fast = rules.effect_config(effect, self.BASE, rules.Features(bpm=175))
            self.assertLess(slow["speed"], fast["speed"], effect)
        self.assertIsInstance(
            rules.effect_config("scroll", self.BASE, rules.Features(bpm=120))["speed"],
            int,
        )

    def test_energy_raises_responsiveness(self) -> None:
        for effect, key in (
            ("melt", "reactivity"),
            ("lava_lamp", "reactivity"),
            ("energy", "sensitivity"),
            ("power", "bass_decay_rate"),
        ):
            quiet = rules.effect_config(effect, self.BASE, rules.Features(energy=0.1))
            loud = rules.effect_config(effect, self.BASE, rules.Features(energy=0.95))
            self.assertLess(quiet[key], loud[key], effect)

    def test_values_stay_inside_ledfx_schema_ranges(self) -> None:
        for bpm in (0, 40, 120, 300):
            for energy in (-1.0, 0.0, 0.5, 1.0, 2.0):
                features = rules.Features(bpm=bpm, energy=energy)
                for effect in rules.EFFECTS:
                    config = rules.effect_config(effect, self.BASE, features)
                    for (name, key), (low, high) in self.RANGES.items():
                        if name == effect:
                            self.assertGreaterEqual(config[key], low, (effect, key))
                            self.assertLessEqual(config[key], high, (effect, key))

    def test_unknown_inputs_keep_ledfx_defaults(self) -> None:
        for effect in rules.EFFECTS:
            plain = rules.effect_config(effect, self.BASE)
            unknown = rules.effect_config(effect, self.BASE, rules.Features())
            self.assertEqual(plain, unknown, effect)

    def test_strobe_is_a_drive_effect_with_track_colours(self) -> None:
        self.assertIn("strobe", rules.FAMILIES["drive"])
        self.assertRegex(
            rules.effect_config("strobe", self.BASE)["gradient"], CSS_GRADIENT
        )

    def test_override_dropdown_offers_exactly_the_director_effects(self) -> None:
        core = yaml.load(
            (ROOT / "home-assistant" / "core" / "configuration.yaml").read_text(),
            Loader=DuplicateKeySafeLoader,
        )
        options = core["input_select"]["music_mode_effect"]["options"]
        self.assertEqual(options[0], "Auto")
        self.assertEqual(
            {option.lower().replace(" ", "_") for option in options[1:]},
            set(rules.EFFECTS),
        )
        self.assertEqual(
            core["music_director"]["effect_select"], "input_select.music_mode_effect"
        )


class TestMetadata(unittest.TestCase):
    def test_same_track_tolerates_punctuation_and_features(self) -> None:
        self.assertTrue(
            metadata.same_track(
                "Who Knows", "Daniel Caesar", "Who Knows?", "Daniel Caesar"
            )
        )
        self.assertTrue(metadata.same_track("Song", "A", "Song (feat. B)", "A, B"))
        self.assertFalse(metadata.same_track("Song", "A", "Other", "A"))
        self.assertFalse(metadata.same_track("", "A", "", "A"))

    def test_spotify_track_id(self) -> None:
        self.assertEqual(
            metadata.spotify_track_id("spotify:track:6DH13QYXK7lKkYHSU88N48"),
            "6DH13QYXK7lKkYHSU88N48",
        )
        self.assertIsNone(
            metadata.spotify_track_id("spotify:episode:6DH13QYXK7lKkYHSU88N48")
        )
        self.assertIsNone(metadata.spotify_track_id(None))

    def test_sources_merge_in_precedence_order(self) -> None:
        info = metadata.TrackInfo()
        metadata.apply_reccobeats(
            info,
            {
                "content": [
                    {
                        "tempo": 148.1,
                        "energy": 0.918,
                        "danceability": 0.355,
                        "acousticness": 0.001,
                        "isrc": "gbffp0300052",
                    }
                ]
            },
        )
        metadata.apply_library(
            info,
            {"match": {"genres": ["Rock"], "bpm": 150}, "matched_by": "isrc"},
        )
        metadata.apply_lastfm(
            info, {"toptags": {"tag": [{"name": "alternative", "count": 100}]}}, "X"
        )
        self.assertEqual(info.isrc, "GBFFP0300052")
        self.assertEqual(info.bpm, 148.1)  # ReccoBeats tempo beats file tags.
        self.assertEqual(info.genres, ("Rock",))  # Library genres beat Last.fm.
        self.assertEqual(info.sources, ["reccobeats", "library:isrc"])
        self.assertEqual(info.features().energy, 0.918)

    def test_missing_sources_leave_info_empty(self) -> None:
        info = metadata.TrackInfo()
        metadata.apply_reccobeats(info, {"content": []})
        metadata.apply_reccobeats(info, None)
        metadata.apply_library(info, {"match": None, "matched_by": None})
        metadata.apply_lastfm(info, {"error": 6, "message": "Track not found"}, "X")
        self.assertEqual(info, metadata.TrackInfo())

    def test_library_bpm_fills_in_without_reccobeats(self) -> None:
        info = metadata.TrackInfo()
        metadata.apply_library(
            info, {"match": {"genres": [], "bpm": 92}, "matched_by": "title"}
        )
        self.assertEqual(info.bpm, 92)
        self.assertEqual(info.genres, ())

    def test_lastfm_filters_noise_and_rare_tags(self) -> None:
        body = {
            "toptags": {
                "tag": [
                    {"name": "Hip-Hop", "count": 100},
                    {"name": "seen live", "count": 90},
                    {"name": "2010s", "count": 80},
                    {"name": "Kendrick Lamar", "count": 70},
                    {"name": "conscious", "count": 40},
                    {"name": "obscure", "count": 3},
                ]
            }
        }
        self.assertEqual(
            metadata.lastfm_genres(body, "Kendrick Lamar"), ("hip-hop", "conscious")
        )
        single = {"toptags": {"tag": {"name": "jazz", "count": "55"}}}
        self.assertEqual(metadata.lastfm_genres(single), ("jazz",))


if __name__ == "__main__":
    unittest.main()
