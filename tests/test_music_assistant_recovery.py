from __future__ import annotations

import asyncio
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "music_assistant_recovery", ROOT / "gitops/music-assistant/runtime/recovery.py"
)
assert SPEC and SPEC.loader
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)
ENTITY = "media_player.satellite"


class RecoveryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.provider = SimpleNamespace(
            available=True,
            config=SimpleNamespace(get_value=lambda key: [ENTITY]),
            hass_prov=SimpleNamespace(
                hass=SimpleNamespace(connected=True),
                get_states=AsyncMock(
                    return_value=[
                        {
                            "entity_id": ENTITY,
                            "state": "idle",
                            "attributes": {"volume_level": 0.16},
                        }
                    ]
                ),
            ),
            logger=Mock(),
        )
        self.player = SimpleNamespace(
            provider=self.provider,
            state=SimpleNamespace(enabled=True, available=False),
            update_from_compressed_state=Mock(),
        )
        self.provider.mass = SimpleNamespace(
            players=SimpleNamespace(get_player=lambda entity_id: self.player)
        )

    async def test_repairs_missed_reconnect_update_with_authoritative_state(
        self,
    ) -> None:
        await recovery.reconcile_unavailable(self.provider)
        self.provider.hass_prov.get_states.assert_awaited_once_with(entity_ids=[ENTITY])
        self.player.update_from_compressed_state.assert_called_once_with(
            {"s": "idle", "a": {"volume_level": 0.16}}
        )

    async def test_real_outages_and_missing_sources_remain_unavailable(self) -> None:
        for state in ("unavailable", "unknown", ""):
            self.provider.hass_prov.get_states.return_value[0]["state"] = state
            await recovery.reconcile_unavailable(self.provider)
        self.provider.hass_prov.get_states.return_value = []
        await recovery.reconcile_unavailable(self.provider)
        self.player.update_from_compressed_state.assert_not_called()

    async def test_healthy_disabled_and_foreign_players_are_not_polled(self) -> None:
        for changes in (
            {"state": SimpleNamespace(enabled=True, available=True)},
            {"state": SimpleNamespace(enabled=False, available=False)},
            {"provider": object()},
        ):
            original = self.player.__dict__.copy()
            self.player.__dict__.update(changes)
            await recovery.reconcile_unavailable(self.provider)
            self.player.__dict__.update(original)
        self.provider.hass_prov.get_states.assert_not_awaited()

    async def test_disconnected_provider_is_not_polled(self) -> None:
        self.provider.hass_prov.hass.connected = False
        await recovery.reconcile_unavailable(self.provider)
        self.provider.hass_prov.get_states.assert_not_awaited()

    async def test_live_update_during_fetch_wins_over_older_snapshot(self) -> None:
        async def fetch(**kwargs):
            self.player.state = SimpleNamespace(enabled=True, available=False)
            return [{"entity_id": ENTITY, "state": "idle", "attributes": {}}]

        self.provider.hass_prov.get_states.side_effect = fetch
        await recovery.reconcile_unavailable(self.provider)
        self.player.update_from_compressed_state.assert_not_called()

    async def test_stalled_fetch_times_out_without_changing_availability(self) -> None:
        async def fetch(**kwargs):
            await asyncio.Future()

        self.provider.hass_prov.get_states.side_effect = fetch
        with patch.object(recovery, "FETCH_TIMEOUT_SECONDS", 0.001):
            with self.assertRaises(TimeoutError):
                await recovery.reconcile_unavailable(self.provider)
        self.player.update_from_compressed_state.assert_not_called()

    async def test_disconnect_during_fetch_discards_snapshot(self) -> None:
        async def fetch(**kwargs):
            self.provider.hass_prov.hass.connected = False
            return [{"entity_id": ENTITY, "state": "idle", "attributes": {}}]

        self.provider.hass_prov.get_states.side_effect = fetch
        await recovery.reconcile_unavailable(self.provider)
        self.player.update_from_compressed_state.assert_not_called()

    async def test_failed_read_retries_and_does_not_expose_credentials(self) -> None:
        self.provider.hass_prov.get_states.side_effect = [
            RuntimeError("sensitive-token"),
            [{"entity_id": ENTITY, "state": "idle", "attributes": {}}],
        ]
        with patch.object(recovery, "INTERVAL_SECONDS", 0):
            task = asyncio.create_task(recovery.recovery_loop(self.provider))
            for _ in range(30):
                if self.player.update_from_compressed_state.called:
                    break
                await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.player.update_from_compressed_state.assert_called()
        self.assertNotIn("sensitive-token", str(self.provider.logger.mock_calls))

    async def test_provider_unload_cancels_task_and_reload_has_one_new_task(
        self,
    ) -> None:
        class Provider:
            loaded_in_mass = AsyncMock()
            unload = AsyncMock()
            logger = Mock()

        original_unload = Provider.unload
        recovery.install_recovery(Provider)
        recovery.install_recovery(Provider)
        provider = Provider()
        await provider.loaded_in_mass()
        first = provider._homelab_recovery_task
        await provider.unload(is_removed=True)
        self.assertTrue(first.cancelled())
        original_unload.assert_awaited_once_with(provider, True)
        await provider.loaded_in_mass()
        self.assertIsNot(first, provider._homelab_recovery_task)
        await provider.unload()


class SkipDebounceTest(unittest.TestCase):
    def test_caps_only_queue_skip_delay_and_installs_once(self) -> None:
        calls: list[tuple[float, str | None, tuple, dict]] = []

        class Mass:
            def call_later(self, delay, target, *args, task_id=None, **kwargs):
                calls.append((delay, task_id, args, kwargs))
                return "handle"

        recovery.install_skip_debounce(Mass)
        recovery.install_skip_debounce(Mass)
        mass = Mass()
        target = object()
        self.assertEqual(
            mass.call_later(1, target, "queue", 3, task_id="queue_play_index_queue"),
            "handle",
        )
        mass.call_later(5, target, "queue", task_id="fill_autoplay_tracks_queue")
        mass.call_later(0.05, target, task_id="queue_play_index_queue")
        mass.call_later(1, target, extra=True)
        self.assertEqual(
            calls,
            [
                (
                    recovery.SKIP_DEBOUNCE_SECONDS,
                    "queue_play_index_queue",
                    ("queue", 3),
                    {},
                ),
                (5, "fill_autoplay_tracks_queue", ("queue",), {}),
                (0.05, "queue_play_index_queue", (), {}),
                (1, None, (), {"extra": True}),
            ],
        )
        self.assertLess(recovery.SKIP_DEBOUNCE_SECONDS, 0.2)


if __name__ == "__main__":
    unittest.main()
