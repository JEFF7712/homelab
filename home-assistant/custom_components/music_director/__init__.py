"""music_director: album-art colours and genre-aware LedFx effects per track."""

from __future__ import annotations

import os

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import Event, HomeAssistant, ServiceCall
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.typing import ConfigType

from .director import DOMAIN, Director, DirectorConfig
from .metadata import SongOverride
from .palette import NAMED_HUES, hex_rgb
from .rules import EFFECTS


def _colour_name(value: object) -> str:
    """A NAMED_HUES colour name, or a #RRGGBB colour."""
    if hex_rgb(str(value)) is not None:
        return str(value).strip().lower()
    name = " ".join(str(value).lower().replace("_", " ").split())
    if name not in NAMED_HUES:
        raise vol.Invalid(
            f"unknown colour {value!r}; use #RRGGBB or one of {sorted(NAMED_HUES)}"
        )
    return name


def _effect_name(value: object) -> str:
    name = str(value).strip().lower().replace(" ", "_")
    if name not in EFFECTS:
        raise vol.Invalid(f"unknown effect {value!r}; use one of {list(EFFECTS)}")
    return name


OVERRIDE_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional("artist"): cv.string,
            vol.Optional("album"): cv.string,
            vol.Optional("title"): cv.string,
            vol.Optional("color"): _colour_name,
            vol.Optional("effect"): _effect_name,
        }
    ),
    cv.has_at_least_one_key("artist", "album", "title"),
    cv.has_at_least_one_key("color", "effect"),
)

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
                vol.Optional("overrides", default=[]): vol.All(
                    cv.ensure_list, [OVERRIDE_SCHEMA]
                ),
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
            overrides=tuple(SongOverride(**item) for item in conf["overrides"]),
            transition_time=conf["transition_time"],
            lastfm_key=os.environ.get("LASTFM_API_KEY") or None,
        ),
    )
    hass.data[DOMAIN] = director

    async def refresh(_call: ServiceCall) -> None:
        director.schedule(force=True)

    hass.services.async_register(DOMAIN, "refresh", refresh)
    # Music Mode Colour (light), Brightness (number), Strip Colours (select).
    for platform in (Platform.LIGHT, Platform.NUMBER, Platform.SELECT):
        hass.async_create_task(async_load_platform(hass, platform, DOMAIN, {}, config))

    if hass.is_running:
        director.async_start()
    else:

        async def start(_event: Event) -> None:
            director.async_start()

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, start)
    return True
