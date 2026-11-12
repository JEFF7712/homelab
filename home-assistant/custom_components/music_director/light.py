"""Music Mode Colour: a virtual light whose colour wheel picks the music colour.

Off means Auto (cover and vibe colours); on applies the chosen colour to the
strip and every taking-part area until it is switched off. Its brightness is
the music lights' brightness, shared with number.music_mode_brightness.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util.color import color_hs_to_RGB

from .director import CONTROLS_UPDATED, DOMAIN, Director

# Red, the colour the light offers before anything has been picked.
DEFAULT_HS = (0.0, 100.0)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    if discovery_info is None:
        return
    async_add_entities([MusicColourLight(hass.data[DOMAIN])])


class MusicColourLight(LightEntity, RestoreEntity):
    _attr_name = "Music Mode Colour"
    _attr_unique_id = "music_director_colour"
    _attr_icon = "mdi:palette"
    _attr_should_poll = False
    _attr_color_mode = ColorMode.HS

    def __init__(self, director: Director) -> None:
        self._attr_supported_color_modes = {ColorMode.HS}
        self._director = director
        self._hs: tuple[float, float] = DEFAULT_HS

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            hs = last.attributes.get(ATTR_HS_COLOR)
            if isinstance(hs, (list, tuple)) and len(hs) == 2:
                self._hs = (float(hs[0]), float(hs[1]))
            if last.state == "on":
                self._director.set_colour(self._rgb())
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, CONTROLS_UPDATED, self.async_write_ha_state
            )
        )

    def _rgb(self) -> tuple[int, int, int]:
        r, g, b = color_hs_to_RGB(*self._hs)
        return (r, g, b)

    @property
    def is_on(self) -> bool:
        return self._director.manual_rgb is not None

    @property
    def hs_color(self) -> tuple[float, float]:
        return self._hs

    @property
    def brightness(self) -> int:
        return round(self._director.brightness_pct * 255 / 100)

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_HS_COLOR in kwargs:
            hue, saturation = kwargs[ATTR_HS_COLOR]
            self._hs = (float(hue), float(saturation))
        if ATTR_BRIGHTNESS in kwargs:
            self._director.set_brightness(round(kwargs[ATTR_BRIGHTNESS] * 100 / 255))
        self._director.set_colour(self._rgb())

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._director.set_colour(None)
