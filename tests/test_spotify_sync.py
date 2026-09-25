import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock

from scripts.spotify_sync import LocalLibrary, SyncEngine, normalize, safe_filename


class TestSpotifySync(unittest.TestCase):
    def test_normalize(self) -> None:
        self.assertEqual(normalize("SiR - You Can't Save Me!"), "siryoucantsaveme")
        self.assertEqual(normalize("  Kendrick Lamar  "), "kendricklamar")
        self.assertEqual(normalize(""), "")

    def test_safe_filename(self) -> None:
        self.assertEqual(safe_filename("R&B"), "R&B")
        self.assertEqual(safe_filename("Boom Bap / Jazz Rap"), "Boom Bap _ Jazz Rap")
        self.assertEqual(safe_filename("   My Playlist #1   "), "My Playlist #1")
        self.assertEqual(safe_filename(""), "Unnamed_Playlist")
        self.assertEqual(safe_filename(".."), "Unnamed_Playlist")

    def test_local_library_matching(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [
                root / "fakemink" / "01 - Snow White.flac",
                root / "Sade" / "07 - Punch Drunk.flac",
                root / "SiR" / "Chasing Summer" / "You Can't Save Me.mp3",
            ]
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            library = LocalLibrary(root)
            library.index()

            self.assertEqual(
                library.match(["fakemink"], "Snow White").relative,
                "fakemink/01 - Snow White.flac",
            )
            self.assertEqual(
                library.match(["Sade"], "Punch Drunk").relative,
                "Sade/07 - Punch Drunk.flac",
            )
            self.assertEqual(
                library.match(["SiR"], "You Can't Save Me").relative,
                "SiR/Chasing Summer/You Can't Save Me.mp3",
            )
            self.assertIsNone(library.match(["Unknown Artist"], "Unknown Song"))

    def test_duplicate_playlist_names_are_stable(self) -> None:
        engine = object.__new__(SyncEngine)
        playlists = [
            {"id": "one", "name": "Mix"},
            {"id": "two", "name": "Mix"},
            {"id": "three", "name": "Unique"},
        ]
        self.assertEqual(
            engine.playlist_filenames(playlists),
            {
                "one": "Mix [one].m3u8",
                "two": "Mix [two].m3u8",
                "three": "Unique.m3u8",
            },
        )

    def test_ensure_artists_enables_monitoring(self) -> None:
        engine = object.__new__(SyncEngine)
        engine.args = Namespace(
            dry_run=False,
            artist_delay=0,
            lidarr_root_folder="/data",
        )
        engine.lidarr = Mock()
        engine.lidarr.lookup.return_value = [
            {"id": 42, "artistName": "Example", "monitored": False}
        ]
        existing = {}

        added, monitored = engine.ensure_artists(["Example"], existing)

        self.assertEqual((added, monitored), (0, 1))
        engine.lidarr.put.assert_called_once()
        self.assertTrue(engine.lidarr.put.call_args.args[1]["monitored"])

    def test_state_version_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / ".spotify_sync_state.json"
            state_path.write_text('{"playlists": {"old": {}}}')
            engine = object.__new__(SyncEngine)
            engine.state_path = state_path
            engine.STATE_VERSION = 2
            self.assertEqual(
                engine.load_state(),
                {"version": 2, "playlists": {"old": {}}},
            )


if __name__ == "__main__":
    unittest.main()
