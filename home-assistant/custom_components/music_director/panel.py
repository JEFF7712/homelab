"""Music Colours: a sidebar panel to review and pin playlist song colours.

The panel (panel.js, served from this directory) talks to the director over
three websocket commands: list the prepared playlists, list one playlist's
songs with the colours and effect the lights would show, and pin or unpin a
colour or an effect.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from .director import DOMAIN, Director
from .palette import hex_rgb
from .pins import KEEP
from .rules import EFFECTS

PANEL_FILE = Path(__file__).parent / "panel.js"
STATIC_URL = "/music_director_static/panel.js"
PANEL_PATH = "music-colours"
ELEMENT = "music-colours-panel"


def _director(hass: HomeAssistant) -> Director:
    return hass.data[DOMAIN]


def _hex(value: Any) -> str:
    if hex_rgb(str(value)) is None:
        raise vol.Invalid(f"not a #RRGGBB colour: {value!r}")
    return str(value).lower()


@websocket_api.websocket_command({vol.Required("type"): "music_director/playlists"})
@callback
def ws_playlists(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    connection.send_result(
        msg["id"],
        {"playlists": _director(hass).playlist_names(), "effects": list(EFFECTS)},
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "music_director/playlist",
        vol.Required("name"): str,
    }
)
@callback
def ws_playlist(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    rows = _director(hass).playlist_colours(msg["name"])
    if rows is None:
        connection.send_error(msg["id"], "not_found", "No such prepared playlist")
        return
    connection.send_result(msg["id"], rows)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "music_director/pin",
        vol.Required("title"): str,
        vol.Required("artist"): str,
        vol.Required("album"): str,
        # A #RRGGBB colour or an effect name; null unpins it, and a missing
        # key leaves it as it is.
        vol.Optional("colour"): vol.Any(None, _hex),
        vol.Optional("effect"): vol.Any(None, vol.In(EFFECTS)),
    }
)
@callback
def ws_pin(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    _director(hass).pin_song(
        msg["title"],
        msg["artist"],
        msg["album"],
        colour=msg.get("colour", KEEP),
        effect=msg.get("effect", KEEP),
    )
    connection.send_result(msg["id"])


async def async_setup_panel(hass: HomeAssistant) -> None:
    for command in (ws_playlists, ws_playlist, ws_pin):
        websocket_api.async_register_command(hass, command)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(PANEL_FILE), False)]
    )
    # The version busts the browser cache when the panel changes.
    version = hashlib.sha256(
        await hass.async_add_executor_job(PANEL_FILE.read_bytes)
    ).hexdigest()[:12]
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_PATH,
        webcomponent_name=ELEMENT,
        sidebar_title="Music Colours",
        sidebar_icon="mdi:palette-swatch",
        module_url=f"{STATIC_URL}?v={version}",
        require_admin=False,
    )
