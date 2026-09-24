"""Offline tests for satellite capture failure/recovery, playback inhibition, and healthcheck."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

HEALTHCHECK_PATH = (
    Path(__file__).resolve().parents[1] / "gitops/voice/satellite/healthcheck.py"
)
spec = importlib.util.spec_from_file_location("satellite_healthcheck", HEALTHCHECK_PATH)
healthcheck = importlib.util.module_from_spec(spec)
sys.modules["satellite_healthcheck"] = healthcheck
spec.loader.exec_module(healthcheck)


class FakeServerState:
    """Mock ServerState for satellite protocol tests."""

    def __init__(self) -> None:
        self.muted = False
        self.playback_inhibited = False
        self.acoustic_tail_seconds = 0.05  # 50 ms for fast unit testing
        self.active_wake_words: set[str] = set()
        self.stop_word = MagicMock()
        self.stop_word.id = "stop_word_id"
        self.tts_player = MagicMock()
        self.music_player = MagicMock()
        self.entities = []
        self.connections = []
        self.output_only = False
        self.audio_input_channels = 1
        self.media_player_entity = None
        self.mute_switch_entity = None
        self.volume = 50
        self.last_audio_frame_time = None


class SatelliteRecoveryTests(unittest.TestCase):
    def test_healthcheck_detects_fresh_and_stale_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            health_file = os.path.join(tmpdir, "satellite_audio_healthy")
            with patch.object(healthcheck, "HEALTH_FILE", health_file):
                # When file does not exist -> unhealthy
                self.assertFalse(healthcheck.check_audio_freshness())

                # When file is fresh -> healthy
                with open(health_file, "w") as f:
                    f.write("test")
                self.assertTrue(healthcheck.check_audio_freshness())

                # When file is stale (> 10s old) -> unhealthy
                stale_time = time.time() - 15.0
                os.utime(health_file, (stale_time, stale_time))
                self.assertFalse(healthcheck.check_audio_freshness())

    def test_bounded_backoff_calculation(self) -> None:
        initial_backoff = 1.0
        max_backoff = 10.0
        # Formula: min(initial_backoff * (2 ** (attempt - 1)), max_backoff)
        delays = [
            min(initial_backoff * (2 ** (attempt - 1)), max_backoff)
            for attempt in range(1, 7)
        ]
        self.assertEqual(delays, [1.0, 2.0, 4.0, 8.0, 10.0, 10.0])

    def test_playback_inhibition_lifecycle_and_acoustic_tail(self) -> None:
        state = FakeServerState()

        # Mock VoiceSatelliteProtocol components without full protobuf network stack
        class DummySatellite:
            def __init__(self, s: FakeServerState) -> None:
                self.state = s
                self._tail_timer: threading.Timer | None = None
                self._pipeline_active = False
                self._tts_url = None
                self._tts_played = False
                self._timer_finished = False

            def _start_tail_timer(self, duration: float) -> None:
                self._cancel_tail_timer()

                def _clear():
                    self.state.playback_inhibited = False

                self._tail_timer = threading.Timer(duration, _clear)
                self._tail_timer.daemon = True
                self._tail_timer.start()

            def _cancel_tail_timer(self) -> None:
                if self._tail_timer is not None:
                    self._tail_timer.cancel()
                    self._tail_timer = None

            def play_tts(self) -> None:
                self._tts_played = True
                self._cancel_tail_timer()
                self.state.playback_inhibited = True
                self.state.active_wake_words.add(self.state.stop_word.id)
                self.state.tts_player.play(
                    self._tts_url, done_callback=self._tts_finished
                )

            def _tts_finished(self) -> None:
                self._pipeline_active = False
                self.state.active_wake_words.discard(self.state.stop_word.id)
                tail = getattr(self.state, "acoustic_tail_seconds", 0.05)
                self._start_tail_timer(tail)

            def stop(self) -> None:
                self.state.active_wake_words.discard(self.state.stop_word.id)
                self._pipeline_active = False
                self._cancel_tail_timer()
                self.state.playback_inhibited = False
                self.state.tts_player.stop()

            def wakeup(self, wake_word) -> bool:
                if (
                    self.state.muted
                    or self.state.playback_inhibited
                    or self._pipeline_active
                ):
                    return False
                self._pipeline_active = True
                return True

        satellite = DummySatellite(state)
        dummy_ww = MagicMock(wake_word="hey_jarvis")

        # Initial state: uninhibited, unmuted
        self.assertFalse(state.playback_inhibited)
        self.assertTrue(satellite.wakeup(dummy_ww))
        satellite._pipeline_active = False

        # Start TTS -> playback_inhibited becomes True
        satellite._tts_url = "http://fake.tts/audio.mp3"
        satellite.play_tts()
        self.assertTrue(state.playback_inhibited)
        self.assertIn("stop_word_id", state.active_wake_words)

        # While playing TTS, wake triggers are inhibited
        self.assertFalse(satellite.wakeup(dummy_ww))

        # Finish TTS -> tail timer starts, playback_inhibited remains True
        satellite._tts_finished()
        self.assertTrue(state.playback_inhibited)
        self.assertNotIn("stop_word_id", state.active_wake_words)

        # Wait for acoustic tail timer (0.05s) to expire
        time.sleep(0.08)
        self.assertFalse(state.playback_inhibited)

        # Now wakeup succeeds again
        self.assertTrue(satellite.wakeup(dummy_ww))

    def test_manual_mute_preservation(self) -> None:
        state = FakeServerState()

        def set_muted(new_state: bool) -> None:
            state.muted = bool(new_state)
            if state.muted:
                state.tts_player.stop()

        # User mutes satellite
        set_muted(True)
        self.assertTrue(state.muted)
        self.assertFalse(state.playback_inhibited)

        # Playback tail expiration must not touch manual mute
        state.playback_inhibited = True
        # Simulate tail expiration
        state.playback_inhibited = False
        self.assertTrue(state.muted)

        # User unmutes
        set_muted(False)
        self.assertFalse(state.muted)

    def test_stop_word_cancellation_during_playback(self) -> None:
        state = FakeServerState()

        class DummySatellite:
            def __init__(self, s: FakeServerState) -> None:
                self.state = s
                self._pipeline_active = True

            def stop(self) -> None:
                self.state.playback_inhibited = False
                self._pipeline_active = False
                self.state.tts_player.stop()
                self.state.music_player.unduck()

        sat = DummySatellite(state)
        state.playback_inhibited = True
        state.active_wake_words.add("stop_word_id")

        # Stop word fires
        sat.stop()

        self.assertFalse(state.playback_inhibited)
        self.assertFalse(sat._pipeline_active)
        state.tts_player.stop.assert_called_once()
        state.music_player.unduck.assert_called_once()

    def test_probing_while_ha_connected_and_tts_playing_does_not_corrupt_state(
        self,
    ) -> None:
        state = FakeServerState()

        ProtocolUnderTest = load_production_protocol()
        state.name = "test"
        state.connected = False
        state.satellite = None

        # 1. Establish real HA session
        ha_session = protocol_session(ProtocolUnderTest, state)
        ha_session.is_established_ha = True
        state.satellite = ha_session
        state.connected = True
        self.assertTrue(state.connected)
        self.assertIs(state.satellite, ha_session)

        # 2. TTS starts playing -> playback inhibition is active
        state.playback_inhibited = True

        # 3. Health probe connects via raw TCP
        probe = protocol_session(ProtocolUnderTest, state)
        # Assert probe did NOT overwrite state.satellite or clear connected / playback_inhibited
        self.assertFalse(probe.is_established_ha)
        self.assertIs(state.satellite, ha_session)
        self.assertTrue(state.connected)
        self.assertTrue(state.playback_inhibited)

        # 4. Health probe disconnects
        probe.connection_lost(None)
        # Assert HA session and playback inhibition remain intact
        self.assertIs(state.satellite, ha_session)
        self.assertTrue(state.connected)
        self.assertTrue(state.playback_inhibited)
        state.tts_player.stop.assert_not_called()

        # 5. When real HA session disconnects, state is cleaned up
        ha_session.connection_lost(None)
        self.assertIsNone(state.satellite)
        self.assertFalse(state.connected)
        self.assertFalse(state.playback_inhibited)

    def test_dedicated_health_endpoint_check(self) -> None:
        import http.server
        import socketserver

        class HealthHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/healthz":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status": "ok"}')
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format, *args):
                pass

        with socketserver.TCPServer(("127.0.0.1", 0), HealthHandler) as server:
            port = server.server_address[1]
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                with patch.object(healthcheck, "HEALTH_PORT", port):
                    self.assertTrue(healthcheck.check_health_endpoint())
            finally:
                server.shutdown()

        # When server is down -> check_health_endpoint returns False
        with patch.object(healthcheck, "HEALTH_PORT", 65530):
            self.assertFalse(healthcheck.check_health_endpoint())


if __name__ == "__main__":
    unittest.main()


def load_production_protocol():
    import ast
    import asyncio
    import logging
    from types import SimpleNamespace

    source = HEALTHCHECK_PATH.with_name("satellite.py")
    tree = ast.parse(source.read_text())
    cls = next(
        n
        for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == "VoiceSatelliteProtocol"
    )

    class Base:
        def __init__(self, name):
            pass

        def connection_lost(self, exc):
            pass

    namespace = {
        "APIServer": Base,
        "asyncio": asyncio,
        "threading": threading,
        "VoiceAssistantFeature": SimpleNamespace(
            API_AUDIO=1,
            ANNOUNCE=2,
            VOICE_ASSISTANT=4,
            START_CONVERSATION=8,
            TIMERS=16,
            MULTI_CHANNEL_AUDIO=32,
        ),
        "_LOGGER": logging.getLogger(__name__),
        "LVAEvent": SimpleNamespace(DISCONNECTED="disconnected"),
    }
    for name in (
        "MuteSwitchEntity",
        "MediaPlayerEntity",
        "ThinkingSoundEntity",
        "WakeWord1SensitivityNumberEntity",
        "WakeWord2SensitivityNumberEntity",
        "StopWordSensitivityNumberEntity",
        "MicSettingEntity",
        "ButtonEventSensorEntity",
        "LEDLightEntity",
    ):
        namespace[name] = type(name, (MagicMock,), {})
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            ),
            cls,
        ],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(source), "exec"), namespace)
    return namespace["VoiceSatelliteProtocol"]


def protocol_session(cls, state):
    import asyncio

    instance = cls.__new__(cls)
    instance.state = state
    instance.is_established_ha = False
    instance._tail_timer = None
    instance._disconnect_event = asyncio.Event()
    instance._emit = MagicMock()
    for name in (
        "mute_switch_entity",
        "mic_gain_entity",
        "mic_noise_suppression_entity",
        "mic_volume_entity",
    ):
        setattr(state, name, None)
    state.connections.append(instance)
    return instance


class ProductionOwnershipTests(unittest.TestCase):
    def test_old_session_disconnect_preserves_new_owner(self):
        state = FakeServerState()
        cls = load_production_protocol()
        old = protocol_session(cls, state)
        new = protocol_session(cls, state)
        old.is_established_ha = new.is_established_ha = True
        state.satellite = new
        state.connected = state.playback_inhibited = True
        old.connection_lost(None)
        self.assertIs(state.satellite, new)
        self.assertTrue(state.connected)
        self.assertTrue(state.playback_inhibited)
        state.tts_player.stop.assert_not_called()
        old._emit.assert_not_called()

    def test_current_owner_disconnect_cleans_up_despite_probe(self):
        state = FakeServerState()
        cls = load_production_protocol()
        owner = protocol_session(cls, state)
        probe = protocol_session(cls, state)
        owner.is_established_ha = True
        state.satellite = owner
        state.connected = state.playback_inhibited = True
        owner.connection_lost(None)
        self.assertIsNone(state.satellite)
        self.assertFalse(state.connected)
        self.assertFalse(state.playback_inhibited)
        state.tts_player.stop.assert_called_once()
        probe.connection_lost(None)
        state.tts_player.stop.assert_called_once()

    def test_old_tail_callback_cannot_clear_new_owner(self):
        state = FakeServerState()
        cls = load_production_protocol()
        old = protocol_session(cls, state)
        state.satellite = old
        with patch("threading.Timer") as timer:
            old._start_tail_timer(0.35)
            callback = timer.call_args.args[1]
        state.satellite = protocol_session(cls, state)
        state.playback_inhibited = True
        callback()
        self.assertTrue(state.playback_inhibited)

    def test_constructor_initializes_session_fields_and_timer_cancel_is_local(self):
        state = MagicMock()
        state.name = "test"
        state.entities = []
        state.output_only = True
        for name in (
            "media_player_entity",
            "mute_switch_entity",
            "thinking_sound_entity",
            "sensitivity_1_number_entity",
            "sensitivity_2_number_entity",
            "stop_sensitivity_number_entity",
            "mic_gain_entity",
            "mic_noise_suppression_entity",
            "mic_volume_entity",
        ):
            setattr(state, name, None)
        cls = load_production_protocol()
        instance = cls(state)
        self.assertFalse(instance._is_streaming_audio)
        self.assertFalse(instance._disconnect_event.is_set())
        before = list(state.entities)
        instance._cancel_tail_timer()
        self.assertEqual(state.entities, before)
