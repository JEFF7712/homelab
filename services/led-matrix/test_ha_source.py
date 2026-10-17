import json
import threading
import time
import unittest
from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import Mock, patch

from ha_source import (
    ArtworkCache,
    HomeAssistantSource,
    HybridPlaybackSource,
    PlaybackState,
    parse_spotify_state,
)
from PIL import Image, ImageDraw
from spotify_matrix_streamer import SpotifyQuotaExceeded, SpotifyRateLimited
from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve

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
            self.assertFalse(request.call_args.kwargs["allow_redirects"])

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

        arcade = RetroArcadeRenderer(cycle_interval=2.0)
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
            DungeonGame,
            PongGame,
            SnakeGame,
            TetrisGame,
        )

        for game in [
            PongGame(),
            SnakeGame(),
            BreakoutGame(),
            TetrisGame(),
            DungeonGame(),
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
        self.assertIn("DUNGEON", names)

    def test_tetris_ai_stacks_pieces(self) -> None:
        from modes.arcade import TetrisGame

        game = TetrisGame()
        filled = 0
        for _ in range(300):
            game.update(0.05)
            filled = max(filled, sum(1 for row in game.board for cell in row if cell))
        self.assertGreater(filled, 0)

    def test_dungeon_bot_navigates(self) -> None:
        from modes.arcade import DungeonGame

        game = DungeonGame()
        start = (game.px, game.py)
        for _ in range(200):
            game.update(0.05)
        self.assertNotEqual((game.px, game.py), start)
        self.assertEqual(len(game.grid), 21)
        self.assertTrue(all(game.grid[y][x] == 1 for x in range(21) for y in (0, 20)))


if __name__ == "__main__":
    unittest.main()
