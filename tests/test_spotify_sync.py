import importlib.util
import sys
import tempfile
import unittest
import urllib.error
from argparse import Namespace
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from tests.gitops_render import config_map

# The CronJob runs this file from the spotify-sync ConfigMap; it is the only
# copy, so these tests exercise the deployed code.
SOURCE = Path(__file__).resolve().parents[1] / "gitops/media/spotify-sync/sync.py"
_SPEC = importlib.util.spec_from_file_location("spotify_sync", SOURCE)
assert _SPEC is not None and _SPEC.loader is not None
spotify_sync = importlib.util.module_from_spec(_SPEC)
# Registered before execution so its dataclasses can resolve their module.
sys.modules[_SPEC.name] = spotify_sync
_SPEC.loader.exec_module(spotify_sync)

HTTPClient = spotify_sync.HTTPClient
LidarrClient = spotify_sync.LidarrClient
LocalLibrary = spotify_sync.LocalLibrary
SpotifyRateLimitError = spotify_sync.SpotifyRateLimitError
SyncEngine = spotify_sync.SyncEngine
normalize = spotify_sync.normalize
safe_filename = spotify_sync.safe_filename


def _engine(*, dry_run: bool = False) -> Any:
    engine = object.__new__(SyncEngine)
    engine.args = Namespace(dry_run=dry_run, artist_delay=0)
    engine.artist_defaults = {
        "rootFolderPath": "/data/music",
        "qualityProfileId": 4,
        "metadataProfileId": 1,
    }
    engine.lidarr = Mock()
    return engine


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
        engine.args = Namespace(dry_run=False, artist_delay=0)
        engine.artist_defaults = {
            "rootFolderPath": "/data/music",
            "qualityProfileId": 4,
            "metadataProfileId": 1,
        }
        engine.lidarr = Mock()
        engine.lidarr.lookup.return_value = [
            {"id": 42, "artistName": "Example", "monitored": False}
        ]
        existing = {}

        added, monitored = engine.ensure_artists(["Example"], existing)

        self.assertEqual((added, monitored), (0, 1))
        engine.lidarr.put.assert_called_once()
        self.assertTrue(engine.lidarr.put.call_args.args[1]["monitored"])

    def test_existing_artist_found_by_clean_name_or_mbid_is_not_re_added(
        self,
    ) -> None:
        # Regression for 8b9ed281: a Spotify name that normalizes differently
        # from Lidarr's artistName used to POST a duplicate artist.
        records = [
            {
                "id": 7,
                "artistName": "The Weeknd",
                "cleanName": "theweeknd",
                "foreignArtistId": "mbid-weeknd",
                "monitored": True,
                "qualityProfileId": 4,
                "metadataProfileId": 1,
            }
        ]
        args = Namespace(
            music_dir="/nonexistent",
            playlist_dir=tempfile.mkdtemp(),
            http_timeout=1,
            retries=1,
            lidarr_url="http://lidarr",
            dry_run=False,
            artist_delay=0,
        )
        environment = {
            "SPOTIFY_CLIENT_ID": "id",
            "SPOTIFY_CLIENT_SECRET": "secret",
            "SPOTIFY_REFRESH_TOKEN": "token",
            "LIDARR_API_KEY": "key",
        }
        with (
            patch.dict("os.environ", environment),
            patch.object(LidarrClient, "artists", return_value=records),
            patch.object(
                LidarrClient,
                "artist_defaults",
                return_value={"rootFolderPath": "/data/music"},
            ),
        ):
            engine = SyncEngine(args)
        engine.lidarr = Mock()
        engine.lidarr.lookup.side_effect = [
            [{"artistName": "Weeknd", "cleanName": "theweeknd"}],
            [{"artistName": "Abel", "foreignArtistId": "mbid-weeknd"}],
        ]

        added, monitored = engine.ensure_artists(
            ["Weeknd", "Abel"], engine.existing_artists
        )

        self.assertEqual((added, monitored), (0, 0))
        engine.lidarr.post.assert_not_called()

    def test_new_artist_is_added_once_across_name_variants(self) -> None:
        engine = _engine()
        lookup = {"artistName": "JAY-Z", "foreignArtistId": "mbid-jay"}
        engine.lidarr.lookup.return_value = [lookup]
        engine.lidarr.post.return_value = {
            "id": 9,
            "artistName": "JAY-Z",
            "cleanName": "jayz",
            "foreignArtistId": "mbid-jay",
            "monitored": True,
        }
        existing: dict[str, dict[str, Any]] = {}

        added, monitored = engine.ensure_artists(["Jay-Z", "Shawn Carter"], existing)

        self.assertEqual((added, monitored), (1, 0))
        engine.lidarr.post.assert_called_once()
        self.assertEqual(existing["mbid-jay"]["id"], 9)
        self.assertEqual(existing["jayz"]["id"], 9)

    def test_dry_run_reports_new_artist_once_without_writing(self) -> None:
        engine = _engine(dry_run=True)
        engine.lidarr.lookup.return_value = [
            {
                "artistName": "JAY-Z",
                "cleanName": "jayz",
                "foreignArtistId": "mbid-jay",
                "monitored": False,
            }
        ]
        existing: dict[str, dict[str, Any]] = {}

        added, monitored = engine.ensure_artists(["Jay-Z", "Shawn Carter"], existing)

        self.assertEqual((added, monitored), (1, 0))
        engine.lidarr.post.assert_not_called()
        engine.lidarr.put.assert_not_called()

    def test_configmap_carries_the_tested_source(self) -> None:
        rendered = config_map("media", "spotify-sync")
        self.assertEqual(rendered["data"]["sync.py"], SOURCE.read_text())

    def test_rate_limit_is_reported_without_hour_long_sleep(self) -> None:
        client = HTTPClient(retries=2)
        error = urllib.error.HTTPError(
            "https://api.spotify.com/v1/me/playlists",
            429,
            "Too Many Requests",
            {"Retry-After": "50748"},
            None,
        )
        with (
            patch("urllib.request.urlopen", side_effect=error),
            self.assertRaisesRegex(SpotifyRateLimitError, "50748 seconds"),
        ):
            client.request("https://api.spotify.com/v1/me/playlists")

    def test_artist_defaults_use_lidarr_contract(self) -> None:
        engine = object.__new__(SyncEngine)
        engine.lidarr = object.__new__(LidarrClient)
        engine.lidarr.get = Mock()
        engine.lidarr.get.side_effect = [
            [
                {
                    "id": 3,
                    "path": "/data/music",
                    "accessible": True,
                    "defaultQualityProfileId": 1,
                    "defaultMetadataProfileId": 3,
                }
            ]
        ]
        defaults = engine.lidarr.artist_defaults(
            [
                {"qualityProfileId": 4, "metadataProfileId": 1},
                {"qualityProfileId": 4, "metadataProfileId": 1},
            ]
        )
        self.assertEqual(
            defaults,
            {
                "rootFolderPath": "/data/music",
                "qualityProfileId": 4,
                "metadataProfileId": 1,
            },
        )

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
