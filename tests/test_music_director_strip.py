"""Tests for how the music_director keeps the LedFx strip in step.

The director needs Home Assistant and aiohttp, which the offline test
environment lacks, so minimal stand-ins are installed just long enough to
import it. The LedFx calls are recorded instead of sent.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "home-assistant" / "custom_components" / "music_director"

ENABLED = "input_boolean.shared_music_mode"
STRIP = "input_boolean.music_mode_led_strip"


class State:
    def __init__(
        self, entity_id: str, state: str, attributes: dict[str, Any] | None = None
    ) -> None:
        self.entity_id = entity_id
        self.state = state
        self.attributes = attributes or {}


def _stubs() -> dict[str, types.ModuleType]:
    def module(name: str, **attrs: Any) -> types.ModuleType:
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        return mod

    class Store:
        def __init__(self, *args: Any) -> None:
            pass

        def async_delay_save(self, *args: Any) -> None:
            pass

    oauth = module("homeassistant.helpers.config_entry_oauth2_flow")
    registry = module("homeassistant.helpers.entity_registry")
    return {
        "aiohttp": module(
            "aiohttp",
            ClientTimeout=lambda **kwargs: kwargs,
            ClientError=type("ClientError", (Exception,), {}),
            WSMsgType=types.SimpleNamespace(TEXT=1),
        ),
        "homeassistant": module("homeassistant", __path__=[]),
        "homeassistant.const": module("homeassistant.const", STATE_ON="on"),
        "homeassistant.core": module(
            "homeassistant.core",
            Event=object,
            EventStateChangedData=dict,
            HomeAssistant=object,
            State=State,
            callback=lambda func: func,
        ),
        "homeassistant.helpers": module(
            "homeassistant.helpers",
            __path__=[],
            config_entry_oauth2_flow=oauth,
            entity_registry=registry,
        ),
        "homeassistant.helpers.config_entry_oauth2_flow": oauth,
        "homeassistant.helpers.entity_registry": registry,
        "homeassistant.helpers.aiohttp_client": module(
            "homeassistant.helpers.aiohttp_client",
            async_get_clientsession=lambda hass: None,
        ),
        "homeassistant.helpers.dispatcher": module(
            "homeassistant.helpers.dispatcher",
            async_dispatcher_send=lambda *args: None,
        ),
        "homeassistant.helpers.event": module(
            "homeassistant.helpers.event",
            async_track_state_change_event=lambda *args: None,
        ),
        "homeassistant.helpers.storage": module(
            "homeassistant.helpers.storage", Store=Store
        ),
        "homeassistant.util": module("homeassistant.util", __path__=[]),
        "homeassistant.util.json": module(
            "homeassistant.util.json", json_loads=json.loads
        ),
    }


def _load_director() -> types.ModuleType:
    if "music_director" not in sys.modules:
        package = types.ModuleType("music_director")
        package.__path__ = [str(PACKAGE_DIR)]
        sys.modules["music_director"] = package
    stubs = _stubs()
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        return importlib.import_module("music_director.director")
    finally:
        # Other test modules install their own Home Assistant stand-ins.
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


director = _load_director()
palette = importlib.import_module("music_director.palette")
metadata = importlib.import_module("music_director.metadata")


class States:
    def __init__(self) -> None:
        self.values: dict[str, State] = {}

    def get(self, entity_id: str) -> State | None:
        return self.values.get(entity_id)

    def async_set(
        self, entity_id: str, state: str, attributes: dict[str, Any] | None = None
    ) -> None:
        self.values[entity_id] = State(entity_id, state, attributes)


class Hass:
    def __init__(self) -> None:
        self.states = States()
        self.tasks: list[asyncio.Task[Any]] = []

    def async_create_task(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.ensure_future(coro)
        self.tasks.append(task)
        return task

    def async_create_background_task(self, coro: Any, name: str) -> asyncio.Task[Any]:
        return self.async_create_task(coro)

    async def settle(self) -> None:
        while pending := [t for t in self.tasks if not t.done()]:
            await asyncio.gather(*pending)


class LedFx:
    """Records strip calls. A call can be held open, and fails like LedFx
    while fail_with is set."""

    def __init__(self, owner: Any) -> None:
        self.owner = owner
        self.log: list[str] = []
        self.calls: list[tuple[str, Any]] = []
        self.hold: asyncio.Event | None = None
        self.fail_with: str | None = None

    async def __call__(self, method: str, payload: Any = None) -> bool:
        self.calls.append((method, payload))
        self.log.append(f"{method} start")
        if self.hold is not None:
            await self.hold.wait()
        self.log.append(f"{method} end")
        if self.fail_with is not None:
            self.owner._set_ledfx_status(self.fail_with)
            return False
        self.owner._set_ledfx_status("ok")
        return True

    def methods(self) -> list[str]:
        return [method for method, _ in self.calls]


def make_director() -> tuple[Any, Hass, LedFx]:
    hass = Hass()
    hass.states.async_set(ENABLED, "on")
    hass.states.async_set(STRIP, "on")
    config = director.DirectorConfig(
        player="media_player.speaker",
        spotify="media_player.spotify",
        enabled=ENABLED,
        strip=STRIP,
        ledfx_url="http://ledfx",
        ledfx_virtual="strip",
        library_url="http://library",
        areas=("kitchen", "lounge"),
        effect_select="select.music_mode_effect",
        transition_time=1.5,
    )
    music = director.Director(hass, config)  # type: ignore[arg-type]
    ledfx = LedFx(music)
    music._ledfx = ledfx
    return music, hass, ledfx


def song(music: Any, title: str = "Song") -> Any:
    return music._compose(
        title,
        "Artist",
        "Album",
        metadata.TrackInfo(),
        palette.vibe_palette(),
        "default",
        "energy",
        0.5,
    )


class TestStripUpdates(unittest.IsolatedAsyncioTestCase):
    async def test_new_track_starts_its_effect(self) -> None:
        music, _, ledfx = make_director()
        await music._apply(song(music))
        self.assertEqual(ledfx.methods(), ["POST"])

    async def test_unchanged_decision_leaves_ledfx_alone(self) -> None:
        music, _, ledfx = make_director()
        await music._apply(song(music))
        await music._apply(music._recompose(music._decision))
        self.assertEqual(ledfx.methods(), ["POST"])

    async def test_brightness_change_updates_in_place(self) -> None:
        music, _, ledfx = make_director()
        await music._apply(song(music))
        music.brightness_pct = 40
        await music._apply(music._recompose(music._decision))
        self.assertEqual(ledfx.methods(), ["POST", "PUT"])
        self.assertEqual(ledfx.calls[-1][1]["config"]["brightness"], 0.36)

    async def test_restarted_ledfx_gets_the_effect_again(self) -> None:
        # A PUT on an effect LedFx no longer runs is refused: start it.
        music, _, ledfx = make_director()
        await music._apply(song(music))
        music.brightness_pct = 40
        ledfx.fail_with = "error"
        await music._apply(music._recompose(music._decision))
        self.assertEqual(ledfx.methods(), ["POST", "PUT", "POST"])

    async def test_unreachable_ledfx_is_not_hammered(self) -> None:
        music, _, ledfx = make_director()
        await music._apply(song(music))
        music.brightness_pct = 40
        ledfx.fail_with = "unreachable"
        await music._apply(music._recompose(music._decision))
        self.assertEqual(ledfx.methods(), ["POST", "PUT"])

    async def test_ledfx_coming_back_restarts_the_effect(self) -> None:
        music, hass, ledfx = make_director()
        await music._apply(song(music))
        music._set_ledfx_status("unreachable")
        music._set_ledfx_status("ok")
        await hass.settle()
        self.assertEqual(ledfx.methods(), ["POST", "POST"])

    async def test_strip_off_lands_after_an_update_in_flight(self) -> None:
        music, hass, ledfx = make_director()
        ledfx.hold = asyncio.Event()
        update = hass.async_create_task(music._apply(song(music)))
        await asyncio.sleep(0)
        clear = hass.async_create_task(music._clear_strip())
        await asyncio.sleep(0)
        ledfx.hold.set()
        await asyncio.gather(update, clear)
        self.assertEqual(
            ledfx.log, ["POST start", "POST end", "DELETE start", "DELETE end"]
        )
        self.assertIsNone(music._sent)

    async def test_strip_on_starts_the_current_effect(self) -> None:
        music, hass, ledfx = make_director()
        await music._apply(song(music))
        await music._clear_strip()
        await music._strip_on()
        self.assertEqual(ledfx.methods(), ["POST", "DELETE", "POST"])


class TestAfterMusicModeEnds(unittest.IsolatedAsyncioTestCase):
    async def test_late_decision_changes_nothing(self) -> None:
        music, hass, ledfx = make_director()
        hass.states.async_set(ENABLED, "off")
        await music._apply(song(music))
        self.assertEqual(ledfx.calls, [])
        self.assertIsNone(hass.states.get(director.SENSOR))
        self.assertIsNone(music._decision)

    async def test_cover_found_after_the_track_changed_is_dropped(self) -> None:
        music, _, ledfx = make_director()
        await music._apply(song(music))
        music._track_key = "artist\nsong\nalbum"
        original = director.COVER_RECHECK_SECONDS
        director.COVER_RECHECK_SECONDS = (0.0,)

        async def cover_for(key: str) -> Any:
            music._track_key = "artist\nnext song\nalbum"
            return palette.vibe_palette()

        music._cover_for = cover_for
        try:
            await music._recheck_cover("artist\nsong\nalbum")
        finally:
            director.COVER_RECHECK_SECONDS = original
        self.assertEqual(ledfx.methods(), ["POST"])


class TestDropFlash(unittest.IsolatedAsyncioTestCase):
    async def test_no_flash_on_an_effect_ledfx_does_not_run(self) -> None:
        music, hass, ledfx = make_director()
        ledfx.fail_with = "unreachable"
        await music._apply(song(music))
        music._flash()
        await hass.settle()
        self.assertIsNone(music._flash_task)
        self.assertEqual(ledfx.methods(), ["POST"])

    async def test_flash_stops_when_the_effect_changes(self) -> None:
        music, hass, ledfx = make_director()
        original = director.FLASH_STEPS
        director.FLASH_STEPS = ((1.0, 0.0), (0.5, 0.0))
        try:
            await music._apply(song(music))
            ledfx.hold = asyncio.Event()
            music._flash()
            while len(ledfx.calls) < 2:
                await asyncio.sleep(0)
            # The first flash step is out; the dropdown picks another effect.
            changed = music._compose(
                "Song",
                "Artist",
                "Album",
                metadata.TrackInfo(),
                palette.vibe_palette(),
                "default",
                "power",
                0.5,
            )
            music._decision = changed
            ledfx.hold.set()
            await hass.settle()
        finally:
            director.FLASH_STEPS = original
        self.assertEqual(ledfx.methods(), ["POST", "PUT", "POST"])
        self.assertEqual(ledfx.calls[-1][1]["type"], "power")
        self.assertIs(music._sent, changed)


if __name__ == "__main__":
    unittest.main()
