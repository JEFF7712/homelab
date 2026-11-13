"""Music Mode Strip Colours: the LED strip's colours as a gradient or one solid colour."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .director import CONTROLS_UPDATED, DOMAIN, Director

GRADIENT = "Gradient"
SOLID = "Solid"


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    if discovery_info is None:
        return
    async_add_entities([StripColours(hass.data[DOMAIN])])


class StripColours(SelectEntity, RestoreEntity):
    _attr_name = "Music Mode Strip Colours"
    _attr_unique_id = "music_director_strip_colours"
    _attr_icon = "mdi:gradient-horizontal"
    _attr_should_poll = False

    def __init__(self, director: Director) -> None:
        self._attr_options = [GRADIENT, SOLID]
        self._director = director

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in self._attr_options:
            self._director.set_strip_solid(last.state == SOLID)
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, CONTROLS_UPDATED, self.async_write_ha_state
            )
        )

    @property
    def current_option(self) -> str:
        return SOLID if self._director.strip_solid else GRADIENT

    async def async_select_option(self, option: str) -> None:
        self._director.set_strip_solid(option == SOLID)
