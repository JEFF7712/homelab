import json
import threading
import time
import unittest
from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import Mock, patch

from PIL import Image
from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve

from ha_source import ArtworkCache, HomeAssistantSource

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


if __name__ == "__main__":
    unittest.main()
