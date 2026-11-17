"""Music Mode Brightness: brightness of the strip and painted areas, in %."""

from __future__ import annotations

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .director import (
    CONTROLS_UPDATED,
    DOMAIN,
    MAX_BRIGHTNESS,
    MIN_BRIGHTNESS,
    Director,
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    if discovery_info is None:
        return
    async_add_entities([MusicBrightness(hass.data[DOMAIN])])


class MusicBrightness(RestoreNumber):
    _attr_name = "Music Mode Brightness"
    _attr_unique_id = "music_director_brightness"
    _attr_icon = "mdi:brightness-6"
    _attr_should_poll = False
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = MIN_BRIGHTNESS
    _attr_native_max_value = MAX_BRIGHTNESS
    _attr_native_step = 5
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, director: Director) -> None:
        self._director = director

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._director.set_brightness(round(last.native_value))
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, CONTROLS_UPDATED, self.async_write_ha_state
            )
        )

    @property
    def native_value(self) -> float:
        return self._director.brightness_pct

    async def async_set_native_value(self, value: float) -> None:
        self._director.set_brightness(round(value))
