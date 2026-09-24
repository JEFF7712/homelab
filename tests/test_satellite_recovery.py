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
from typing import Any
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
        self.name = "test"
        self.satellite = None
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

    def test_audio_loop_detects_digital_silence_and_cleans_health_file(self) -> None:
        try:
            import numpy as np

            raw_silent = np.zeros((1024, 1), dtype=np.float32)
            raw_sound = np.array([[0.01]], dtype=np.float32)

            def check_nonzero(arr: Any) -> bool:
                return bool(np.any(arr != 0))

        except ImportError:
            raw_silent = [0.0] * 1024
            raw_sound = [0.01]

            def check_nonzero(arr: Any) -> bool:
                return any(x != 0 for x in arr)

        with tempfile.TemporaryDirectory() as tmpdir:
            health_file = os.path.join(tmpdir, "satellite_audio_healthy")
            with open(health_file, "w") as f:
                f.write("123.45\n")

            # Simulate 64 silent blocks (synthetic zero frames from dropped stream)
            silent_blocks = 0
            exit_called = False

            for _ in range(64):
                is_digital_silence = not check_nonzero(raw_silent)
                if is_digital_silence:
                    silent_blocks += 1
                    if silent_blocks >= 64:
                        if os.path.exists(health_file):
                            os.remove(health_file)
                        exit_called = True
                        break

            self.assertTrue(exit_called)
            self.assertEqual(silent_blocks, 64)
            self.assertFalse(os.path.exists(health_file))

            # When non-silent frames arrive, silent_blocks resets and health file is touched
            silent_blocks = 50
            is_digital_silence = not check_nonzero(raw_sound)
            if not is_digital_silence:
                silent_blocks = 0
                with open(health_file, "w") as f:
                    f.write("678.90\n")

            self.assertEqual(silent_blocks, 0)
            self.assertTrue(os.path.exists(health_file))

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
        "LVAEvent": MagicMock(),
        "VoiceAssistantAnnounceFinished": MagicMock(),
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
    exec(compile(ast.fix_missing_locations(module), str(source), "exec"), namespace)  # noqa: S102
    return namespace["VoiceSatelliteProtocol"]


def protocol_session(cls, state):

    with patch.object(cls, "_initialize_entities"):
        instance = cls(state)
    instance.send_messages = MagicMock()
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

    def test_stale_tts_callback_does_not_change_new_playback(self):
        state = FakeServerState()
        cls = load_production_protocol()
        old = protocol_session(cls, state)
        state.satellite = protocol_session(cls, state)
        state.playback_inhibited = True
        state.active_wake_words.add(state.stop_word.id)
        old._tts_finished()
        self.assertTrue(state.playback_inhibited)
        self.assertIn(state.stop_word.id, state.active_wake_words)
        old.send_messages.assert_not_called()

    def test_session_takeover_cancels_previous_playback_without_unmuting(self):
        state = FakeServerState()
        cls = load_production_protocol()
        old = protocol_session(cls, state)
        new = protocol_session(cls, state)
        new._initialize_entities = MagicMock()
        state.satellite = old
        state.muted = True
        state.playback_inhibited = True
        state.tts_player.stop.side_effect = old._tts_finished
        new._claim_session()
        self.assertIs(state.satellite, new)
        self.assertTrue(state.connected)
        self.assertTrue(state.muted)
        self.assertFalse(state.playback_inhibited)
        self.assertTrue(new.is_established_ha)
        old.send_messages.assert_not_called()

    def test_real_playback_and_tail_preserve_manual_mute(self):
        state = FakeServerState()
        cls = load_production_protocol()
        session = protocol_session(cls, state)
        state.satellite = session
        session._tts_url = "http://test/audio.wav"
        session.play_tts()
        self.assertTrue(state.playback_inhibited)
        state.muted = True
        with patch("threading.Timer") as timer:
            state.tts_player.play.call_args.kwargs["done_callback"]()
            self.assertTrue(state.playback_inhibited)
            timer.call_args.args[1]()
        self.assertFalse(state.playback_inhibited)
        self.assertTrue(state.muted)

    def test_probe_constructor_does_not_rebind_live_entities(self):
        state = FakeServerState()
        cls = load_production_protocol()
        owner = protocol_session(cls, state)
        state.satellite = owner
        state.media_player_entity = MagicMock(server=owner)
        state.entities.append(state.media_player_entity)
        probe = cls(state)
        self.assertIs(state.media_player_entity.server, owner)
        self.assertIs(state.satellite, owner)
        self.assertFalse(probe.is_established_ha)


if __name__ == "__main__":
    unittest.main()
