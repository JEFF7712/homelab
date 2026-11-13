"""Virtual lights whose colour wheels set the music colour.

Music Mode Colour: off means Auto (cover, vibe or pinned colours); on applies
the chosen colour to the strip and every taking-part area until it is
switched off.

Music Mode Pin: on means the playing album has a pinned colour, shown each
time it plays; its colour wheel edits the pin and switching it off removes
the pin, so the album goes back to automatic colours. Unavailable while
nothing plays.

Both lights' brightness is the music lights' brightness, shared with
number.music_mode_brightness.
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
from homeassistant.util.color import color_hs_to_RGB, color_RGB_to_hs

from .director import CONTROLS_UPDATED, DOMAIN, Director
from .palette import hex_rgb, named_palette, solid_colour

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
    director = hass.data[DOMAIN]
    async_add_entities([MusicColourLight(director), MusicPinLight(director)])


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


def _colour_rgb(colour: str) -> tuple[int, int, int] | None:
    """RGB for a pinned colour: #rrggbb as is, a colour name as shown."""
    rgb = hex_rgb(colour)
    if rgb is not None:
        return rgb
    palette = named_palette(colour, "override")
    return solid_colour(palette) if palette else None


class MusicPinLight(LightEntity):
    _attr_name = "Music Mode Pin"
    _attr_unique_id = "music_director_pin"
    _attr_icon = "mdi:pin"
    _attr_should_poll = False
    _attr_color_mode = ColorMode.HS

    def __init__(self, director: Director) -> None:
        self._attr_supported_color_modes = {ColorMode.HS}
        self._director = director

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, CONTROLS_UPDATED, self.async_write_ha_state
            )
        )

    @property
    def available(self) -> bool:
        return self._director.current_track is not None

    @property
    def is_on(self) -> bool:
        return self._director.current_pin() is not None

    def _rgb(self) -> tuple[int, int, int] | None:
        pin = self._director.current_pin()
        if pin is not None:
            return _colour_rgb(pin)
        return self._director.showing_rgb()

    @property
    def hs_color(self) -> tuple[float, float] | None:
        rgb = self._rgb()
        return color_RGB_to_hs(*rgb) if rgb else None

    @property
    def brightness(self) -> int:
        return round(self._director.brightness_pct * 255 / 100)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        track = self._director.current_track
        return {
            "album": track.album if track else None,
            "pinned_colour": self._director.current_pin(),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            self._director.set_brightness(round(kwargs[ATTR_BRIGHTNESS] * 100 / 255))
        if ATTR_HS_COLOR in kwargs:
            hue, saturation = kwargs[ATTR_HS_COLOR]
            rgb: tuple[int, int, int] | None = color_hs_to_RGB(
                float(hue), float(saturation)
            )
        elif self._director.current_pin() is not None:
            # Already pinned: a brightness change only.
            return
        else:
            # Pin what is showing: the held manual colour, else the auto one.
            rgb = self._director.manual_rgb or self._director.showing_rgb()
        if rgb is None:
            return
        self._director.set_pin(rgb)
        # The pin now shows for this album; other songs go back to Auto.
        if self._director.manual_rgb is not None:
            self._director.set_colour(None)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._director.set_pin(None)
