import json
import threading
import time
import unittest
from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw
from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve

from ha_source import (
    ArtworkCache,
    HomeAssistantSource,
    HybridPlaybackSource,
    PlaybackState,
    parse_spotify_state,
)
from spotify_matrix_streamer import SpotifyQuotaExceeded, SpotifyRateLimited

ENTITY = "media_player.spotify_rupan"


def state(title: str = "First", playback: str = "playing", age: float = 0) -> dict:
    return {
        "entity_id": ENTITY,
        "state": playback,
        "last_updated": datetime.fromtimestamp(time.time() - age, UTC).isoformat(),
        "attributes": {
            "media_title": title,
            "media_artist": "Artist",
            "entity_picture": "https://i.scdn.co/image/test",
        },
    }


def compact(value: dict) -> dict:
    return {
        "s": value["state"],
        "a": value["attributes"],
        "lc": datetime.fromisoformat(value["last_updated"]).timestamp(),
    }


def wait_until(predicate, seconds: float = 4) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Condition did not become true")


class HomeAssistantTests(unittest.TestCase):
    def test_spotify_vinyl_uses_locked_full_brightness(self) -> None:
        from ha_matrix_streamer import effective_brightness

        self.assertEqual(effective_brightness("Spotify", 0.35), 1.0)
        self.assertEqual(effective_brightness("Clock & Weather", 0.35), 0.35)
        self.assertEqual(effective_brightness("Off", 0.35), 0.0)

    def test_compact_entity_deltas_merge_attributes_and_remove_stale_art(self) -> None:
        source = HomeAssistantSource("http://127.0.0.1:8123", "test-token", ENTITY)
        source._connected = True
        initial = state()
        source.handle_event(
            {"id": 1, "type": "event", "event": {"a": {ENTITY: compact(initial)}}}
        )
        updated = time.time() + 1
        source.handle_event(
            {
                "id": 1,
                "type": "event",
                "event": {
                    "c": {
                        ENTITY: {
                            "+": {
                                "s": "paused",
                                "lc": updated,
                                "a": {"media_title": "Next"},
                            },
                            "-": {"a": ["entity_picture"]},
                        }
                    }
                },
            }
        )
        current = source.snapshot()
        self.assertEqual(current.title, "Next")
        self.assertEqual(current.artist, "Artist")
        self.assertEqual(current.artwork_url, "")
        self.assertAlmostEqual(current.updated_at, updated, delta=0.000001)
        self.assertFalse(current.playing)
        source.handle_event(
            {"id": 1, "type": "event", "event": {"r": ["media_player.other"]}}
        )
        self.assertTrue(source.snapshot().available)
        source.handle_event({"id": 1, "type": "event", "event": {"r": [ENTITY]}})
        self.assertFalse(source.snapshot().available)

    def test_stale_playing_state_idles_but_paused_art_is_retained(self) -> None:
        source = HomeAssistantSource("http://127.0.0.1:8123", "test-token", ENTITY)
        source._connected = True
        source.apply_state(state(age=300))
        self.assertFalse(source.snapshot().available)
        source.apply_state(state(playback="paused"))
        self.assertTrue(source.snapshot().available)
        self.assertFalse(source.snapshot().playing)
        source.apply_state(state(playback="unavailable"))
        self.assertFalse(source.snapshot().available)

    def test_older_events_and_unrelated_entities_do_not_replace_current_track(
        self,
    ) -> None:
        source = HomeAssistantSource("http://127.0.0.1:8123", "test-token", ENTITY)
        source._connected = True
        source.apply_state(state("Current"))
        source.apply_state(state("Older", age=60))
        unrelated = state("Unrelated")
        unrelated["entity_id"] = "media_player.other"
        source.apply_state(unrelated)
        self.assertEqual(source.snapshot().title, "Current")
        source.apply_state(None)
        self.assertFalse(source.snapshot().available)

    def test_websocket_events_and_reconnect_resynchronize_cached_state(self) -> None:
        connections = []
        commands = []
        heartbeats = []
        cached = [state()]
        fixture_errors = []

        def handler(socket) -> None:
            try:
                socket.send(json.dumps({"type": "auth_required"}))
                authentication = json.loads(socket.recv(timeout=3))
                if authentication != {"type": "auth", "access_token": "test-token"}:
                    raise AssertionError("Wrong authentication")
                commands.append(authentication["type"])
                socket.send(json.dumps({"type": "auth_ok"}))
                subscription = json.loads(socket.recv(timeout=3))
                if subscription != {
                    "id": 1,
                    "type": "subscribe_entities",
                    "entity_ids": [ENTITY],
                }:
                    raise AssertionError("Unexpected command or entity subscription")
                commands.append(subscription["type"])
                socket.send(json.dumps({"id": 1, "type": "result", "success": True}))
                socket.send(
                    json.dumps(
                        {
                            "id": 1,
                            "type": "event",
                            "event": {"a": {ENTITY: compact(cached[0])}},
                        }
                    )
                )
                connections.append(socket)
                while True:
                    command = json.loads(socket.recv())
                    if command["type"] != "ping":
                        raise AssertionError("Unexpected command after subscription")
                    heartbeats.append(command["id"])
                    if len(heartbeats) == 2:
                        socket.send(json.dumps({"id": 1, "type": "event", "event": {}}))
                    else:
                        socket.send(json.dumps({"id": command["id"], "type": "pong"}))
            except ConnectionClosed:
                return
            except (AssertionError, KeyError, ValueError, TimeoutError) as error:
                fixture_errors.append(type(error).__name__)

        with serve(handler, "127.0.0.1", 0) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            base = f"http://127.0.0.1:{server.socket.getsockname()[1]}"
            source = HomeAssistantSource(base, "test-token", ENTITY)
            source._heartbeat_interval = 0.05
            source._heartbeat_timeout = 2

            def cached_response(*args, **kwargs):
                response = Mock(status_code=200)
                response.json.return_value = cached[0]
                return response

            with patch(
                "ha_source.requests.get", side_effect=cached_response
            ) as request:
                source.start()
                try:
                    wait_until(lambda: source.snapshot().available)
                    wait_until(lambda: len(heartbeats) >= 3)
                    self.assertTrue(source.connected)
                    update = state("Second", "paused")
                    connections[0].send(
                        json.dumps(
                            {
                                "id": 1,
                                "type": "event",
                                "event": {"a": {ENTITY: compact(update)}},
                            }
                        )
                    )
                    wait_until(lambda: source.snapshot().title == "Second")
                    self.assertFalse(source.snapshot().playing)
                    cached[0] = state("After reconnect")
                    connections[0].close()
                    wait_until(lambda: not source.snapshot().available)
                    wait_until(
                        lambda: (
                            source.snapshot().title == "After reconnect"
                            and source.snapshot().available
                        )
                    )
                    self.assertEqual(commands, ["auth", "subscribe_entities"] * 2)
                    self.assertEqual(request.call_count, 0)
                    self.assertEqual(fixture_errors, [])
                finally:
                    source.close()
                    server.shutdown()
                    worker.join(3)

    def test_artwork_never_leaks_ha_token_to_external_hosts(self) -> None:
        image_data = BytesIO()
        Image.new("RGB", (16, 16), "red").save(image_data, format="PNG")
        response = Mock(status_code=200)
        response.iter_content.return_value = [image_data.getvalue()]
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        source = HomeAssistantSource("http://127.0.0.1:8123", "test-token", ENTITY)
        with patch("ha_source.requests.get", return_value=response) as request:
            source.fetch_artwork("https://i.scdn.co/image/test")
            self.assertEqual(request.call_args.kwargs["headers"], {})
            source.fetch_artwork("/api/media_player_proxy/" + ENTITY)
            self.assertEqual(
                request.call_args.kwargs["headers"],
                {"Authorization": "Bearer test-token"},
            )
            for bad_url in (
                "https://api.spotify.com/v1/me/player",
                "https://accounts.spotify.com/api/token",
                "/api/config",
                "http://outside.example/art",
            ):
                with self.assertRaises(ValueError):
                    source.fetch_artwork(bad_url)
            self.assertEqual(request.call_count, 2)
            self.assertTrue(request.call_args.kwargs["allow_redirects"])

    def test_silent_ha_connection_reaches_heartbeat_deadline(self) -> None:
        source = HomeAssistantSource("http://127.0.0.1:8123", "test-token", ENTITY)
        source._heartbeat_interval = 0.1
        source._heartbeat_timeout = 0.1
        socket = Mock()
        socket.recv.side_effect = [
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
            json.dumps({"id": 1, "type": "result", "success": True}),
            TimeoutError(),
        ]
        response = Mock(status_code=200)
        response.json.return_value = state()
        with (
            patch("ha_source.requests.get", return_value=response),
            patch("ha_source.time.monotonic", side_effect=[0, 0.2, 0.4]),
            self.assertRaisesRegex(TimeoutError, "HA heartbeat timed out"),
        ):
            source._session(socket)
        self.assertEqual(
            json.loads(socket.send.call_args.args[0]), {"id": 2, "type": "ping"}
        )

    def test_artwork_fetch_is_nonblocking_bounded_and_cached(self) -> None:
        release = threading.Event()
        source = Mock(spec=HomeAssistantSource)

        def fetch(url):
            if not release.wait(3):
                raise AssertionError("Artwork worker stalled")
            return Image.new("RGB", (16, 16), "red")

        source.fetch_artwork.side_effect = fetch
        cache = ArtworkCache(source)
        try:
            self.assertIsNone(cache.get("first"))
            wait_until(lambda: source.fetch_artwork.call_count == 1)
            self.assertIsNone(cache.get("second"))
            self.assertEqual(source.fetch_artwork.call_count, 1)
            release.set()
            wait_until(lambda: cache.get("second") is not None)
            self.assertEqual(source.fetch_artwork.call_count, 2)
            self.assertIsNotNone(cache.get("first"))
            self.assertEqual(source.fetch_artwork.call_count, 2)
        finally:
            release.set()
            cache.close()


