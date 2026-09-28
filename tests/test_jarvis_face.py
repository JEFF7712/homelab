from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FACE_DIR = REPO_ROOT / "home-assistant" / "www" / "jarvis"


def read_face(name: str) -> str:
    return (FACE_DIR / name).read_text(encoding="utf-8")


class JarvisFaceSelfUpdateTest(unittest.TestCase):
    def test_config_has_integer_face_version(self) -> None:
        config = json.loads(read_face("config.json"))
        self.assertIn("face_version", config)
        self.assertIsInstance(config["face_version"], int)
        self.assertGreater(config["face_version"], 0)

    def test_index_html_versions_assets_through_loader(self) -> None:
        html = read_face("index.html")
        self.assertIn('id="face-css"', html)
        self.assertIn("encodeURIComponent(v)", html)
        self.assertNotIn('<script src="app.js', html)

    def test_app_js_polls_face_version(self) -> None:
        js = read_face("app.js")
        self.assertIn("face_version", js)
        self.assertIn("setInterval", js)
        self.assertIn("__jarvisFaceVersionTarget", js)
        self.assertGreaterEqual(js.count("cache: 'no-store'"), 2)

    def test_music_state_wiring(self) -> None:
        config = json.loads(read_face("config.json"))
        self.assertIn("face_color_music", config)
        js = read_face("app.js")
        self.assertIn("music:", js)
        self.assertIn("state-music", js)
        self.assertIn("state === 'playing')", js)
        branch = js.split("state === 'playing')")[1].split("Default: Idle")[0]
        self.assertIn("setState('music'", branch)
        self.assertNotIn("setState('responding'", branch)
        html_css = read_face("style.css")
        self.assertIn("#app.state-music", html_css)

    def test_music_shows_album_art_with_cava_underneath(self) -> None:
        html = read_face("index.html")
        self.assertIn('id="music-overlay"', html)
        self.assertIn('id="album-art"', html)
        self.assertIn('id="cava-canvas"', html)
        self.assertLess(
            html.index('id="album-art"'),
            html.index('id="cava-canvas"'),
            "album art sits above the cava bars",
        )
        js = read_face("app.js")
        self.assertIn("entity_picture", js)
        self.assertIn("cava-canvas", js)
        self.assertIn("album-art", js)
        self.assertIn("if (exprName === 'music')", js)
        self.assertIn("urlParams.get('art')", js)
        self.assertIn("nBars = 28", js)
        self.assertIn("crossOrigin", js)
        self.assertIn("getImageData", js)
        self.assertIn("artFill || fill", js)
        self.assertNotIn("drawMusicVisualizer", js)
        css = read_face("style.css")
        self.assertIn("#music-overlay", css)
        self.assertIn("#app.state-music #music-overlay", css)

    def test_music_label_shows_track(self) -> None:
        js = read_face("app.js")
        self.assertIn("media_title", js)
        self.assertIn("media_artist", js)
        self.assertIn("JARVIS // PLAYING // ", js)

    def test_kiosk_boot_url_matches_face_version(self) -> None:
        config = json.loads(read_face("config.json"))
        kiosk = (
            REPO_ROOT / "flake" / "hosts" / "homelab-05" / "default.nix"
        ).read_text(encoding="utf-8")
        match = re.search(r'url = "([^"]+/local/jarvis/index\.html\?v=([^"]+))"', kiosk)
        self.assertIsNotNone(match, "kiosk URL must carry the face ?v=")
        assert match is not None
        self.assertEqual(match.group(2), str(config["face_version"]))


class JarvisFaceSubscriptionTest(unittest.TestCase):
    def test_subscribes_to_exactly_the_displayed_entities(self) -> None:
        js = read_face("app.js")
        self.assertIn("subscribe_entities", js)
        self.assertIn(
            "[config.satelliteEntity, config.muteEntity, config.mediaPlayerEntity]",
            js,
        )
        self.assertNotIn("get_states", js)
        self.assertNotIn("subscribe_events", js)

    def test_untracked_entities_are_never_retained(self) -> None:
        js = read_face("app.js")
        self.assertIn("isTracked", js)
        self.assertIn("delete lastEntityStates[entityId]", js)

    def test_compressed_states_and_diffs_are_merged(self) -> None:
        js = read_face("app.js")
        self.assertIn("storeFullState", js)
        self.assertIn("applyStateDiff", js)
        self.assertIn("compressed.s", js)

    def test_heartbeat_and_reconnect_are_bounded(self) -> None:
        js = read_face("app.js")
        self.assertIn("HEARTBEAT_INTERVAL_MS = 30000", js)
        self.assertIn("STALE_AFTER_MS = 90000", js)
        self.assertIn("MAX_BACKOFF_MS = 30000", js)
        self.assertIn("type: 'ping'", js)
        self.assertIn("Math.random()", js)


class JarvisFaceBehaviorTest(unittest.TestCase):
    """Drive the real face bundle in node against live-captured HA shapes."""

    def test_entity_subscription_against_real_shapes(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is unavailable in this environment")
        harness = REPO_ROOT / "tests" / "test_jarvis_face_harness.js"
        fixture = REPO_ROOT / "tests" / "fixtures" / "face_entity_snapshot.json"
        self.assertTrue(fixture.is_file(), "live-captured snapshot fixture exists")
        completed = subprocess.run(
            [
                node,
                str(harness),
                str(FACE_DIR / "app.js"),
                str(fixture),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"face harness failed:\n{completed.stdout}\n{completed.stderr}",
        )
        self.assertIn("real snapshot renders idle", completed.stdout)


if __name__ == "__main__":
    unittest.main()
