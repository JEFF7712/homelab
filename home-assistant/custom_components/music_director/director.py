"""Home Assistant side of music_director: watch the player, gather metadata,
decide a palette and LedFx effect once per track, and publish the result.

The LED strip is driven here through the LedFx API. Bulb areas are painted
by the shared music mode automation from sensor.music_director, so all
snapshot and Adaptive Lighting handling stays in Home Assistant YAML.

The manual colour, album colour pins and the music brightness live here
and are set by the integration's lights (Music Mode Colour, Music Mode Pin)
and number (Music Mode Brightness).
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from homeassistant.const import STATE_ON
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store

from .metadata import (
    SongOverride,
    TrackInfo,
    apply_lastfm,
    apply_library,
    apply_reccobeats,
    find_override,
    same_track,
    spotify_track_id,
)
from .palette import (
    Palette,
    area_colors,
    chosen_palette,
    extract_palette,
    gradient,
    named_palette,
    solid_colour,
    solid_gradient,
    spread_label,
    to_hex,
    vibe_palette,
    with_intensity,
)
from .pins import STORAGE_KEY, STORAGE_VERSION, PinStore
from .rules import (
    EFFECTS,
    choose_effect,
    classify,
    effect_config,
    intensity,
    tempo_level,
)

_LOGGER = logging.getLogger(__name__)

DOMAIN = "music_director"
SENSOR = "sensor.music_director"
# Dispatcher signal for the light and number entities to refresh their state.
CONTROLS_UPDATED = f"{DOMAIN}_controls_updated"
MIN_BRIGHTNESS = 10
MAX_BRIGHTNESS = 100
DEFAULT_BRIGHTNESS = 70
RECCOBEATS_URL = "https://api.reccobeats.com/v1/audio-features"
LASTFM_URL = "https://ws.audioscrobbler.com/2.0/"
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=8)
# The Spotify Web API quota is shared with Home Assistant's own polling: when
# Spotify lags the speaker, ask for at most one refresh per track change, and
# none within SPOTIFY_REFRESH_GAP seconds of the last or while Spotify is
# unavailable (it goes unavailable when rate limited).
SPOTIFY_RETRY_SECONDS = 1.5
SPOTIFY_REFRESH_GAP = 30.0
# The player's art can lag its title on a track change; try again before
# falling back to the vibe palette.
COVER_ATTEMPTS = 3
COVER_RETRY_SECONDS = 2.0
ART_SIZE = 48
MAX_ART_BYTES = 5_000_000
CACHE_SIZE = 256


@dataclass(frozen=True)
class DirectorConfig:
    player: str
    spotify: str
    enabled: str
    strip: str
    ledfx_url: str
    ledfx_virtual: str
    library_url: str
    areas: tuple[str, ...]
    effect_select: str
    transition_time: float
    overrides: tuple[SongOverride, ...] = ()
    lastfm_key: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Decision:
    title: str
    artist: str
    album: str
    info: TrackInfo
    # The cover or vibe palette, before any pin.
    natural: Palette
    # natural, or the album's pin.
    auto_palette: Palette
    # auto_palette, or the manual colour.
    palette: Palette
    family: str
    auto_effect: str
    effect: str
    config: dict[str, Any]
    # The track's intensity, 0 (calm) to 1 (intense), whatever the colour.
    level: float = 0.5

    @property
    def manual(self) -> bool:
        return self.effect != self.auto_effect


def _track_key(state: State | None) -> str | None:
    if state is None or state.state != "playing":
        return None
    title = state.attributes.get("media_title")
    if not title:
        return None
    artist = state.attributes.get("media_artist") or ""
    album = state.attributes.get("media_album_name") or ""
    return f"{artist}\n{title}\n{album}"


def _decode_pixels(data: bytes) -> list[tuple[int, int, int]]:
    from PIL import Image  # Home Assistant ships Pillow; import off the event loop.

    with Image.open(io.BytesIO(data)) as image:
        raw = image.convert("RGB").resize((ART_SIZE, ART_SIZE)).tobytes()
    return [(raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw), 3)]


def _cover_palette(data: bytes) -> Palette | None:
    return extract_palette(_decode_pixels(data))


class Director:
    def __init__(self, hass: HomeAssistant, config: DirectorConfig) -> None:
        self.hass = hass
        self.config = config
        self._session = async_get_clientsession(hass)
        self._cache: OrderedDict[str, tuple[TrackInfo, Palette | None]] = OrderedDict()
        self._task: asyncio.Task[None] | None = None
        self._track_key: str | None = None
        self._last_effect: str | None = None
        self._decision: Decision | None = None
        self._transition_set = False
        self._spotify_refreshed = 0.0
        # Why the last track had no Spotify ID, for the dashboard.
        self.spotify_status = "ok"
        # Chosen colour (Music Mode Colour light on), or None for Auto.
        self.manual_rgb: tuple[int, int, int] | None = None
        self.brightness_pct = DEFAULT_BRIGHTNESS
        self.pins = PinStore(Store(hass, STORAGE_VERSION, STORAGE_KEY))
        # Music Mode Strip Colours: one solid colour instead of a gradient.
        self.strip_solid = False

    @callback
    def async_start(self) -> None:
        async_track_state_change_event(
            self.hass,
            [
                self.config.player,
                self.config.enabled,
                self.config.strip,
                self.config.effect_select,
            ],
            self._on_change,
        )
        if self._is_on(self.config.enabled):
            self.schedule(force=True)

    def _is_on(self, entity_id: str) -> bool:
        state = self.hass.states.get(entity_id)
        return state is not None and state.state == STATE_ON

    @callback
    def _on_change(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        new_state = event.data["new_state"]
        if entity_id == self.config.enabled:
            if new_state is not None and new_state.state == STATE_ON:
                self.schedule(force=True)
            else:
                self._cancel()
                self.hass.async_create_task(self._stop())
            return
        if not self._is_on(self.config.enabled):
            return
        if entity_id == self.config.effect_select:
            self._reapply()
            return
        if entity_id == self.config.strip:
            if new_state is not None and new_state.state == STATE_ON:
                if self._decision is not None:
                    self.hass.async_create_task(self._push_strip(self._decision))
            else:
                self.hass.async_create_task(self._clear_strip())
            return
        key = _track_key(new_state)
        if key is not None and key != self._track_key:
            self.schedule(force=False)

    @callback
    def _reapply(self) -> None:
        """Recompose the current track's decision after a manual control
        changed, without fetching anything again."""
        if self._decision is not None and self._is_on(self.config.enabled):
            self.hass.async_create_task(self._apply(self._recompose(self._decision)))

    @callback
    def set_colour(self, rgb: tuple[int, int, int] | None) -> None:
        """Hold a chosen colour on every light, or None to go back to Auto."""
        self.manual_rgb = rgb
        async_dispatcher_send(self.hass, CONTROLS_UPDATED)
        self._reapply()

    @property
    def current_track(self) -> Decision | None:
        """The playing track's decision, or None when idle."""
        decision = self._decision
        return decision if decision is not None and decision.title else None

    def current_pin(self) -> str | None:
        """The current track's pinned colour (a name or #rrggbb), or None."""
        track = self.current_track
        if track is None:
            return None
        pin = self.pins.pins.find(track.title, track.artist, track.album)
        return pin.color if pin else None

    def showing_rgb(self) -> tuple[int, int, int] | None:
        """The colour the strip shows for the current track."""
        track = self.current_track
        return solid_colour(track.palette) if track else None

    @callback
    def set_pin(self, rgb: tuple[int, int, int] | None) -> None:
        """Pin a colour to the current track's album, or None to unpin it so
        the album goes back to automatic colours."""
        track = self.current_track
        if track is None:
            return
        if rgb is None:
            self.pins.pins.unpin(track.title, track.artist, track.album)
        else:
            self.pins.pins.pin(track.title, track.artist, track.album, to_hex(rgb))
        self.pins.save()
        async_dispatcher_send(self.hass, CONTROLS_UPDATED)
        self._reapply()

    @callback
    def set_strip_solid(self, solid: bool) -> None:
        self.strip_solid = solid
        async_dispatcher_send(self.hass, CONTROLS_UPDATED)
        self._reapply()

    @callback
    def set_brightness(self, pct: int) -> None:
        self.brightness_pct = min(MAX_BRIGHTNESS, max(MIN_BRIGHTNESS, pct))
        async_dispatcher_send(self.hass, CONTROLS_UPDATED)
        self._reapply()

    def _cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    @callback
    def schedule(self, force: bool) -> None:
        self._cancel()
        self._task = self.hass.async_create_task(self._run(force))

    def _override(self) -> str | None:
        """The dropdown's effect, or None for Auto or an unknown option."""
        state = self.hass.states.get(self.config.effect_select)
        option = (state.state if state else "").strip().lower().replace(" ", "_")
        return option if option in EFFECTS else None

    def _colour_override(self) -> Palette | None:
        """The Music Mode Colour light's palette, or None for Auto."""
        return chosen_palette(self.manual_rgb) if self.manual_rgb else None

    def _compose(
        self,
        title: str,
        artist: str,
        album: str,
        info: TrackInfo,
        natural: Palette,
        family: str,
        auto_effect: str,
        level: float,
    ) -> Decision:
        pin = self.pins.pins.find(title, artist, album) if title else None
        pinned = named_palette(pin.color, "override") if pin and pin.color else None
        auto_palette = pinned or natural
        palette = self._colour_override() or auto_palette
        effect = self._override() or auto_effect
        config = {
            **effect_config(effect, palette, info.features(), self.strip_solid),
            "brightness": round(self.brightness_pct / 100, 2),
        }
        return Decision(
            title,
            artist,
            album,
            info,
            natural,
            auto_palette,
            palette,
            family,
            auto_effect,
            effect,
            config,
            level,
        )

    def _recompose(self, decision: Decision) -> Decision:
        return self._compose(
            decision.title,
            decision.artist,
            decision.album,
            decision.info,
            decision.natural,
            decision.family,
            decision.auto_effect,
            decision.level,
        )

    async def _apply(self, decision: Decision) -> None:
        self._decision = decision
        self._publish(decision)
        # The pin light follows the playing track.
        async_dispatcher_send(self.hass, CONTROLS_UPDATED)
        if self._is_on(self.config.strip):
            await self._push_strip(decision)

    async def _run(self, force: bool) -> None:
        if not self._transition_set:
            self._transition_set = await self._ensure_transition()
        player = self.hass.states.get(self.config.player)
        key = _track_key(player)
        if key is None:
            if self._decision is not None and not force:
                return
            decision = self._idle_decision()
            self._track_key = None
        else:
            if key == self._track_key and not force:
                return
            self._track_key = key
            assert player is not None
            decision = await self._decide(player, key)
        # Repeat avoidance follows the automatic choice, not the override.
        self._last_effect = decision.auto_effect
        await self._apply(decision)

    def _idle_decision(self) -> Decision:
        idle = vibe_palette()
        return self._compose(
            "", "", "", TrackInfo(), idle, "default", "energy", idle.intensity
        )

    async def _decide(self, player: State, key: str) -> Decision:
        cached = self._cache.get(key)
        if cached is None:
            info, cover = await asyncio.gather(
                self._gather_info(player), self._cover_for(key)
            )
            cached = (info, cover)
            self._cache[key] = cached
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        else:
            self._cache.move_to_end(key)
            if cached[1] is None:
                # A missed cover is not remembered: try again on replay.
                cached = (cached[0], await self._cover_for(key))
                self._cache[key] = cached
        info, cover = cached
        features = info.features()
        family = classify(features)
        level = intensity(features, family)
        title = player.attributes.get("media_title") or ""
        artist = player.attributes.get("media_artist") or ""
        album = player.attributes.get("media_album_name") or ""
        # Colours come from pins (seeded from configured overrides); the
        # configured overrides still set effects.
        song = find_override(
            tuple(o for o in self.config.overrides if o.effect), title, artist, album
        )
        palette = (
            with_intensity(cover, level)
            if cover
            else vibe_palette(
                info.genres,
                info.energy,
                info.valence,
                tempo_level(info.bpm),
                level,
            )
        )
        if song and song.effect:
            effect = song.effect
        else:
            effect = choose_effect(family, key, self._last_effect)
        decision = self._compose(
            title, artist, album, info, palette, family, effect, level
        )
        _LOGGER.info(
            "%s - %s: %s/%s%s, intensity %.2f, palette %s (hue %.0f), sources %s",
            artist,
            title,
            family,
            decision.effect,
            " (manual)" if decision.manual else "",
            level,
            decision.palette.source,
            decision.palette.hue,
            ",".join(info.sources) or "none",
        )
        return decision

    async def _get_json(self, url: str, params: dict[str, str], label: str) -> Any:
        try:
            async with self._session.get(
                url, params=params, timeout=HTTP_TIMEOUT
            ) as response:
                if response.status != 200:
                    _LOGGER.debug("%s lookup returned HTTP %s", label, response.status)
                    return None
                return await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            # The exception text can carry request URLs, and Last.fm's has the key.
            _LOGGER.debug("%s lookup failed: %s", label, type(error).__name__)
            return None

    def _spotify_match(self, title: str, artist: str) -> str | None:
        state = self.hass.states.get(self.config.spotify)
        if state is None or state.state in ("unavailable", "unknown"):
            self.spotify_status = "unavailable"
            return None
        if same_track(
            title,
            artist,
            state.attributes.get("media_title") or "",
            state.attributes.get("media_artist") or "",
        ):
            self.spotify_status = "ok"
            return spotify_track_id(state.attributes.get("media_content_id"))
        self.spotify_status = "other track"
        return None

    async def _spotify_id(self, title: str, artist: str) -> str | None:
        """The track's Spotify ID from Spotify's media player. The speaker
        plays through Spotify Connect into Music Assistant and reports no
        ID, so this is the only source."""
        found = self._spotify_match(title, artist)
        if found or self.spotify_status == "unavailable":
            return found
        now = time.monotonic()
        if now - self._spotify_refreshed < SPOTIFY_REFRESH_GAP:
            return None
        self._spotify_refreshed = now
        try:
            await self.hass.services.async_call(
                "homeassistant",
                "update_entity",
                {"entity_id": self.config.spotify},
                blocking=True,
            )
        except Exception as error:  # noqa: BLE001 - polling is best effort
            _LOGGER.debug("Spotify refresh failed: %s", type(error).__name__)
        await asyncio.sleep(SPOTIFY_RETRY_SECONDS)
        return self._spotify_match(title, artist)

    async def _gather_info(self, player: State) -> TrackInfo:
        info = TrackInfo()
        title = player.attributes.get("media_title") or ""
        artist = player.attributes.get("media_artist") or ""
        duration = player.attributes.get("media_duration")
        spotify_id = await self._spotify_id(title, artist)
        if spotify_id:
            apply_reccobeats(
                info,
                await self._get_json(RECCOBEATS_URL, {"ids": spotify_id}, "ReccoBeats"),
            )
        params = {"artist": artist, "title": title, "isrc": info.isrc or ""}
        if isinstance(duration, (int, float)) and duration > 0:
            params["duration"] = str(duration)
        apply_library(
            info,
            await self._get_json(
                f"{self.config.library_url}/lookup", params, "Library"
            ),
        )
        if not info.genres and self.config.lastfm_key:
            for method, extra in (
                ("track.gettoptags", {"track": title}),
                ("artist.gettoptags", {}),
            ):
                body = await self._get_json(
                    LASTFM_URL,
                    {
                        "method": method,
                        "artist": artist,
                        "autocorrect": "1",
                        "api_key": self.config.lastfm_key,
                        "format": "json",
                        **extra,
                    },
                    "Last.fm",
                )
                apply_lastfm(info, body, artist)
                if info.genres:
                    break
        return info

    async def _cover_for(self, key: str) -> Palette | None:
        """The cover palette for the track in key. Spotify's art is used
        when Spotify reports the same track, since it changes with the
        title; otherwise the player's. Retries only while no art can be
        downloaded, and gives up if the track changes."""
        for attempt in range(COVER_ATTEMPTS):
            player = self.hass.states.get(self.config.player)
            if _track_key(player) != key:
                return None
            assert player is not None
            title = player.attributes.get("media_title") or ""
            artist = player.attributes.get("media_artist") or ""
            spotify = self.hass.states.get(self.config.spotify)
            sources = [player]
            if spotify is not None and same_track(
                title,
                artist,
                spotify.attributes.get("media_title") or "",
                spotify.attributes.get("media_artist") or "",
            ):
                sources.insert(0, spotify)
            for source in sources:
                data = await self._fetch_art(source)
                if data is None:
                    continue
                try:
                    cover = await self.hass.async_add_executor_job(_cover_palette, data)
                except (OSError, ValueError) as error:
                    _LOGGER.debug("Cover art unreadable: %s", type(error).__name__)
                    continue
                if cover is None:
                    _LOGGER.info(
                        "No usable cover colour for %s", key.replace("\n", " - ")
                    )
                return cover
            if attempt < COVER_ATTEMPTS - 1:
                await asyncio.sleep(COVER_RETRY_SECONDS)
        _LOGGER.info("No cover art for %s", key.replace("\n", " - "))
        return None

    async def _fetch_art(self, state: State) -> bytes | None:
        picture = state.attributes.get("entity_picture")
        if not picture:
            return None
        if not picture.startswith("http"):
            picture = f"http://127.0.0.1:{self.hass.http.server_port}{picture}"
        try:
            async with self._session.get(picture, timeout=HTTP_TIMEOUT) as response:
                if response.status != 200:
                    return None
                return await response.content.read(MAX_ART_BYTES)
        except (aiohttp.ClientError, TimeoutError) as error:
            _LOGGER.debug("Cover art unavailable: %s", type(error).__name__)
            return None

    def _publish(self, decision: Decision) -> None:
        colours = area_colors(decision.palette, len(self.config.areas))
        info = decision.info
        self.hass.states.async_set(
            SENSOR,
            decision.effect,
            {
                "friendly_name": "Music Director",
                "icon": "mdi:palette",
                "title": decision.title,
                "artist": decision.artist,
                "genres": list(info.genres),
                "bpm": round(info.bpm) if info.bpm else None,
                "energy": info.energy,
                "valence": info.valence,
                "family": decision.family,
                "effect": decision.effect,
                "auto_effect": decision.auto_effect,
                "effect_mode": "manual" if decision.manual else "auto",
                "effect_config": {
                    k: v for k, v in decision.config.items() if k != "gradient"
                },
                "palette_source": decision.palette.source,
                "palette_spread": spread_label(decision.palette),
                "colour_mode": "manual" if self.manual_rgb else "auto",
                "album": decision.album,
                "pinned_colour": self.current_pin(),
                "natural_palette_source": decision.natural.source,
                "spotify_status": self.spotify_status,
                "brightness_pct": self.brightness_pct,
                "auto_palette_source": decision.auto_palette.source,
                "intensity": round(decision.level, 3),
                "metadata_sources": list(info.sources),
                "area_colors": {
                    area: list(colour)
                    for area, colour in zip(self.config.areas, colours, strict=True)
                },
                "strip_style": "solid" if self.strip_solid else "gradient",
                "gradient": (
                    solid_gradient(decision.palette)
                    if self.strip_solid
                    else gradient(decision.palette)
                ),
            },
        )

    async def _ledfx(self, method: str, payload: dict[str, Any] | None = None) -> None:
        url = (
            f"{self.config.ledfx_url}/api/virtuals/{self.config.ledfx_virtual}/effects"
        )
        try:
            async with self._session.request(
                method, url, json=payload, timeout=HTTP_TIMEOUT
            ) as response:
                if response.status >= 400:
                    _LOGGER.warning(
                        "LedFx %s returned HTTP %s: %s",
                        method,
                        response.status,
                        (await response.text())[:200],
                    )
        except (aiohttp.ClientError, TimeoutError) as error:
            _LOGGER.warning("LedFx %s failed: %s", method, error)

    async def _ensure_transition(self) -> bool:
        """Set the strip's crossfade between effects. LedFx keeps virtual
        config on POST /api/virtuals with an id, so send the full config back
        with only transition_time changed. Returns True once it is in place."""
        base = f"{self.config.ledfx_url}/api/virtuals"
        virtual = self.config.ledfx_virtual
        try:
            async with self._session.get(
                f"{base}/{virtual}", timeout=HTTP_TIMEOUT
            ) as response:
                body = await response.json(content_type=None)
            config = dict(body[virtual]["config"])
            if config.get("transition_time") == self.config.transition_time:
                return True
            config["transition_time"] = self.config.transition_time
            async with self._session.post(
                base, json={"id": virtual, "config": config}, timeout=HTTP_TIMEOUT
            ) as response:
                if response.status >= 400:
                    _LOGGER.warning(
                        "LedFx transition update returned %s", response.status
                    )
                    return False
            return True
        except (
            aiohttp.ClientError,
            TimeoutError,
            ValueError,
            KeyError,
            TypeError,
        ) as error:
            _LOGGER.warning("LedFx transition update failed: %s", type(error).__name__)
            return False

    async def _push_strip(self, decision: Decision) -> None:
        await self._ledfx("POST", {"type": decision.effect, "config": decision.config})

    async def _clear_strip(self) -> None:
        await self._ledfx("DELETE")

    async def _stop(self) -> None:
        self._track_key = None
        self._decision = None
        self.hass.states.async_set(
            SENSOR, "off", {"friendly_name": "Music Director", "icon": "mdi:palette"}
        )
        await self._clear_strip()