class HybridPlaybackSourceTests(unittest.TestCase):
    def test_parse_spotify_state_extracts_track_details_and_playback_state(
        self,
    ) -> None:
        empty = parse_spotify_state(None, now=100.0)
        self.assertFalse(empty.available)
        self.assertFalse(empty.playing)
        self.assertEqual(empty.title, "")
        self.assertEqual(empty.updated_at, 100.0)

        playing_track = {
            "is_playing": True,
            "item": {
                "name": "Bohemian Rhapsody",
                "artists": [{"name": "Queen"}],
                "album": {
                    "images": [{"url": "https://i.scdn.co/image/art1", "width": 640}]
                },
            },
        }
        parsed = parse_spotify_state(playing_track, now=200.0)
        self.assertTrue(parsed.available)
        self.assertTrue(parsed.playing)
        self.assertEqual(parsed.title, "Bohemian Rhapsody")
        self.assertEqual(parsed.artist, "Queen")
        self.assertEqual(parsed.artwork_url, "https://i.scdn.co/image/art1")
        self.assertEqual(parsed.updated_at, 200.0)

        paused_track = {
            "is_playing": False,
            "item": {
                "name": "Under Pressure",
                "artists": [{"name": "Queen"}, {"name": "David Bowie"}],
                "album": {
                    "images": [{"url": "https://i.scdn.co/image/art2", "width": 640}]
                },
            },
        }
        parsed_paused = parse_spotify_state(paused_track, now=300.0)
        self.assertTrue(parsed_paused.available)
        self.assertFalse(parsed_paused.playing)
        self.assertEqual(parsed_paused.title, "Under Pressure")
        self.assertEqual(parsed_paused.artist, "Queen, David Bowie")

        episode = {
            "is_playing": True,
            "item": {
                "name": "Episode 1",
                "show": {"name": "Podcast Show"},
                "images": [{"url": "https://i.scdn.co/image/ep1"}],
            },
        }
        parsed_ep = parse_spotify_state(episode, now=400.0)
        self.assertTrue(parsed_ep.available)
        self.assertTrue(parsed_ep.playing)
        self.assertEqual(parsed_ep.title, "Episode 1")
        self.assertEqual(parsed_ep.artist, "Podcast Show")
        self.assertEqual(parsed_ep.artwork_url, "https://i.scdn.co/image/ep1")

    def test_hybrid_source_prefers_spotify_when_available(self) -> None:
        ha_mock = Mock(spec=HomeAssistantSource)
        ha_mock.connected = True
        ha_mock.snapshot.return_value = PlaybackState(
            available=True, playing=True, title="HA Song", artist="HA Artist"
        )
        spotify_mock = Mock()
        spotify_mock.get_currently_playing.return_value = {
            "is_playing": True,
            "item": {
                "name": "Spotify Song",
                "artists": [{"name": "Spotify Artist"}],
                "album": {"images": [{"url": "https://i.scdn.co/image/spot"}]},
            },
        }

        hybrid = HybridPlaybackSource(ha_mock, spotify_mock, poll_interval_playing=1.5)
        # Before first poll, falls back to HA
        self.assertEqual(hybrid.active_provider, "home_assistant")
        self.assertEqual(hybrid.snapshot().title, "HA Song")

        # After one successful poll, Spotify is active
        delay = hybrid._poll_once()
        self.assertEqual(delay, 1.5)
        self.assertEqual(hybrid.active_provider, "spotify")
        self.assertEqual(hybrid.snapshot().title, "Spotify Song")
        self.assertEqual(hybrid.snapshot().artist, "Spotify Artist")
        self.assertTrue(hybrid.snapshot().playing)
        self.assertTrue(hybrid.connected)

    def test_hybrid_source_falls_back_to_ha_on_rate_limit(self) -> None:
        ha_mock = Mock(spec=HomeAssistantSource)
        ha_mock.connected = True
        ha_mock.snapshot.return_value = PlaybackState(
            available=True, playing=False, title="HA Fallback Track"
        )
        spotify_mock = Mock()
        spotify_mock.get_currently_playing.side_effect = SpotifyRateLimited(
            retry_after=45.0
        )

        hybrid = HybridPlaybackSource(ha_mock, spotify_mock)
        delay = hybrid._poll_once()

        self.assertEqual(delay, 45.0)
        self.assertEqual(hybrid.active_provider, "home_assistant")
        self.assertEqual(hybrid.snapshot().title, "HA Fallback Track")
        self.assertGreater(hybrid._fallback_until, time.monotonic() + 40.0)

    def test_hybrid_source_recovers_to_spotify_after_fallback(self) -> None:
        ha_mock = Mock(spec=HomeAssistantSource)
        ha_mock.connected = True
        ha_mock.snapshot.return_value = PlaybackState(available=True, title="HA Track")
        spotify_mock = Mock()
        spotify_mock.get_currently_playing.side_effect = SpotifyRateLimited(
            retry_after=60.0
        )

        hybrid = HybridPlaybackSource(ha_mock, spotify_mock)
        hybrid._poll_once()
        self.assertEqual(hybrid.active_provider, "home_assistant")

        # Simulate fallback expiration and successful Spotify poll
        hybrid._fallback_until = 0.0
        spotify_mock.get_currently_playing.side_effect = None
        spotify_mock.get_currently_playing.return_value = {
            "is_playing": False,
            "item": {
                "name": "Recovered Spotify",
                "artists": [{"name": "Artist"}],
                "album": {"images": []},
            },
        }
        delay = hybrid._poll_once()

        self.assertEqual(
            delay, 1.0
        )  # active paused delay matches playing rate for fast resume
        self.assertEqual(hybrid.active_provider, "spotify")
        self.assertEqual(hybrid.snapshot().title, "Recovered Spotify")
        self.assertFalse(hybrid.snapshot().playing)

        # After active_window expires, paused polling backs off to conserve quota
        hybrid._last_active_at = time.monotonic() - 200.0
        backed_off_delay = hybrid._poll_once()
        self.assertEqual(backed_off_delay, 2.5)

    def test_hybrid_source_paused_and_idle_intervals(self) -> None:
        ha_mock = Mock(spec=HomeAssistantSource)
        ha_mock.connected = True
        spotify_mock = Mock()

        # Explicit paused interval override
        hybrid = HybridPlaybackSource(
            ha_mock,
            spotify_mock,
            poll_interval_playing=1.0,
            poll_interval_paused=0.8,
            poll_interval_idle=4.0,
        )
        spotify_mock.get_currently_playing.return_value = {
            "is_playing": False,
            "item": {"name": "Track", "artists": [], "album": {"images": []}},
        }
        delay = hybrid._poll_once()
        self.assertEqual(delay, 0.8)

        # 204 idle response recently active vs long idle
        spotify_mock.get_currently_playing.return_value = None
        hybrid._last_active_at = time.monotonic() - 10.0
        recent_idle_delay = hybrid._poll_once()
        self.assertEqual(recent_idle_delay, 2.5)

        hybrid._last_active_at = time.monotonic() - 100.0
        long_idle_delay = hybrid._poll_once()
        self.assertEqual(long_idle_delay, 4.0)

    def test_hybrid_source_falls_back_on_quota_and_network_error(self) -> None:
        ha_mock = Mock(spec=HomeAssistantSource)
        ha_mock.connected = True
        ha_mock.snapshot.return_value = PlaybackState(available=True, title="HA Safety")
        spotify_mock = Mock()

        hybrid = HybridPlaybackSource(
            ha_mock, spotify_mock, quota_cooldown=1800.0, error_cooldown=10.0
        )

        # Quota exceeded
        spotify_mock.get_currently_playing.side_effect = SpotifyQuotaExceeded("Quota")
        delay = hybrid._poll_once()
        self.assertEqual(delay, 1800.0)
        self.assertEqual(hybrid.active_provider, "home_assistant")
        self.assertEqual(hybrid.snapshot().title, "HA Safety")

        # Network error
        hybrid._fallback_until = 0.0
        import requests

        spotify_mock.get_currently_playing.side_effect = requests.ConnectionError(
            "DNS failure"
        )
        delay = hybrid._poll_once()
        self.assertEqual(delay, 10.0)
        self.assertEqual(hybrid.active_provider, "home_assistant")
        self.assertEqual(hybrid.snapshot().title, "HA Safety")

    def test_hybrid_source_delegates_artwork_fetch(self) -> None:
        ha_mock = Mock(spec=HomeAssistantSource)
        dummy_img = Image.new("RGB", (16, 16), "blue")
        ha_mock.fetch_artwork.return_value = dummy_img
        spotify_mock = Mock()

        hybrid = HybridPlaybackSource(ha_mock, spotify_mock)
        img = hybrid.fetch_artwork("https://i.scdn.co/image/art")
        ha_mock.fetch_artwork.assert_called_once_with("https://i.scdn.co/image/art")
        self.assertEqual(img, dummy_img)

    def test_ha_update_triggers_wake_event(self) -> None:
        ha_source = HomeAssistantSource("http://127.0.0.1:8123", "test-token", ENTITY)
        spotify_mock = Mock()
        hybrid = HybridPlaybackSource(ha_source, spotify_mock)

        self.assertFalse(hybrid._wake_event.is_set())
        ha_source.apply_state(state("Waking track"))
        self.assertTrue(hybrid._wake_event.is_set())

    def test_hybrid_source_delegates_hub_state(self) -> None:
        ha_mock = Mock(spec=HomeAssistantSource)
        from modes.base import HAState

        dummy_state = HAState(power=True, mode="Night Clock", brightness=0.4)
        ha_mock.hub_state.return_value = dummy_state
        hybrid = HybridPlaybackSource(ha_mock, Mock())
        self.assertEqual(hybrid.hub_state(), dummy_state)

    def test_hybrid_source_does_not_poll_when_power_off_or_unrelated_mode(
        self,
    ) -> None:
        ha_mock = Mock(spec=HomeAssistantSource)
        ha_mock.connected = True
        spotify_mock = Mock()
        from modes.base import HAState

        hybrid = HybridPlaybackSource(ha_mock, spotify_mock)

        # Power off: should not poll
        ha_mock.hub_state.return_value = HAState(power=False, mode="Spotify")
        self.assertFalse(hybrid._should_poll_spotify())

        # Mode Off: should not poll
        ha_mock.hub_state.return_value = HAState(power=True, mode="Off")
        self.assertFalse(hybrid._should_poll_spotify())

        # Unrelated modes should not poll
        for mode in (
            "Clock & Weather",
            "Night Clock",
            "AI Arcade",
            "Retro Arcade",
            "Arcade",
            "2-Player Arcade",
            "Game of Life",
            "Physics",
            "Homelab Dashboards",
            "LedFx",
        ):
            ha_mock.hub_state.return_value = HAState(power=True, mode=mode)
            self.assertFalse(
                hybrid._should_poll_spotify(), f"Expected False for mode {mode}"
            )

        # Mode Spotify: should poll
        ha_mock.hub_state.return_value = HAState(power=True, mode="Spotify")
        self.assertTrue(hybrid._should_poll_spotify())

        # Mode Auto: should poll
        ha_mock.hub_state.return_value = HAState(power=True, mode="Auto")
        self.assertTrue(hybrid._should_poll_spotify())

    def test_hub_update_wakes_hybrid_poller(self) -> None:
        ha_source = HomeAssistantSource(
            "http://127.0.0.1:8123",
            "test-token",
            ENTITY,
            power_entity="input_boolean.bedroom_matrix_power",
        )
        spotify_mock = Mock()
        hybrid = HybridPlaybackSource(ha_source, spotify_mock)

        hub_updates = []
        hybrid.on_hub_update = lambda state: hub_updates.append(state)

        self.assertFalse(hybrid._wake_event.is_set())
        ha_source._apply_hub_entity("input_boolean.bedroom_matrix_power", {"s": "off"})
        self.assertTrue(hybrid._wake_event.is_set())
        self.assertEqual(len(hub_updates), 1)

    def test_multi_entity_media_player_prioritizes_active_playing_and_fallbacks(
        self,
    ) -> None:
        source = HomeAssistantSource(
            "http://127.0.0.1:8123",
            "test-token",
            "media_player.spotify_rupan,media_player.bedroom_speaker",
        )
        source._connected = True
        self.assertEqual(
            source.entities,
            ["media_player.spotify_rupan", "media_player.bedroom_speaker"],
        )
        self.assertEqual(source.entity, "media_player.spotify_rupan")

        # Spotify is paused
        spotify_state = {
            "entity_id": "media_player.spotify_rupan",
            "state": "paused",
            "last_updated": datetime.fromtimestamp(time.time() - 10, UTC).isoformat(),
            "attributes": {
                "media_title": "Spotify Paused",
                "media_artist": "Artist 1",
                "entity_picture": "https://i.scdn.co/image/spot1",
            },
        }
        source.apply_state(spotify_state)
        self.assertEqual(source.snapshot().title, "Spotify Paused")

        # Bedroom speaker starts playing
        speaker_state = {
            "entity_id": "media_player.bedroom_speaker",
            "state": "playing",
            "last_updated": datetime.fromtimestamp(time.time() - 2, UTC).isoformat(),
            "attributes": {
                "media_title": "Hop Off A Jet",
                "media_artist": "Young Thug",
                "entity_picture": "/api/media_player_proxy/media_player.bedroom_speaker?token=xyz",
            },
        }
        source.apply_state(speaker_state)
        snap = source.snapshot()
        self.assertEqual(snap.title, "Hop Off A Jet")
        self.assertTrue(snap.playing)
        self.assertEqual(
            snap.artwork_url,
            "/api/media_player_proxy/media_player.bedroom_speaker?token=xyz",
        )

        # Bedroom speaker stops (becomes idle), snapshot falls back to Spotify Paused
        speaker_idle = {
            "entity_id": "media_player.bedroom_speaker",
            "state": "idle",
            "last_updated": datetime.fromtimestamp(time.time(), UTC).isoformat(),
            "attributes": {},
        }
        source.apply_state(speaker_idle)
        snap = source.snapshot()
        self.assertEqual(snap.title, "Spotify Paused")
        self.assertFalse(snap.playing)


