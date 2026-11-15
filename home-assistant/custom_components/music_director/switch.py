"""Music Mode Drop Flash: flash the strip when a song drops."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .director import CONTROLS_UPDATED, DOMAIN, Director


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    if discovery_info is None:
        return
    async_add_entities([DropFlash(hass.data[DOMAIN])])


class DropFlash(SwitchEntity, RestoreEntity):
    _attr_name = "Music Mode Drop Flash"
    _attr_unique_id = "music_director_drop_flash"
    _attr_icon = "mdi:flash"
    _attr_should_poll = False

    def __init__(self, director: Director) -> None:
        self._director = director

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        # On by default; a switched-off flash stays off across restarts.
        if last is not None and last.state == STATE_OFF:
            self._director.set_drop_flash(False)
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, CONTROLS_UPDATED, self.async_write_ha_state
            )
        )

    @property
    def is_on(self) -> bool:
        return self._director.drop_flash

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._director.set_drop_flash(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._director.set_drop_flash(False)
