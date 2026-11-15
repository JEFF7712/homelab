"""Music Mode selects: Strip Colours (a gradient or one solid colour) and
Effect Pin (an effect pinned to the playing album, or Auto)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .director import CONTROLS_UPDATED, DOMAIN, Director
from .rules import EFFECTS

GRADIENT = "Gradient"
SOLID = "Solid"
AUTO = "Auto"


def effect_label(effect: str) -> str:
    return effect.replace("_", " ").title()


EFFECT_BY_LABEL = {effect_label(effect): effect for effect in EFFECTS}


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    if discovery_info is None:
        return
    director = hass.data[DOMAIN]
    async_add_entities([StripColours(director), EffectPin(director)])


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


class EffectPin(SelectEntity):
    """The effect pinned to the playing track's album; Auto unpins it."""

    _attr_name = "Music Mode Effect Pin"
    _attr_unique_id = "music_director_effect_pin"
    _attr_icon = "mdi:pin"
    _attr_should_poll = False

    def __init__(self, director: Director) -> None:
        self._attr_options = [AUTO, *EFFECT_BY_LABEL]
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
    def current_option(self) -> str:
        effect = self._director.current_pinned_effect()
        return effect_label(effect) if effect else AUTO

    async def async_select_option(self, option: str) -> None:
        self._director.set_effect_pin(EFFECT_BY_LABEL.get(option))