class HubModeAndRendererTests(unittest.TestCase):
    def test_hub_state_multi_entity_parsing_and_deltas(self) -> None:
        source = HomeAssistantSource(
            base_url="http://127.0.0.1:8123",
            token="test-token",
            entity=ENTITY,
            power_entity="input_boolean.bedroom_matrix_power",
            mode_entity="input_select.bedroom_matrix_mode",
            brightness_entity="input_number.bedroom_matrix_brightness",
            weather_entity="weather.forecast_home",
        )
        source._connected = True

        # Initial compact snapshot with all hub entities
        source.handle_event(
            {
                "id": 1,
                "type": "event",
                "event": {
                    "a": {
                        "input_boolean.bedroom_matrix_power": {"s": "on"},
                        "input_select.bedroom_matrix_mode": {"s": "Clock & Weather"},
                        "input_number.bedroom_matrix_brightness": {"s": "65"},
                        "weather.forecast_home": {
                            "s": "sunny",
                            "a": {"temperature": 75.5, "temperature_unit": "°F"},
                        },
                    }
                },
            }
        )

        hub = source.hub_state()
        self.assertTrue(hub.power)
        self.assertEqual(hub.mode, "Clock & Weather")
        self.assertAlmostEqual(hub.brightness, 0.65, places=2)
        self.assertEqual(hub.weather_condition, "sunny")
        self.assertAlmostEqual(hub.weather_temp, 75.5, places=1)
        self.assertEqual(hub.weather_unit, "°F")

        # Delta event: power turned off, mode changed to Night Clock
        source.handle_event(
            {
                "id": 1,
                "type": "event",
                "event": {
                    "c": {
                        "input_boolean.bedroom_matrix_power": {"+": {"s": "off"}},
                        "input_select.bedroom_matrix_mode": {"+": {"s": "Night Clock"}},
                        "weather.forecast_home": {
                            "+": {
                                "s": "rainy",
                                "a": {"temperature": 62.0},
                            }
                        },
                    }
                },
            }
        )

        updated_hub = source.hub_state()
        self.assertFalse(updated_hub.power)
        self.assertEqual(updated_hub.mode, "Night Clock")
        self.assertEqual(updated_hub.weather_condition, "rainy")
        self.assertAlmostEqual(updated_hub.weather_temp, 62.0, places=1)

    def test_hub_state_brightness_clamping_and_scaling(self) -> None:
        source = HomeAssistantSource(
            base_url="http://127.0.0.1:8123",
            token="test-token",
            entity=ENTITY,
            brightness_entity="input_number.bedroom_matrix_brightness",
        )
        # 100% -> 1.0
        source.handle_event(
            {
                "id": 1,
                "type": "event",
                "event": {
                    "a": {"input_number.bedroom_matrix_brightness": {"s": "100"}}
                },
            }
        )
        self.assertAlmostEqual(source.hub_state().brightness, 1.0)

        # 0.40 -> 0.40
        source.handle_event(
            {
                "id": 1,
                "type": "event",
                "event": {
                    "c": {
                        "input_number.bedroom_matrix_brightness": {"+": {"s": "0.40"}}
                    }
                },
            }
        )
        self.assertAlmostEqual(source.hub_state().brightness, 0.40)

        # Low bound clamp (5% -> 0.05)
        source.handle_event(
            {
                "id": 1,
                "type": "event",
                "event": {
                    "c": {"input_number.bedroom_matrix_brightness": {"+": {"s": "2"}}}
                },
            }
        )
        self.assertAlmostEqual(source.hub_state().brightness, 0.05)

    def test_mode_renderers_render_valid_frames(self) -> None:
        from modes import (
            BlankRenderer,
            ClockWeatherRenderer,
            NightClockRenderer,
            SpotifyVinylRenderer,
        )
        from modes.base import HAState, PlaybackState, RenderContext

        ha_state = HAState(
            connected=True,
            power=True,
            mode="Auto",
            brightness=0.7,
            weather_temp=68.0,
            weather_condition="partlycloudy",
        )
        spotify_state = PlaybackState(
            available=True,
            playing=True,
            title="Song",
            artist="Artist",
        )
        dummy_art = Image.new("RGB", (64, 64), (100, 150, 200))
        ctx = RenderContext(
            tick=100.0,
            dt=0.05,
            ha_state=ha_state,
            spotify_state=spotify_state,
            spotify_art=dummy_art,
            brightness=0.7,
            rotation_speed=45.0,
        )

        # 1. Blank
        blank_frame = BlankRenderer().render(ctx, size=64)
        self.assertEqual(blank_frame.size, (64, 64))
        self.assertEqual(blank_frame.mode, "RGB")
        self.assertEqual(blank_frame.getpixel((0, 0)), (0, 0, 0))

        # 2. Spotify Vinyl
        vinyl = SpotifyVinylRenderer()
        frame1 = vinyl.render(ctx, size=64)
        self.assertEqual(frame1.size, (64, 64))
        self.assertEqual(frame1.mode, "RGB")
        self.assertGreater(vinyl.angle, 0.0)

        # 3. Clock & Weather
        cw_frame = ClockWeatherRenderer().render(ctx, size=64)
        self.assertEqual(cw_frame.size, (64, 64))
        self.assertEqual(cw_frame.mode, "RGB")

        # 4. Night Clock
        nc_frame = NightClockRenderer().render(ctx, size=64)
        self.assertEqual(nc_frame.size, (64, 64))
        self.assertEqual(nc_frame.mode, "RGB")

        # 5. Retro Arcade
        from modes import RetroArcadeRenderer

        arcade = RetroArcadeRenderer(cycle_interval=2.0, randomize=False)
        self.assertEqual(arcade.current_game.name, "PONG AI")
        a_frame1 = arcade.render(ctx, size=64)
        self.assertEqual(a_frame1.size, (64, 64))
        self.assertEqual(a_frame1.mode, "RGB")
        # Game of Life is a standalone mode, not part of the arcade rotation
        self.assertNotIn("GAME OF LIFE", [game.name for game in arcade.games])

        # Advance past cycle_interval -> rotates to next game (Snake)
        ctx_adv = RenderContext(
            tick=103.0,
            dt=2.5,
            ha_state=ha_state,
            spotify_state=spotify_state,
            spotify_art=dummy_art,
        )
        a_frame2 = arcade.render(ctx_adv, size=64)
        self.assertEqual(arcade.current_game.name, "SNAKE AI")
        self.assertEqual(a_frame2.size, (64, 64))

        # 6. Game of Life (standalone mode)
        from modes import LifeRenderer

        life = LifeRenderer()
        life_frame = life.render(ctx, size=64)
        self.assertEqual(life_frame.size, (64, 64))
        self.assertEqual(life_frame.mode, "RGB")

        # Test individual arcade games
        from modes.arcade import (
            BreakoutGame,
            DonkeyKongGame,
            PacmanGame,
            PongGame,
            SnakeGame,
            SpaceInvadersGame,
            TetrisGame,
        )

        for game in [
            PongGame(),
            SnakeGame(),
            BreakoutGame(),
            TetrisGame(),
            PacmanGame(),
            SpaceInvadersGame(),
            DonkeyKongGame(),
        ]:
            game.reset()
            game.update(0.05)
            img = Image.new("RGB", (64, 64))
            draw = ImageDraw.Draw(img)
            game.draw(img, draw, size=64)
            self.assertEqual(img.size, (64, 64))
            self.assertEqual(img.mode, "RGB")

    def test_breakout_bricks_clear_of_score_hud(self) -> None:
        from modes.arcade import BreakoutGame

        game = BreakoutGame()
        # Bricks must sit below the top HUD band that holds the score
        for brick in game.bricks:
            self.assertGreaterEqual(brick.y, 12)

    def test_multiplayer_hub_and_roles(self) -> None:
        from web_controller import MultiplayerHub

        hub = MultiplayerHub()
        self.assertFalse(hub.p1_connected)
        self.assertFalse(hub.p2_connected)
        self.assertEqual(hub.get_lobby_state()["phase"], "lobby")

        dummy_ws1 = object()
        dummy_ws2 = object()
        dummy_ws3 = object()

        r1 = hub.register_client(dummy_ws1)  # type: ignore
        self.assertEqual(r1, 1)
        self.assertTrue(hub.p1_connected)

        r2 = hub.register_client(dummy_ws2)  # type: ignore
        self.assertEqual(r2, 2)
        self.assertTrue(hub.p2_connected)

        r3 = hub.register_client(dummy_ws3)  # type: ignore
        self.assertEqual(r3, 0)  # Spectator

        # Test input handling
        hub.handle_input(1, "up", "down")
        self.assertIn("up", hub.p1_inputs)
        hub.handle_input(1, "up", "up")
        self.assertNotIn("up", hub.p1_inputs)

        # Test unregister
        hub.unregister_client(dummy_ws2)  # type: ignore
        self.assertFalse(hub.p2_connected)
        hub.unregister_client(dummy_ws1)  # type: ignore
        self.assertFalse(hub.p1_connected)

    def test_multiplayer_pong(self) -> None:
        from modes.multiplayer import Pong2P

        pong = Pong2P(solo_vs_ai=False)
        pong.reset()
        self.assertFalse(pong.is_game_over())
        self.assertEqual(pong.winner, 0)

        # Move P1 up and P2 down
        p1_orig = pong.p1_y
        p2_orig = pong.p2_y
        pong.update(0.1, {"up"}, {"down"})
        self.assertLess(pong.p1_y, p1_orig)
        self.assertGreater(pong.p2_y, p2_orig)

        # Render frame
        img = Image.new("RGB", (64, 64))
        draw = ImageDraw.Draw(img)
        pong.render(draw)
        self.assertEqual(img.size, (64, 64))

        # Test AI mode
        pong_ai = Pong2P(solo_vs_ai=True)
        pong_ai.ball_y = 50.0
        pong_ai.p2_y = 10.0
        pong_ai.update(0.1, set(), set())
        self.assertGreater(pong_ai.p2_y, 10.0)

    def test_multiplayer_tron(self) -> None:
        from modes.multiplayer import Tron2P

        tron = Tron2P(solo_vs_ai=False)
        tron.reset()
        tron.round_delay = 0.0
        self.assertFalse(tron.is_game_over())
        self.assertEqual(tron.current_round, 1)

        # Steer P1 down
        tron.update(0.1, {"down"}, set())
        self.assertEqual(tron.p1_dir, "down")

        # Reverse lock (cannot instantly steer up)
        tron.update(0.1, {"up"}, set())
        self.assertEqual(tron.p1_dir, "down")

        # Render frame
        img = Image.new("RGB", (64, 64))
        draw = ImageDraw.Draw(img)
        tron.render(draw)
        self.assertEqual(img.size, (64, 64))

    def test_multiplayer_arcade_renderer_phases(self) -> None:
        from modes.base import HAState, PlaybackState, RenderContext
        from modes.multiplayer import MultiplayerArcadeRenderer
        from web_controller import MultiplayerHub

        hub = MultiplayerHub()
        renderer = MultiplayerArcadeRenderer(hub)

        # 1. Lobby Phase
        self.assertEqual(hub.phase, "lobby")
        img_lobby = renderer.render()
        self.assertEqual(img_lobby.size, (64, 64))

        # 2. Start Countdown
        hub.trigger_game_start("pong", solo=False)
        self.assertEqual(hub.phase, "countdown")
        img_cd = renderer.render()
        self.assertEqual(img_cd.size, (64, 64))

        # 3. Fast-forward countdown into Playing phase
        hub.countdown_remaining = 0.001
        renderer.render()
        self.assertEqual(hub.phase, "playing")
        img_play = renderer.render()
        self.assertEqual(img_play.size, (64, 64))

        # 4. Render with RenderContext and explicit size
        ctx = RenderContext(
            tick=time.monotonic(),
            dt=0.05,
            ha_state=HAState(connected=True, power=True, mode="2-Player Arcade"),
            spotify_state=PlaybackState(),
            spotify_art=None,
        )
        img_ctx = renderer.render(ctx, size=64)
        self.assertEqual(img_ctx.size, (64, 64))

    def test_web_controller_http_endpoints(self) -> None:
        from web_controller.server import MultiplayerHub, WebControllerServer

        hub = MultiplayerHub()
        server = WebControllerServer(hub)

        class DummyConnection:
            pass

        class DummyRequest:
            def __init__(self, path: str):
                self.path = path

        conn = DummyConnection()
        resp_root = server.process_request(conn, DummyRequest("/"))  # type: ignore
        self.assertIsNotNone(resp_root)
        self.assertEqual(resp_root.status_code, 200)
        self.assertIn(b"LED MATRIX ARCADE", resp_root.body)

        resp_health = server.process_request(conn, DummyRequest("/health"))  # type: ignore
        self.assertIsNotNone(resp_health)
        self.assertEqual(resp_health.status_code, 200)
        self.assertIn(b"healthy", resp_health.body)

        resp_ws = server.process_request(conn, DummyRequest("/ws"))  # type: ignore
        self.assertIsNone(resp_ws)  # Upgrade to WebSocket

    def test_arcade_rotation_includes_new_games(self) -> None:
        from modes.arcade import RetroArcadeRenderer

        names = [game.name for game in RetroArcadeRenderer().games]
        self.assertIn("TETRIS AI", names)
        self.assertIn("PAC-MAN", names)
        self.assertIn("SPACE INVADERS", names)
        self.assertIn("DONKEY KONG", names)
        self.assertIn("FROGGER", names)
        self.assertIn("ASTEROIDS", names)
        self.assertNotIn("DUNGEON", names)

    def test_tetris_ai_stacks_pieces(self) -> None:
        from modes.arcade import TetrisGame

        game = TetrisGame()
        filled = 0
        for _ in range(300):
            game.update(0.05)
            filled = max(filled, sum(1 for row in game.board for cell in row if cell))
        self.assertGreater(filled, 0)

    def test_pacman_ai_eats_and_navigates(self) -> None:
        from modes.arcade import PacmanGame

        game = PacmanGame()
        initial_dots = len(game.dots)
        for _ in range(200):
            game.update(0.05)
        # Pac-man should have eaten dots or moved
        self.assertNotEqual((game.pac_x, game.pac_y), (7.0, 9.0))
        self.assertLessEqual(len(game.dots), initial_dots)

    def test_space_invaders_sim(self) -> None:
        from modes.arcade import SpaceInvadersGame

        game = SpaceInvadersGame()
        positions = set()
        for _ in range(60):
            game.update(0.05)
            positions.add((game.fleet_x, game.fleet_y))
        # Fleet should march through multiple positions
        self.assertGreater(len(positions), 1)

    def test_donkey_kong_sim(self) -> None:
        from modes.arcade import DonkeyKongGame

        game = DonkeyKongGame()
        positions = set()
        for _ in range(60):
            game.update(0.05)
            positions.add((round(game.mario_x, 1), round(game.mario_y, 1)))
        # Mario should run along girders
        self.assertGreater(len(positions), 1)

    def test_frogger_sim(self) -> None:
        from modes.arcade import FroggerGame

        game = FroggerGame()
        positions = set()
        for _ in range(80):
            game.update(0.05)
            positions.add((round(game.frog_x, 1), round(game.frog_y, 1)))
        # Autonomous frog should navigate across lanes
        self.assertGreater(len(positions), 1)

    def test_asteroids_ai_sim(self) -> None:
        from modes.arcade import AsteroidsGame

        game = AsteroidsGame()
        for _ in range(100):
            game.update(0.04)
        # Simulation runs smoothly with active ship and asteroids
        self.assertGreaterEqual(len(game.asteroids), 1)
        self.assertGreaterEqual(game.score, 0)

    def test_multiplayer_tron_event_queue(self) -> None:
        from modes.multiplayer import Tron2P

        tron = Tron2P(solo_vs_ai=False)
        tron.reset()
        tron.round_delay = 0.0

        # Initial direction is right
        self.assertEqual(tron.p1_dir, "right")

        # Discrete tap "down" in p1_events
        tron.update(0.1, set(), set(), p1_events=["down"])
        self.assertEqual(tron.p1_dir, "down")

        # Next discrete tap "left" in p1_events (should turn left, not stuck in up or down)
        tron.update(0.1, set(), set(), p1_events=["left"])
        self.assertEqual(tron.p1_dir, "left")

        # Next discrete tap "up" in p1_events
        tron.update(0.1, set(), set(), p1_events=["up"])
        self.assertEqual(tron.p1_dir, "up")

        # Instant 180 reverse attempt ("down") should be rejected
        tron.update(0.1, set(), set(), p1_events=["down"])
        self.assertEqual(tron.p1_dir, "up")

    def test_interactive_snake(self) -> None:
        from modes.multiplayer import InteractiveSnake

        snake = InteractiveSnake()
        snake.reset()
        self.assertFalse(snake.is_game_over())
        self.assertEqual(snake.score, 0)
        self.assertEqual(len(snake.snake), 4)

        # Steer down
        snake.update(0.1, set(), set(), p1_events=["down"])
        self.assertEqual(snake.current_dir, "down")

        # Steer left
        snake.update(0.1, set(), set(), p1_events=["left"])
        self.assertEqual(snake.current_dir, "left")

        # Render
        img = Image.new("RGB", (64, 64))
        draw = ImageDraw.Draw(img)
        snake.render(draw)
        self.assertEqual(img.size, (64, 64))

        # Status text
        status = snake.get_status_text()
        self.assertIn("SCORE:", status)

    def test_interactive_breakout(self) -> None:
        from modes.multiplayer import InteractiveBreakout

        game = InteractiveBreakout()
        game.reset()
        self.assertFalse(game.is_game_over())
        self.assertEqual(game.lives, 3)

        # Move paddle left
        orig_x = game.paddle_x
        game.update(0.1, {"left"}, set())
        self.assertLess(game.paddle_x, orig_x)

        # Move paddle right
        orig_x = game.paddle_x
        game.update(0.1, {"right"}, set())
        self.assertGreater(game.paddle_x, orig_x)

        # Render
        img = Image.new("RGB", (64, 64))
        draw = ImageDraw.Draw(img)
        game.render(draw)
        self.assertEqual(img.size, (64, 64))

    def test_interactive_tetris(self) -> None:
        from modes.multiplayer import InteractiveTetris

        game = InteractiveTetris()
        game.reset()
        self.assertFalse(game.is_game_over())

        # Move piece left
        orig_px = game.piece_x
        game.update(0.01, set(), set(), p1_events=["left"])
        self.assertLessEqual(game.piece_x, orig_px)

        # Rotate piece
        game.update(0.01, set(), set(), p1_events=["rotate"])

        # Hard drop
        game.update(0.01, set(), set(), p1_events=["hard_drop"])
        self.assertGreater(game.score, 0)

        # Render
        img = Image.new("RGB", (64, 64))
        draw = ImageDraw.Draw(img)
        game.render(draw)
        self.assertEqual(img.size, (64, 64))

    def test_carousel_and_menu_navigation(self) -> None:
        from modes.multiplayer import CarouselRenderer, LobbyRenderer
        from web_controller.server import MultiplayerHub

        hub = MultiplayerHub()
        carousel = CarouselRenderer(hub)
        lobby = LobbyRenderer(hub)

        # Initial game index 0 (pong)
        self.assertEqual(hub.selected_game_index, 0)
        self.assertEqual(hub.selected_game, "pong")

        # Navigate next -> tron
        hub.navigate_menu("next")
        self.assertEqual(hub.selected_game_index, 1)
        self.assertEqual(hub.selected_game, "tron")

        # Navigate prev -> pong
        hub.navigate_menu("prev")
        self.assertEqual(hub.selected_game_index, 0)
        self.assertEqual(hub.selected_game, "pong")

        # Update carousel animation
        carousel.update(0.1)

        # Render carousel
        img = Image.new("RGB", (64, 64))
        draw = ImageDraw.Draw(img)
        carousel.render(draw)
        self.assertEqual(img.size, (64, 64))

        # When no players connected, lobby renders QR code
        self.assertFalse(hub.p1_connected)
        lobby.render_lobby(draw)

        # When P1 connects, lobby renders Carousel wheel
        hub.p1_ws = object()  # dummy connection
        self.assertTrue(hub.p1_connected)
        lobby.render_lobby(draw)

        # Toggle QR forced
        hub.toggle_matrix_qr()
        self.assertEqual(hub.matrix_view, "qr")
        lobby.render_lobby(draw)

    def test_life_palette_rotation_default_interval(self) -> None:
        from modes import DEFAULT_PALETTES, LifeRenderer

        life = LifeRenderer()
        # Default palette rotation interval is 2 minutes (120 seconds)
        self.assertEqual(life.palette_interval, 120.0)
        self.assertGreaterEqual(len(DEFAULT_PALETTES), 4)
        self.assertEqual(life.current_palette.name, "Cyberpunk")
        self.assertEqual(life.palette_idx, 0)

        # Updating by 60 seconds (1 minute) should not rotate palette yet
        life.update(60.0)
        self.assertEqual(life.current_palette.name, "Cyberpunk")
        self.assertEqual(life.palette_idx, 0)

        # Another 60 seconds (total 120s / 2 minutes) rotates to next palette
        life.update(60.0)
        self.assertEqual(life.palette_idx, 1)
        self.assertEqual(life.current_palette.name, "Amber CRT")

        # Another 120 seconds rotates to palette 2
        life.update(120.0)
        self.assertEqual(life.palette_idx, 2)
        self.assertEqual(life.current_palette.name, "Matrix Terminal")

    def test_life_palette_full_rotation_cycle(self) -> None:
        from modes import DEFAULT_PALETTES, LifeRenderer

        life = LifeRenderer(palette_interval=120.0)
        total_palettes = len(DEFAULT_PALETTES)

        # Step through every palette in sequence
        for idx in range(total_palettes):
            self.assertEqual(life.palette_idx, idx)
            self.assertEqual(life.current_palette, DEFAULT_PALETTES[idx])
            life.update(120.0)

        # Verify clean wrap-around back to the first palette
        self.assertEqual(life.palette_idx, 0)
        self.assertEqual(life.current_palette, DEFAULT_PALETTES[0])

    def test_life_palette_manual_selection(self) -> None:
        from modes import LifeRenderer

        life = LifeRenderer()
        life.set_palette("Matrix Terminal")
        self.assertEqual(life.current_palette.name, "Matrix Terminal")
        self.assertEqual(life.palette_idx, 2)

        # Select by index
        life.set_palette(3)
        self.assertEqual(life.current_palette.name, "Synthwave")

        with self.assertRaises(ValueError):
            life.set_palette("nonexistent_palette")

    def test_life_palette_render_colors(self) -> None:
        from modes import HAState, LifeRenderer, PlaybackState, RenderContext

        life = LifeRenderer()
        ctx = RenderContext(
            tick=10.0,
            dt=0.05,
            ha_state=HAState(),
            spotify_state=PlaybackState(),
            spotify_art=None,
        )

        # Render first palette (Cyberpunk)
        frame1 = life.render(ctx, size=64)
        self.assertEqual(frame1.size, (64, 64))
        # Top-left corner is background (3, 5, 10)
        self.assertEqual(frame1.getpixel((0, 0)), (3, 5, 10))

        # Rotate to Amber CRT palette
        life.set_palette("Amber CRT")
        frame2 = life.render(ctx, size=64)
        # Background color should now match Amber CRT bg (8, 4, 1)
        self.assertEqual(frame2.getpixel((0, 0)), (8, 4, 1))

    def test_life_reset_preserves_palette_timer(self) -> None:
        from modes import LifeRenderer

        life = LifeRenderer(palette_interval=120.0)
        life.update(150.0)  # Advances to palette 1, 30s into next 2min cycle
        self.assertEqual(life.palette_idx, 1)
        self.assertAlmostEqual(life.palette_timer, 30.0)

        # Calling reset (e.g. on cell stagnation or extinction) preserves palette progression
        life.reset()
        self.assertEqual(life.palette_idx, 1)
        self.assertAlmostEqual(life.palette_timer, 30.0)

    def test_visualizer_effects_and_cycling(self) -> None:
        from modes import HAState, PlaybackState, RenderContext, VisualizerRenderer

        vis = VisualizerRenderer(cycle_interval=60.0)
        self.assertEqual(len(vis.effects), 5)
        names = [eff.name for eff in vis.effects]
        self.assertIn("RADIAL SPECTRUM", names)
        self.assertIn("HYPERSPACE TUNNEL", names)
        self.assertIn("PARTICLE FOUNTAIN", names)
        self.assertIn("GRAPHIC EQUALIZER", names)
        self.assertIn("OSCILLOSCOPE RIBBON", names)

        ctx = RenderContext(
            tick=1.0,
            dt=0.05,
            ha_state=HAState(),
            spotify_state=PlaybackState(available=True, playing=True),
            spotify_art=None,
        )

        # Test rendering all effects individually
        for idx in range(len(vis.effects)):
            vis.set_effect(idx)
            frame = vis.render(ctx, size=64)
            self.assertEqual(frame.size, (64, 64))
            self.assertEqual(frame.mode, "RGB")

        # Test cycle timer
        vis.set_effect(0)
        self.assertEqual(vis.current_idx, 0)
        vis.update(30.0, 0.5)
        self.assertEqual(vis.current_idx, 0)
        vis.update(31.0, 0.5)
        self.assertEqual(vis.current_idx, 1)

    def test_ledfx_receiver_ddp_and_fallback(self) -> None:
        from modes import (
            HAState,
            LedFxReceiver,
            LedFxRenderer,
            PlaybackState,
            RenderContext,
            VisualizerRenderer,
        )

        receiver = LedFxReceiver(size=64)
        self.assertFalse(receiver.has_recent_frame())
        self.assertIsNone(receiver.get_frame())

        # Construct a synthetic DDP packet: 10 byte header + 12288 bytes of RGB
        header = bytearray(10)
        header[0] = 0x41
        header[1] = 0x00
        header[2] = 0x01
        header[3] = 0x01
        header[4:8] = (0).to_bytes(4, "big")
        header[8:10] = (12288).to_bytes(2, "big")
        payload = bytes([100, 150, 200] * 4096)
        packet = bytes(header) + payload

        receiver.process_packet(packet)
        self.assertTrue(receiver.has_recent_frame(timeout=1.0))
        frame = receiver.get_frame()
        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertEqual(frame.size, (64, 64))
        self.assertEqual(frame.getpixel((0, 0)), (100, 150, 200))

        # Test LedFxRenderer with active frame
        renderer = LedFxRenderer(receiver=receiver)
        ctx = RenderContext(
            tick=1.0,
            dt=0.05,
            ha_state=HAState(),
            spotify_state=PlaybackState(),
            spotify_art=None,
        )
        rendered_frame = renderer.render(ctx, size=64)
        self.assertEqual(rendered_frame.getpixel((0, 0)), (100, 150, 200))

        # Test fallback when receiver has no recent frames
        empty_receiver = LedFxReceiver(size=64)
        fallback_renderer = VisualizerRenderer()
        renderer_fallback = LedFxRenderer(
            receiver=empty_receiver, fallback_renderer=fallback_renderer
        )
        fb_frame = renderer_fallback.render(ctx, size=64)
        self.assertEqual(fb_frame.size, (64, 64))
        self.assertEqual(fb_frame.mode, "RGB")

    def test_music_mode_auto_selection(self) -> None:
        from modes import HAState, PlaybackState

        # 1. When mode is Auto and music_mode is False, Spotify idle -> Clock & Weather
        state_idle = HAState(mode="Auto", music_mode=False)
        spot_idle = PlaybackState(available=True, playing=False)
        active_mode = (
            "LedFx"
            if state_idle.music_mode
            else ("Spotify" if spot_idle.playing else "Clock & Weather")
        )
        self.assertEqual(active_mode, "Clock & Weather")

        # 2. When mode is Auto and music_mode is True -> auto switches to LedFx
        state_music = HAState(mode="Auto", music_mode=True)
        active_mode = (
            "LedFx"
            if state_music.music_mode
            else ("Spotify" if spot_idle.playing else "Clock & Weather")
        )
        self.assertEqual(active_mode, "LedFx")

        # 3. When mode is explicitly Visualizer -> respects Visualizer regardless of music_mode
        state_vis = HAState(mode="Visualizer", music_mode=True)
        active_mode = state_vis.mode if state_vis.mode != "Auto" else "LedFx"
        self.assertEqual(active_mode, "Visualizer")

    def test_ledfx_receiver_audio_packet(self) -> None:
        import struct

        from modes.ledfx_mode import LedFxReceiver

        receiver = LedFxReceiver(size=64)
        self.assertIsNone(receiver.get_latest_audio())

        # Construct a 76-byte FFT packet
        test_bands = [float(i) / 15.0 for i in range(16)]
        payload = b"FFT1" + struct.pack("!ff16f", 0.45, 0.85, *test_bands)
        receiver.process_packet(payload)

        audio = receiver.get_latest_audio(timeout=2.0)
        self.assertIsNotNone(audio)
        self.assertAlmostEqual(audio.rms, 0.45, places=5)
        self.assertAlmostEqual(audio.beat, 0.85, places=5)
        self.assertEqual(len(audio.bands), 16)
        self.assertAlmostEqual(audio.bands[0], 0.0, places=5)
        self.assertAlmostEqual(audio.bands[15], 1.0, places=5)

        # Timeout should return None
        expired = receiver.get_latest_audio(timeout=0.0)
        self.assertIsNone(expired)

    def test_visualizer_audio_reactivity(self) -> None:
        from modes.base import HAState, PlaybackState, RenderContext
        from modes.ledfx_mode import AudioFrame
        from modes.visualizer import VisualizerRenderer

        frame_data = AudioFrame(
            rms=0.5,
            beat=0.9,
            bands=[0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1] * 2,
            timestamp=100.0,
        )
        vis = VisualizerRenderer(size=64, audio_source=lambda: frame_data)
        ctx = RenderContext(
            tick=10.0,
            dt=0.05,
            ha_state=HAState(),
            spotify_state=PlaybackState(),
            spotify_art=None,
        )

        for _ in range(len(vis.effects)):
            img = vis.render(ctx, size=64)
            self.assertEqual(img.size, (64, 64))
            self.assertEqual(img.mode, "RGB")
            vis.update(60.0, beat=0.9)  # advance to next effect

        # When audio_source returns None, falls back to procedural generator
        vis_none = VisualizerRenderer(size=64, audio_source=lambda: None)
        img_fb = vis_none.render(ctx, size=64)
        self.assertEqual(img_fb.size, (64, 64))

    def test_retro_arcade_random_cycle_and_reset(self) -> None:
        import random

        from modes.arcade import RetroArcadeRenderer

        # Fix seed for reproducible assertions
        random.seed(12345)
        arcade = RetroArcadeRenderer(cycle_interval=1.0, randomize=True)
        self.assertEqual(len(arcade.playlist), len(arcade.games))
        self.assertEqual(set(arcade.playlist), set(range(len(arcade.games))))

        initial_playlist = list(arcade.playlist)

        # Reset should re-shuffle
        random.seed(67890)
        arcade.reset()
        reset_playlist = list(arcade.playlist)
        self.assertNotEqual(initial_playlist, reset_playlist)

        # Cycling through the full playlist covers all games without repeating consecutively
        visited = []
        for _ in range(len(arcade.games) * 2):
            visited.append(arcade.current_game.name)
            arcade._next_game()

        # Check no two consecutive games are the same
        for i in range(len(visited) - 1):
            self.assertNotEqual(
                visited[i],
                visited[i + 1],
                f"Duplicate consecutive game at {i}: {visited[i]}",
            )

    def test_multiplayer_arcade_thread_safety_and_game_change_stability(self) -> None:
        import threading
        import time

        from PIL import Image

        from modes.base import HAState, PlaybackState, RenderContext
        from modes.multiplayer import MultiplayerArcadeRenderer
        from web_controller.server import MultiplayerHub

        hub = MultiplayerHub()
        renderer = MultiplayerArcadeRenderer(hub)
        ctx = RenderContext(
            tick=1.0,
            dt=0.05,
            ha_state=HAState(power=True, mode="2-Player Arcade"),
            spotify_state=PlaybackState(),
            spotify_art=Image.new("RGB", (64, 64)),
        )

        stop = threading.Event()
        errors: list[Exception] = []

        def render_worker() -> None:
            while not stop.is_set():
                try:
                    frame = renderer.render(ctx, size=64)
                    self.assertEqual(frame.size, (64, 64))
                except Exception as e:  # noqa: BLE001
                    errors.append(e)
                time.sleep(0.005)

        t = threading.Thread(target=render_worker, daemon=True)
        t.start()

        # Concurrently perform rapid game changes, rematches, and menu exits
        for _ in range(15):
            renderer._on_start_game("pong", solo=False)
            time.sleep(0.005)
            renderer._on_start_game("tron", solo=True)
            time.sleep(0.005)
            renderer._on_start_game("snake", solo=True)
            time.sleep(0.005)
            renderer._on_start_game("breakout", solo=True)
            time.sleep(0.005)
            renderer._on_start_game("tetris", solo=True)
            time.sleep(0.005)
            renderer._on_rematch()
            time.sleep(0.005)
            renderer._on_menu_exit()
            time.sleep(0.005)

        stop.set()
        t.join(timeout=2.0)
        self.assertEqual(
            len(errors), 0, f"Encountered render errors during game changes: {errors}"
        )

    def test_tetris_safe_multiple_row_clears(self) -> None:
        from modes.multiplayer.tetris_game import COLS, ROWS, InteractiveTetris

        tetris = InteractiveTetris()
        # Fill multiple non-adjacent rows completely
        for x in range(COLS):
            tetris.board[ROWS - 1][x] = "I"
            tetris.board[ROWS - 3][x] = "T"
            tetris.board[ROWS - 5][x] = "O"

        tetris.clearing_rows = [ROWS - 5, ROWS - 3, ROWS - 1]
        tetris.clear_timer = 0.01
        # Update past clear_timer to trigger row removal
        tetris.update(0.02, set(), set())

        self.assertEqual(len(tetris.board), ROWS)
        for y in range(ROWS):
            self.assertEqual(len(tetris.board[y]), COLS)
        self.assertEqual(len(tetris.clearing_rows), 0)

    def test_multiplayer_hub_index_sync_and_clamping(self) -> None:
        from web_controller.server import GAMES_CONFIG, MultiplayerHub

        hub = MultiplayerHub()
        # Start game sets both selected_game and selected_game_index
        hub.trigger_game_start("breakout", solo=True)
        self.assertEqual(hub.selected_game, "breakout")
        expected_idx = next(
            i for i, g in enumerate(GAMES_CONFIG) if g["id"] == "breakout"
        )
        self.assertEqual(hub.selected_game_index, expected_idx)

        # set_selected_game
        hub.set_selected_game("tetris")
        self.assertEqual(hub.selected_game, "tetris")
        expected_tetris_idx = next(
            i for i, g in enumerate(GAMES_CONFIG) if g["id"] == "tetris"
        )
        self.assertEqual(hub.selected_game_index, expected_tetris_idx)

    def test_arcade_high_scores_persistence_and_records(self) -> None:
        import tempfile

        from modes.multiplayer.high_scores import HighScoreManager

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = f"{tmpdir}/scores.json"
            fallback_path = f"{tmpdir}/fallback.json"
            mgr = HighScoreManager(primary_path=file_path, fallback_path=fallback_path)

            self.assertEqual(mgr.get_score("tetris"), 0)
            self.assertEqual(mgr.get_score("snake"), 0)

            # Record higher score
            is_new, best = mgr.update_score("tetris", 1200)
            self.assertTrue(is_new)
            self.assertEqual(best, 1200)
            self.assertEqual(mgr.get_score("tetris"), 1200)

            # Record lower score - should not overwrite
            is_new2, best2 = mgr.update_score("tetris", 800)
            self.assertFalse(is_new2)
            self.assertEqual(best2, 1200)

            # Verify reload from disk
            mgr2 = HighScoreManager(primary_path=file_path, fallback_path=fallback_path)
            self.assertEqual(mgr2.get_score("tetris"), 1200)
            self.assertEqual(mgr2.get_score("snake"), 0)

    def test_multiplayer_hub_sfx_and_high_score_recording(self) -> None:
        import tempfile

        from modes.multiplayer.high_scores import HighScoreManager
        from web_controller.server import MultiplayerHub

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = HighScoreManager(primary_path=f"{tmpdir}/scores.json")
            hub = MultiplayerHub(high_score_manager=mgr)

            broadcasted_messages = []
            hub.broadcast_sync = lambda data: broadcasted_messages.append(data)  # type: ignore

            # SFX broadcast
            hub.play_sfx("pip")
            self.assertEqual(len(broadcasted_messages), 1)
            self.assertEqual(broadcasted_messages[0], {"type": "sfx", "name": "pip"})

            # High score update via hub
            is_new, current_high = hub.record_game_score("snake", 50)
            self.assertTrue(is_new)
            self.assertEqual(current_high, 50)
            self.assertEqual(hub.get_high_score("snake"), 50)

            state = hub.get_lobby_state()
            self.assertIn("high_scores", state)
            self.assertEqual(state["high_scores"]["snake"], 50)

    def test_games_sfx_triggers_and_score_reporting(self) -> None:
        from modes.multiplayer.breakout_game import InteractiveBreakout
        from modes.multiplayer.snake_game import InteractiveSnake
        from modes.multiplayer.tetris_game import InteractiveTetris

        # Test Snake
        snake = InteractiveSnake()
        sfx_events = []
        snake.sfx_callback = lambda name: sfx_events.append(name)
        self.assertEqual(snake.get_final_score(), 0)

        # Force snake death to test crash sound
        snake.snake.append((0, 0))
        snake.current_dir = "left"
        snake.next_dir = "left"
        snake._step_snake()
        self.assertTrue(snake.is_game_over())
        self.assertIn("crash", sfx_events)

        # Test Breakout
        breakout = InteractiveBreakout()
        breakout_sfx = []
        breakout.sfx_callback = lambda name: breakout_sfx.append(name)
        self.assertEqual(breakout.get_final_score(), 0)

        # Force ball lost
        breakout.ball_y = 64.0
        breakout.lives = 1
        breakout.update(0.01, set(), set())
        self.assertTrue(breakout.is_game_over())
        self.assertIn("game_over", breakout_sfx)

        # Test Tetris
        tetris = InteractiveTetris()
        tetris_sfx = []
        tetris.sfx_callback = lambda name: tetris_sfx.append(name)
        self.assertEqual(tetris.get_final_score(), 0)

        # Soft drop event
        tetris.update(0.01, set(), set(), p1_events=["down"])
        self.assertIn("drop", tetris_sfx)

    def test_game_registry_dynamic_discovery_and_creation(self) -> None:
        from modes.multiplayer import GAME_REGISTRY

        games = GAME_REGISTRY.list_games()
        self.assertGreaterEqual(len(games), 5)
        game_ids = [g.id for g in games]
        self.assertEqual(game_ids[:5], ["pong", "tron", "snake", "breakout", "tetris"])

        # Config dictionary matches expectations
        config = GAME_REGISTRY.get_games_config()
        self.assertEqual(len(config), len(games))
        self.assertEqual(config[0]["id"], "pong")
        self.assertEqual(config[0]["supports_2p"], True)

        # Carousel items contain titles, colors, and callable icon functions
        items = GAME_REGISTRY.get_carousel_items()
        self.assertEqual(len(items), len(games))
        for item in items:
            self.assertIn("short_title", item)
            self.assertIn("color", item)
            self.assertTrue(callable(item["icon_fn"]))

        # Factory create_game instantiates valid games
        pong = GAME_REGISTRY.create_game("pong", solo=True)
        self.assertTrue(pong.solo_vs_ai)
        snake = GAME_REGISTRY.create_game("snake")
        self.assertIsNotNone(snake)
        breakout = GAME_REGISTRY.create_game("breakout")
        self.assertIsNotNone(breakout)

    def test_scores_http_endpoint(self) -> None:
        import json
        import tempfile

        from modes.multiplayer.high_scores import HighScoreManager
        from web_controller.server import MultiplayerHub, WebControllerServer

        with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
            tmp.write(b"{}")
            tmp.flush()
            hub = MultiplayerHub()
            hub.high_score_manager = HighScoreManager(
                primary_path=tmp.name, fallback_path=tmp.name
            )
            hub.high_score_manager.update_score("pong", 7)
            hub.high_score_manager.update_score("snake", 42)

            server = WebControllerServer(hub=hub)

            class DummyRequest:
                def __init__(self, path: str):
                    self.path = path

            res = server.process_request(object(), DummyRequest("/scores"))  # type: ignore
            self.assertIsNotNone(res)
            self.assertEqual(res.status_code, 200)
            data = json.loads(res.body.decode("utf-8"))
            self.assertIn("scores", data)
            self.assertEqual(data["scores"].get("pong"), 7)
            self.assertEqual(data["scores"].get("snake"), 42)

    def test_headless_game_fuzz_and_invariants(self) -> None:
        import random

        from PIL import Image, ImageDraw

        from modes.multiplayer import GAME_REGISTRY

        actions_pool = ["up", "down", "left", "right", "action_a", "action_b"]
        dt_scenarios = [0.001, 0.016, 0.033, 0.05, 0.15, 0.0]

        img = Image.new("RGB", (64, 64), (0, 0, 0))
        draw = ImageDraw.Draw(img)

        for spec in GAME_REGISTRY.list_games():
            game = GAME_REGISTRY.create_game(spec.id, solo=True)
            sfx_log = []
            game.sfx_callback = lambda s: sfx_log.append(s)

            for _ in range(80):
                # Randomize inputs and dt stutter
                dt = random.choice(dt_scenarios)
                p1_inputs = set(random.sample(actions_pool, k=random.randint(0, 2)))
                p2_inputs = set(random.sample(actions_pool, k=random.randint(0, 2)))
                p1_events = random.sample(actions_pool, k=random.randint(0, 1))

                # Step simulation without throwing exceptions
                game.update(dt, p1_inputs, p2_inputs, p1_events=p1_events)

                # Render active state without crash
                game.render(draw)

                # Game-specific invariants
                if spec.id == "pong":
                    # Paddle must stay clamped inside the 64px vertical court
                    self.assertGreaterEqual(game.p1_y, 0)
                    self.assertLessEqual(game.p1_y, 64 - game.paddle_h)
                    self.assertGreaterEqual(game.p2_y, 0)
                    self.assertLessEqual(game.p2_y, 64 - game.paddle_h)
                elif spec.id == "snake":
                    # All segments must remain strictly within the 30x30 grid
                    for sx, sy in game.snake:
                        self.assertGreaterEqual(sx, 0)
                        self.assertLess(sx, game.grid_size)
                        self.assertGreaterEqual(sy, 0)
                        self.assertLess(sy, game.grid_size)
                elif spec.id == "breakout":
                    # Paddle center must stay within half_w + 1 and 63 - half_w
                    half_w = game.paddle_w / 2.0
                    self.assertGreaterEqual(game.paddle_x, half_w)
                    self.assertLessEqual(game.paddle_x, 63 - half_w + 0.1)
                elif spec.id == "tetris":
                    # Board grid dimensions invariant 10x17
                    self.assertEqual(len(game.board), 17)
                    self.assertEqual(len(game.board[0]), 10)
                elif spec.id == "tron":
                    # Light cycle positions stay clamped
                    self.assertGreaterEqual(game.p1_pos[0], 0)
                    self.assertLess(game.p1_pos[0], 64)
                    self.assertGreaterEqual(game.p1_pos[1], 0)
                    self.assertLess(game.p1_pos[1], 64)
                    self.assertGreaterEqual(game.p2_pos[0], 0)
                    self.assertLess(game.p2_pos[0], 64)
                    self.assertGreaterEqual(game.p2_pos[1], 0)
                    self.assertLess(game.p2_pos[1], 64)
                elif spec.id == "air_hockey":
                    # Mallets clamped inside their respective rink halves
                    self.assertGreaterEqual(
                        game.p1_x, game.RINK_L + game.MALLET_R - 0.1
                    )
                    self.assertLessEqual(game.p1_x, 29.0 - game.MALLET_R + 0.1)
                    self.assertGreaterEqual(game.p2_x, 34.0 + game.MALLET_R - 0.1)
                    self.assertLessEqual(game.p2_x, game.RINK_R - game.MALLET_R + 0.1)
                elif spec.id == "asteroids":
                    # Coordinates wrap within 64x64 canvas
                    self.assertGreaterEqual(game.ship_x, 0.0)
                    self.assertLess(game.ship_x, 64.0)
                    self.assertGreaterEqual(game.ship_y, 0.0)
                    self.assertLess(game.ship_y, 64.0)
                elif spec.id == "flappy":
                    # Bird stays clamped in flight zone
                    self.assertEqual(game.bird_x, 16.0)
                    self.assertGreaterEqual(game.bird_y, 0.0)
                    self.assertLessEqual(game.bird_y, game.GROUND_Y)
                elif spec.id == "2048":
                    # Grid remains 4x4
                    self.assertEqual(len(game.grid), 4)
                    self.assertTrue(all(len(row) == 4 for row in game.grid))

    def test_classic_arcade_new_games_mechanics(self) -> None:
        from modes.multiplayer import (
            AirHockeyGame,
            AsteroidsGame,
            FlappyBirdGame,
            Game2048,
        )

        # 1. Air Hockey puck bounce & goal mechanics
        hockey = AirHockeyGame(solo_vs_ai=True)
        self.assertEqual(hockey.p1_score, 0)
        self.assertEqual(hockey.p2_score, 0)
        # Position puck right in front of right goal moving right
        hockey.puck_x = 59.0
        hockey.puck_y = 31.0
        hockey.puck_vx = 30.0
        hockey.puck_vy = 0.0
        hockey.update(0.02, set(), set())
        # Goal scored by P1!
        self.assertEqual(hockey.p1_score, 1)

        # 2. Asteroids bullet fire and splitting
        asteroids = AsteroidsGame()
        asteroids.cooldown = 0.0
        # Fire bullet with action_a
        asteroids.update(0.016, {"action_a"}, set())
        self.assertGreater(len(asteroids.bullets), 0)

        # 3. Flappy Bird flap and jump impulse
        flappy = FlappyBirdGame()
        flappy.update(0.016, {"action_a"}, set())
        self.assertAlmostEqual(
            flappy.bird_vy, flappy.FLAP_IMPULSE + flappy.GRAVITY * 0.016, places=2
        )

        # 4. 2048 slide left and merge
        g2048 = Game2048()
        g2048.grid = [
            [2, 2, 0, 0],
            [4, 0, 4, 0],
            [0, 0, 0, 0],
            [8, 8, 8, 8],
        ]
        moved, earned, _ = g2048._apply_move("left")
        self.assertTrue(moved)
        self.assertEqual(g2048.grid[0], [4, 0, 0, 0])
        self.assertEqual(g2048.grid[1], [8, 0, 0, 0])
        self.assertEqual(g2048.grid[3], [16, 16, 0, 0])
        self.assertEqual(earned, 4 + 8 + 32)


if __name__ == "__main__":
    unittest.main()
