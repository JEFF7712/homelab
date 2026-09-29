"""Govee LAN light platform for H6004 bulbs over the documented UDP API."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    PLATFORM_SCHEMA,
    ColorMode,
    LightEntity,
)
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers import config_validation as cv

from . import (
    DOMAIN,
    MAX_CONSECUTIVE_FAILURES,
    MAX_COLOR_TEMP_KELVIN,
    MIN_COLOR_TEMP_KELVIN,
    GoveeLanHub,
    brightness_message,
    color_rgb_message,
    color_temp_message,
    get_hub,
    ha_brightness_to_pct,
    pct_to_ha_brightness,
    turn_message,
)

CONF_LIGHTS = "lights"
CONF_HOST = "host"

SCAN_INTERVAL = timedelta(seconds=60)

_LIGHT_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional("name"): cv.string,
    }
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {vol.Required(CONF_LIGHTS): vol.Schema({cv.string: _LIGHT_SCHEMA})}
)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    hub = get_hub(hass)
    if hub is None:
        hub = GoveeLanHub()
        try:
            await hub.async_bind()
        except OSError as err:
            raise PlatformNotReady(
                f"Govee LAN cannot bind UDP {4002}; retrying: {err}"
            ) from err
        hass.data[DOMAIN] = hub
    entities = [
        GoveeLanLight(slug, entry.get("name", slug), entry[CONF_HOST], hub)
        for slug, entry in config[CONF_LIGHTS].items()
    ]
    async_add_entities(entities)


class GoveeLanLight(LightEntity):
    _attr_supported_color_modes = {ColorMode.RGB, ColorMode.COLOR_TEMP}
    _attr_min_color_temp_kelvin = MIN_COLOR_TEMP_KELVIN
    _attr_max_color_temp_kelvin = MAX_COLOR_TEMP_KELVIN

    def __init__(self, slug: str, name: str, host: str, hub: GoveeLanHub) -> None:
        self._slug = slug
        self._attr_name = name
        self._host = host
        self._hub = hub
        self._failures = 0
        self._attr_unique_id = f"{DOMAIN}_{slug}"
        self._attr_available = True
        self._attr_color_mode = ColorMode.RGB
        self._attr_brightness = 255
        self._attr_rgb_color = (255, 255, 255)

    async def async_turn_on(self, **kwargs: Any) -> None:
        if not self.is_on:
            self._hub.send(self._host, turn_message(True))
        if ATTR_BRIGHTNESS in kwargs:
            self._hub.send(
                self._host,
                brightness_message(ha_brightness_to_pct(kwargs[ATTR_BRIGHTNESS])),
            )
            self._attr_brightness = kwargs[ATTR_BRIGHTNESS]
        if ATTR_RGB_COLOR in kwargs:
            red, green, blue = kwargs[ATTR_RGB_COLOR]
            self._hub.send(self._host, color_rgb_message(red, green, blue))
            self._attr_rgb_color = (red, green, blue)
            self._attr_color_mode = ColorMode.RGB
        elif ATTR_COLOR_TEMP_KELVIN in kwargs:
            kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
            self._hub.send(self._host, color_temp_message(kelvin))
            self._attr_color_temp_kelvin = kelvin
            self._attr_color_mode = ColorMode.COLOR_TEMP
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._hub.send(self._host, turn_message(False))
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_update(self) -> None:
        try:
            state = await self._hub.query(self._host)
        except RuntimeError:
            state = None
        if state is None:
            self._failures += 1
            self._attr_available = self._failures < MAX_CONSECUTIVE_FAILURES
            return
        self._failures = 0
        self._attr_available = True
        self._attr_is_on = state["on"]
        self._attr_brightness = pct_to_ha_brightness(state["brightness"])
        if state["kelvin"]:
            self._attr_color_mode = ColorMode.COLOR_TEMP
            self._attr_color_temp_kelvin = state["kelvin"]
        else:
            self._attr_color_mode = ColorMode.RGB
            self._attr_rgb_color = state["rgb"]
