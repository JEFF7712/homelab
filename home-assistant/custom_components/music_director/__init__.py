"""music_director: album-art colours and genre-aware LedFx effects per track."""

from __future__ import annotations

import os

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant, ServiceCall
from homeassistant.helpers.typing import ConfigType

from .director import Director, DirectorConfig

DOMAIN = "music_director"

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required("player"): cv.entity_id,
                vol.Required("spotify"): cv.entity_id,
                vol.Required("enabled"): cv.entity_id,
                vol.Required("strip"): cv.entity_id,
                vol.Required("ledfx_url"): cv.url,
                vol.Required("ledfx_virtual"): cv.string,
                vol.Required("library_url"): cv.url,
                vol.Required("areas"): vol.All(cv.ensure_list, [cv.slug]),
                vol.Required("effect_select"): cv.entity_id,
                vol.Optional("transition_time", default=1.5): vol.All(
                    vol.Coerce(float), vol.Range(min=0, max=5)
                ),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    conf = config[DOMAIN]
    director = Director(
        hass,
        DirectorConfig(
            player=conf["player"],
            spotify=conf["spotify"],
            enabled=conf["enabled"],
            strip=conf["strip"],
            ledfx_url=conf["ledfx_url"].rstrip("/"),
            ledfx_virtual=conf["ledfx_virtual"],
            library_url=conf["library_url"].rstrip("/"),
            areas=tuple(conf["areas"]),
            effect_select=conf["effect_select"],
            transition_time=conf["transition_time"],
            lastfm_key=os.environ.get("LASTFM_API_KEY") or None,
        ),
    )
    hass.data[DOMAIN] = director

    async def refresh(_call: ServiceCall) -> None:
        director.schedule(force=True)

    hass.services.async_register(DOMAIN, "refresh", refresh)

    if hass.is_running:
        director.async_start()
    else:

        async def start(_event: Event) -> None:
            director.async_start()

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, start)
    return True
