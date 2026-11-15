"""Recover HA imports whose availability got stuck during a reconnect.

The pinned upstream provider relies on websocket updates after its startup
snapshot. Reconcile unavailable imports only; healthy players keep their normal
push-driven playback tracking. Remove this hook when upstream provides the same
reconciliation guarantee.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

INTERVAL_SECONDS = 30
FETCH_TIMEOUT_SECONDS = 15
UNAVAILABLE_STATES = {"unknown", "unavailable"}


async def reconcile_unavailable(provider: Any) -> None:
    """Repair stale availability only after a successful authoritative read."""
    if not provider.available or not provider.hass_prov.hass.connected:
        return
    candidates = {}
    for entity_id in provider.config.get_value("players") or []:
        player = provider.mass.players.get_player(entity_id)
        if (
            player is not None
            and player.provider is provider
            and player.state.enabled
            and not player.state.available
        ):
            candidates[entity_id] = (player, player.state)
    if not candidates:
        return
    async with asyncio.timeout(FETCH_TIMEOUT_SECONDS):
        states = await provider.hass_prov.get_states(entity_ids=list(candidates))
    if not provider.available or not provider.hass_prov.hass.connected:
        return
    for state in states:
        entity_id = state["entity_id"]
        candidate = candidates.get(entity_id)
        if candidate is None:
            continue
        player, observed_state = candidate
        if (
            state["state"] in UNAVAILABLE_STATES
            or not state["state"]
            or provider.mass.players.get_player(entity_id) is not player
            or player.state is not observed_state
            or player.state.available
        ):
            continue
        player.update_from_compressed_state(
            {"s": state["state"], "a": state["attributes"]}
        )
        provider.logger.warning("Recovered stale Home Assistant player %s", entity_id)


async def recovery_loop(provider: Any) -> None:
    """Retry bounded reads without allowing a failed read to kill recovery."""
    while True:
        await asyncio.sleep(INTERVAL_SECONDS)
        try:
            await reconcile_unavailable(provider)
        except Exception as exc:
            # Tokens and URLs can be embedded in client exception messages.
            provider.logger.warning(
                "Home Assistant player recovery read failed (%s); retrying",
                type(exc).__name__,
            )


def install_recovery(provider_type: Any) -> None:
    """Tie one reconciliation task to each upstream provider's lifecycle."""
    if getattr(provider_type, "_homelab_recovery_installed", False):
        return
    original_loaded = provider_type.loaded_in_mass
    original_unload = provider_type.unload

    async def loaded_in_mass(self: Any) -> None:
        await original_loaded(self)
        self._homelab_recovery_task = asyncio.create_task(
            recovery_loop(self), name="homelab-hass-player-recovery"
        )
        self.logger.info("Home Assistant player recovery enabled (30s interval)")

    async def unload(self: Any, is_removed: bool = False) -> None:
        if task := getattr(self, "_homelab_recovery_task", None):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            self._homelab_recovery_task = None
        await original_unload(self, is_removed)

    provider_type.loaded_in_mass = loaded_in_mass
    provider_type.unload = unload
    provider_type._homelab_recovery_installed = True


if __name__ == "__main__":
    from music_assistant.__main__ import main
    from music_assistant.providers.hass_players.provider import (
        HomeAssistantPlayerProvider,
    )

    install_recovery(HomeAssistantPlayerProvider)
    raise SystemExit(main())
