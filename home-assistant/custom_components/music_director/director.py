"""Home Assistant side of music_director: watch the player, gather metadata,
decide a palette and LedFx effect once per track, and publish the result.

The LED strip is driven here through the LedFx API. Bulb areas are painted
by the shared music mode automation from sensor.music_director, so all
snapshot and Adaptive Lighting handling stays in Home Assistant YAML.

The manual colour, album colour pins and the music brightness live here
and are set by the integration's lights (Music Mode Colour, Music Mode Pin)
and number (Music Mode Brightness).

Looked-up tracks are cached across restarts (trackcache.py). While a song
plays, the next song in Spotify's queue is looked up ahead, so the switch to
it only has to update LedFx.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
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
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util.json import json_loads

from .metadata import (
    QueuedTrack,
    SongOverride,
    TrackInfo,
    apply_lastfm,
    apply_library,
    apply_reccobeats,
    find_override,
    next_in_queue,
    normalize,
    reccobeats_by_id,
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
from .pins import KEEP, STORAGE_KEY, STORAGE_VERSION, Keep, PinStore
from .rules import (
    EFFECTS,
    choose_effect,
    classify,
    effect_config,
    intensity,
    tempo_level,
)
from .sections import (
    NORMAL,
    SECTION_BRIGHTNESS,
    SECTION_DRIVE,
    SECTION_INTENSITY,
    DropDetector,
    SectionTracker,
    bulb_brightness_pct,
)
from .trackcache import (
    CACHE_STORAGE_KEY,
    CACHE_STORAGE_VERSION,
    PLAYLIST_STORAGE_KEY,
    Entry,
    PlaylistSong,
    PlaylistStore,
    TrackCacheStore,
    track_key,
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
# LedFx is on the LAN and answers in milliseconds; strip updates wait on each
# other, so a hung LedFx must not hold the queue for long.
LEDFX_TIMEOUT = aiohttp.ClientTimeout(total=3)
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
# A track that fell back to the vibe palette checks its cover again this
# long into the song, in case the art arrived late.
COVER_RECHECK_SECONDS = (8.0, 25.0)
ART_SIZE = 48
MAX_ART_BYTES = 5_000_000
SPOTIFY_QUEUE_URL = "https://api.spotify.com/v1/me/player/queue"
# How far into a song the next one is looked up: late enough to skip songs
# that are skipped straight away, early enough to finish before it ends.
PREPARE_DELAY_SECONDS = 15.0
# The drop flash: the strip's background lights up in the song's colour
# under the running effect, then fades out, as (background brightness,
# seconds) steps. The effect keeps running, so nothing restarts.
FLASH_STEPS = ((1.0, 0.3), (0.6, 0.2), (0.35, 0.2), (0.15, 0.2))
NO_BACKGROUND = "#000000"
# Playlist preparation: ReccoBeats takes up to 40 IDs a call, and a short
# pause per song keeps Last.fm and the library well under their limits.
PREPARE_BATCH = 40
PREPARE_PAUSE_SECONDS = 0.2


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
    # Seconds between the player's title change and the song being audible.
    audio_delay: float = 0.0
    lastfm_key: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Decision:
    title: str
    artist: str
    album: str
    info: TrackInfo
    # The cover or vibe palette, before any pin or section nudge.
    natural: Palette
    # natural, or the album's pin.
    auto_palette: Palette
    # auto_palette, or the manual colour.
    palette: Palette
    family: str
    # natural_effect, or the album's pinned effect.
    auto_effect: str
    # auto_effect, or the dashboard's manual effect.
    effect: str
    config: dict[str, Any]
    # The track's intensity, 0 (calm) to 1 (intense), whatever the colour.
    level: float = 0.5
    # The effect chosen from the genre family (or a configured override).
    natural_effect: str = ""

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
    return track_key(artist, title, album)


def _playlist_track(track: Any) -> QueuedTrack:
    """A Music Assistant playlist track as the director looks it up."""
    album = getattr(track, "album", None)
    image = getattr(track, "image", None) or getattr(album, "image", None)
    spotify_id = next(
        (
            m.item_id
            for m in getattr(track, "provider_mappings", ())
            if m.provider_domain == "spotify"
        ),
        None,
    )
    return QueuedTrack(
        title=track.name,
        artist=", ".join(a.name for a in getattr(track, "artists", ()) or ()),
        album=getattr(album, "name", "") or "",
        spotify_id=spotify_id,
        duration=getattr(track, "duration", None) or None,
        art_url=getattr(image, "path", None)
        if image is not None and getattr(image, "remotely_accessible", False)
        else None,
    )


def _natural(entry: Entry) -> tuple[Palette, str, float]:
    """A cached track's automatic palette (its cover's, else genre and
    mood), effect family and intensity, before any pin."""
    info = entry.info
    features = info.features()
    family = classify(features)
    level = intensity(features, family)
    if entry.cover:
        return with_intensity(entry.cover, level), family, level
    palette = vibe_palette(
        info.genres, info.energy, info.valence, tempo_level(info.bpm), level
    )
    return palette, family, level


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
        self.tracks = TrackCacheStore(
            Store(hass, CACHE_STORAGE_VERSION, CACHE_STORAGE_KEY)
        )
        self.playlists = PlaylistStore(
            Store(hass, CACHE_STORAGE_VERSION, PLAYLIST_STORAGE_KEY)
        )
        # The configured overrides that set an effect (colours come from pins).
        self._effect_overrides = tuple(o for o in config.overrides if o.effect)
        self._prepare_task: asyncio.Task[None] | None = None
        self._prepare_run: asyncio.Task[None] | None = None
        # The next song, looked up ahead ("Artist - Title"), for the dashboard.
        self.next_ready: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._recheck_task: asyncio.Task[None] | None = None
        self._track_key: str | None = None
        self._last_effect: str | None = None
        self._decision: Decision | None = None
        # The decision LedFx last accepted, so updates PUT in place only
        # while that effect is running and skip LedFx when nothing changed.
        # Every strip request holds the lock, so they land in order.
        self._sent: Decision | None = None
        self._strip_lock = asyncio.Lock()
        self._transition_set = False
        self._spotify_refreshed = 0.0
        # Why the last track had no Spotify ID, for the dashboard.
        self.spotify_status = "ok"
        # ok, unreachable or error: LedFx's API and audio feed, for the
        # Music Mode device alert.
        self.ledfx_status = "ok"
        # Chosen colour (Music Mode Colour light on), or None for Auto.
        self.manual_rgb: tuple[int, int, int] | None = None
        self.brightness_pct = DEFAULT_BRIGHTNESS
        self.pins = PinStore(Store(hass, STORAGE_VERSION, STORAGE_KEY))
        # Live song sections from LedFx's audio (sections.py).
        self.sections = SectionTracker()
        # Drops in the live audio flash the strip (Music Mode Drop Flash).
        self.drops = DropDetector()
        self.drop_flash = True
        self._flash_task: asyncio.Task[None] | None = None
        self.section = NORMAL
        self._audio_task: asyncio.Task[None] | None = None
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
            self._start_audio()

    def _start_audio(self) -> None:
        if self._audio_task is None or self._audio_task.done():
            self._audio_task = self.hass.async_create_background_task(
                self._listen_audio(), "music_director audio sections"
            )

    def _stop_audio(self) -> None:
        if self._audio_task is not None and not self._audio_task.done():
            self._audio_task.cancel()
        self._audio_task = None

    async def _listen_audio(self) -> None:
        """Follow LedFx's live melbank loudness and turn sustained changes
        into song sections. Reconnects while Music Mode is on."""
        url = self.config.ledfx_url.replace("http", "ws", 1) + "/api/websocket"
        while True:
            try:
                async with self._session.ws_connect(url, heartbeat=30) as ws:
                    self._set_ledfx_status("ok")
                    await ws.send_json(
                        {
                            "id": 1,
                            "type": "subscribe_event",
                            "event_type": "graph_update",
                        }
                    )
                    graph = None
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        data = msg.json(loads=json_loads)
                        if data.get("event_type") != "graph_update":
                            continue
                        graph = graph or data.get("graph_id")
                        if data.get("graph_id") != graph:
                            continue
                        level = sum(float(v) for v in data.get("melbank") or ())
                        now = time.monotonic()
                        changed = self.sections.add(level, now)
                        if changed is not None:
                            self._set_section(changed)
                        if self.drops.add(level, now):
                            self._flash()
                # LedFx closed the feed (it restarts this way).
                self._set_ledfx_status("unreachable")
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - keep listening
                _LOGGER.debug("LedFx audio feed: %s", type(error).__name__)
                self._set_ledfx_status("unreachable")
            await asyncio.sleep(10)

    @callback
    def set_drop_flash(self, enabled: bool) -> None:
        self.drop_flash = enabled
        async_dispatcher_send(self.hass, CONTROLS_UPDATED)

    @callback
    def _flash(self) -> None:
        decision = self._decision
        if (
            not self.drop_flash
            or decision is None
            or not decision.title
            or not self._is_on(self.config.enabled)
            or not self._is_on(self.config.strip)
            or not self._running(decision)
            or (self._flash_task is not None and not self._flash_task.done())
        ):
            return
        _LOGGER.debug("Drop: flashing the strip")
        self._flash_task = self.hass.async_create_task(self._run_flash(decision))

    async def _run_flash(self, start: Decision) -> None:
        """Light the strip's background in the song's colour and fade it
        out. Strip updates wait meanwhile; the latest decision is sent at the
        end."""
        colour = to_hex(solid_colour(start.palette))
        try:
            for share, seconds in FLASH_STEPS:
                async with self._strip_lock:
                    decision = self._decision
                    # Stop when the song or effect changed or the strip was
                    # cleared: a PUT would land on the wrong effect.
                    if (
                        decision is None
                        or decision.title != start.title
                        or not self._running(decision)
                    ):
                        break
                    await self._ledfx(
                        "PUT",
                        {
                            "type": decision.effect,
                            "config": {
                                **decision.config,
                                "brightness": 1.0,
                                "background_color": colour,
                                "background_brightness": share,
                            },
                        },
                    )
                await asyncio.sleep(seconds)
        finally:
            self._flash_task = None
            if self._is_on(self.config.enabled) and self._is_on(self.config.strip):
                async with self._strip_lock:
                    decision = self._decision
                    if decision is not None and self._running(decision):
                        # Clear the background, with any change made meanwhile.
                        if await self._ledfx(
                            "PUT",
                            {
                                "type": decision.effect,
                                "config": {
                                    **decision.config,
                                    "background_color": NO_BACKGROUND,
                                },
                            },
                        ):
                            self._sent = decision
                    elif decision is not None:
                        await self._send(decision)

    @callback
    def _set_ledfx_status(self, status: str) -> None:
        if status == self.ledfx_status:
            return
        was_reachable = self.ledfx_status != "unreachable"
        self.ledfx_status = status
        if status == "ok":
            _LOGGER.info("LedFx is back")
        else:
            _LOGGER.warning("LedFx is %s", status)
        if self._decision is None or not self._is_on(self.config.enabled):
            return
        if status == "ok" and not was_reachable:
            # A restarted LedFx runs no effect: start it again.
            self._sent = None
            self._reapply()
        else:
            self._publish(self._decision)

    @callback
    def _set_section(self, section: str) -> None:
        self.section = section
        _LOGGER.debug("Song section: %s", section)
        self._reapply()

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
                self._start_audio()
            else:
                self._stop_audio()
                self._cancel()
                self._cancel_prepare()
                self._cancel_recheck()
                if self._flash_task is not None:
                    self._flash_task.cancel()
                self.hass.async_create_task(self._stop())
            return
        if not self._is_on(self.config.enabled):
            return
        if entity_id == self.config.effect_select:
            self._reapply()
            return
        if entity_id == self.config.strip:
            if new_state is not None and new_state.state == STATE_ON:
                self.hass.async_create_task(self._strip_on())
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

    def current_pinned_effect(self) -> str | None:
        """The current track's pinned effect, or None."""
        track = self.current_track
        if track is None:
            return None
        pin = self.pins.pins.find(track.title, track.artist, track.album)
        return pin.effect if pin and pin.effect in EFFECTS else None

    @callback
    def set_effect_pin(self, effect: str | None) -> None:
        """Pin an effect to the current track's album, or None to unpin it so
        the album goes back to the automatic effect."""
        track = self.current_track
        if track is None:
            return
        self.pin_song(track.title, track.artist, track.album, effect=effect)

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
        self.pin_song(
            track.title,
            track.artist,
            track.album,
            colour=None if rgb is None else to_hex(rgb),
        )

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

    def _cancel_recheck(self) -> None:
        if self._recheck_task is not None and not self._recheck_task.done():
            self._recheck_task.cancel()
        self._recheck_task = None

    def _cancel_prepare(self) -> None:
        if self._prepare_task is not None and not self._prepare_task.done():
            self._prepare_task.cancel()
        self._prepare_task = None

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
        natural_effect: str,
        level: float,
    ) -> Decision:
        # Sections nudge the automatic colours' vividness; chosen colours
        # (pins, the manual colour) stay as picked. The Decision keeps the
        # un-nudged palette, so recomposing never stacks the nudge.
        sectioned = with_intensity(
            natural, natural.intensity + SECTION_INTENSITY[self.section]
        )
        pin = self.pins.pins.find(title, artist, album) if title else None
        pinned = named_palette(pin.color, "override") if pin and pin.color else None
        auto_palette = pinned or sectioned
        palette = self._colour_override() or auto_palette
        auto_effect = (
            pin.effect if pin and pin.effect in EFFECTS else None
        ) or natural_effect
        effect = self._override() or auto_effect
        config = {
            **effect_config(
                effect,
                palette,
                info.features(),
                self.strip_solid,
                SECTION_DRIVE[self.section],
            ),
            "brightness": round(
                self.brightness_pct / 100 * SECTION_BRIGHTNESS[self.section], 2
            ),
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
            natural_effect,
        )

    def _recompose(self, decision: Decision) -> Decision:
        return self._compose(
            decision.title,
            decision.artist,
            decision.album,
            decision.info,
            decision.natural,
            decision.family,
            decision.natural_effect,
            decision.level,
        )

    async def _apply(self, decision: Decision) -> None:
        if not self._is_on(self.config.enabled):
            # A late cover check or flash must not bring the sensor or the
            # strip back after Music Mode ended.
            return
        self._decision = decision
        self._publish(decision)
        # The pin light follows the playing track.
        async_dispatcher_send(self.hass, CONTROLS_UPDATED)
        if self._flash_task is not None:
            # The flash sends the latest decision when it ends.
            return
        if not self._is_on(self.config.strip):
            return
        # One strip update at a time, newest last: slider drags and the
        # colour light's brightness-then-colour calls overlap otherwise, and
        # an older request landing last left the strip on stale settings.
        async with self._strip_lock:
            if self._decision is not decision:
                # A newer decision is queued behind this one.
                return
            await self._send(decision)

    def _running(self, decision: Decision) -> bool:
        """Whether LedFx runs decision's effect for its track."""
        sent = self._sent
        return (
            sent is not None
            and sent.effect == decision.effect
            and sent.title == decision.title
        )

    async def _send(self, decision: Decision) -> None:
        """Bring the strip to decision. Call with the strip lock held."""
        sent = self._sent
        if sent is not None and self._running(decision):
            if sent.config == decision.config:
                # A pin, the dropdown or a control set to what it already was.
                return
            # Same effect, new colours or brightness: update in place
            # instead of restarting the effect.
            if await self._ledfx(
                "PUT", {"type": decision.effect, "config": decision.config}
            ):
                self._sent = decision
                return
            if self.ledfx_status != "error":
                return
            # LedFx answered but runs no such effect (it restarted): start it.
        started = await self._ledfx(
            "POST", {"type": decision.effect, "config": decision.config}
        )
        self._sent = decision if started else None

    async def _strip_on(self) -> None:
        async with self._strip_lock:
            decision = self._decision
            if (
                decision is not None
                and self._flash_task is None
                and self._is_on(self.config.enabled)
                and self._is_on(self.config.strip)
            ):
                await self._send(decision)

    async def _run(self, force: bool) -> None:
        # The player's title changes when the next song starts decoding; the
        # soundbar plays it about audio_delay seconds later.
        observed = time.monotonic()
        new_track = False
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
            new_track = key != self._track_key
            if new_track:
                # Restart the trackers now, so the old song's audio cannot
                # move the section or flash while this one is looked up.
                self.sections.reset(observed)
                self.drops.reset(observed)
                self.section = NORMAL
                self.next_ready = None
            self._track_key = key
            assert player is not None
            decision = await self._decide(player, key)
        if new_track:
            # Hold the new song's look until it is audible (a newer track or
            # Music Mode turning off cancels this task), then start its
            # loudness baseline from there.
            hold = self.config.audio_delay - (time.monotonic() - observed)
            if hold > 0:
                await asyncio.sleep(hold)
            self.sections.reset(time.monotonic())
            self.drops.reset(time.monotonic())
        # Repeat avoidance follows the automatic choice, not the override.
        self._last_effect = decision.natural_effect
        await self._apply(decision)
        if not self._transition_set:
            # After the first look is out: the crossfade only matters from
            # the next effect on, and LedFx being down must not delay it.
            self._transition_set = await self._ensure_transition()
        if key is not None and decision.natural.source != "cover":
            self._cancel_recheck()
            self._recheck_task = self.hass.async_create_task(self._recheck_cover(key))
        if key is not None and new_track:
            self._cancel_prepare()
            self._prepare_task = self.hass.async_create_background_task(
                self._prepare_next(key), "music_director next track"
            )

    async def _recheck_cover(self, key: str) -> None:
        """Use the cover after all if its art arrives while the track plays."""
        for delay in COVER_RECHECK_SECONDS:
            await asyncio.sleep(delay)
            decision = self._decision
            if (
                self._track_key != key
                or decision is None
                or decision.natural.source == "cover"
            ):
                return
            cover = await self._cover_for(key)
            if cover is None:
                continue
            if self._track_key != key:
                return
            self.tracks.put(key, Entry(decision.info, cover))
            _LOGGER.info("Late cover colour for %s", key.replace("\n", " - "))
            await self._apply(
                self._compose(
                    decision.title,
                    decision.artist,
                    decision.album,
                    decision.info,
                    with_intensity(cover, decision.level),
                    decision.family,
                    decision.natural_effect,
                    decision.level,
                )
            )
            return

    async def _prepare_next(self, key: str) -> None:
        """Look up the next song in Spotify's queue while this one plays,
        so its info and cover colours are cached when it starts."""
        await asyncio.sleep(PREPARE_DELAY_SECONDS)
        if self._track_key != key or not self._is_on(self.config.enabled):
            return
        artist, title, _ = key.split("\n", 2)
        queued = next_in_queue(await self._spotify_queue(), title, artist)
        if queued is None:
            return
        next_key = track_key(queued.artist, queued.title, queued.album)
        label = f"{queued.artist} - {queued.title}"
        entry = self.tracks.cache.find(next_key)
        if entry is None or (entry.cover is None and not entry.checked):
            info, (cover, checked) = await asyncio.gather(
                self._gather_info(
                    queued.title, queued.artist, queued.duration, queued.spotify_id
                ),
                self._queued_cover(queued),
            )
            if self._track_key != key:
                return
            self.tracks.put(next_key, Entry(info, cover, checked))
            _LOGGER.info("Prepared next track %s", label)
        self.next_ready = label
        if self._decision is not None:
            self._publish(self._decision)

    async def prepare_playlists(
        self, names: list[str] | None, notify: bool = True
    ) -> None:
        """Look up every song in the library's Spotify playlists (or the
        named ones) through Music Assistant, so they play from the cache.
        Songs already cached are skipped. One run at a time."""
        if self._prepare_run is not None and not self._prepare_run.done():
            _LOGGER.info("Playlist preparation is already running")
            return
        self._prepare_run = self.hass.async_create_background_task(
            self._prepare_playlists(names, notify), "music_director prepare playlists"
        )

    async def _prepare_playlists(self, names: list[str] | None, notify: bool) -> None:
        mass = self._music_assistant()
        if mass is None:
            self._notify(
                notify, "Music Assistant is not set up: no playlists to prepare."
            )
            return
        wanted = {normalize(name) for name in names or ()}
        tracks: dict[str, QueuedTrack] = {}
        membership: dict[str, list[PlaylistSong]] = {}
        for playlist in await mass.music.get_library_playlists(limit=1000):
            if not any(
                m.provider_domain == "spotify" for m in playlist.provider_mappings
            ):
                continue
            if wanted and normalize(playlist.name) not in wanted:
                continue
            songs = membership.setdefault(playlist.name, [])
            for track in await mass.music.get_playlist_tracks(
                playlist.item_id, playlist.provider
            ):
                queued = _playlist_track(track)
                key = track_key(queued.artist, queued.title, queued.album)
                songs.append(PlaylistSong(key, queued.art_url))
                tracks.setdefault(key, queued)
        # The colour review panel lists songs by playlist.
        await self.playlists.async_replace(membership, partial=bool(wanted))
        chosen = list(membership)
        todo = []
        for key, queued in tracks.items():
            entry = self.tracks.cache.find(key)
            if entry is None or (entry.cover is None and not entry.checked):
                todo.append((key, queued))
        self._notify(
            notify,
            f"Preparing {len(todo)} of {len(tracks)} songs from "
            f"{len(chosen)} playlists ({len(tracks) - len(todo)} already cached).",
        )
        artist_tags: dict[str, Any] = {}
        done = 0
        for start in range(0, len(todo), PREPARE_BATCH):
            batch = todo[start : start + PREPARE_BATCH]
            ids = [q.spotify_id for _, q in batch if q.spotify_id]
            recco = reccobeats_by_id(
                await self._get_json(
                    RECCOBEATS_URL, {"ids": ",".join(ids)}, "ReccoBeats"
                )
                if ids
                else None
            )
            for key, queued in batch:
                info, (cover, checked) = await asyncio.gather(
                    self._gather_info(
                        queued.title,
                        queued.artist,
                        queued.duration,
                        queued.spotify_id,
                        recco=recco.get(queued.spotify_id or "", {}),
                        artist_tags=artist_tags,
                    ),
                    self._art_palette(queued.art_url),
                )
                self.tracks.put(key, Entry(info, cover, checked))
                done += 1
                await asyncio.sleep(PREPARE_PAUSE_SECONDS)
            _LOGGER.info("Prepared %d of %d playlist songs", done, len(todo))
            # Keep the progress if Home Assistant restarts mid-run.
            await self.tracks.async_save()
        self._notify(notify, f"Prepared {done} songs from {len(chosen)} playlists.")

    def playlist_names(self) -> list[dict[str, Any]]:
        return [
            {"name": name, "songs": len(songs)}
            for name, songs in sorted(
                self.playlists.playlists.items(), key=lambda item: item[0].lower()
            )
        ]

    def playlist_colours(self, name: str) -> list[dict[str, Any]] | None:
        """What the lights would show for each song in a prepared playlist:
        the strip gradient, the area colours and where the colour comes from."""
        songs = self.playlists.playlists.get(name)
        if songs is None:
            return None
        rows = []
        for song in songs:
            artist, title, album = song.key.split("\n", 2)
            row: dict[str, Any] = {
                "key": song.key,
                "title": title,
                "artist": artist,
                "album": album,
                "art": song.art,
            }
            entry = self.tracks.cache.find(song.key)
            pin = self.pins.pins.find(title, artist, album)
            if entry is None and not (pin and (pin.color or pin.effect)):
                row["source"] = "pending"
                rows.append(row)
                continue
            if entry is not None:
                natural, family, level = _natural(entry)
                row["family"] = family
                row["intensity"] = round(level, 2)
                row["genres"] = list(entry.info.genres[:3])
                configured = find_override(self._effect_overrides, title, artist, album)
                row["auto_effect"] = (
                    configured.effect
                    if configured and configured.effect
                    else choose_effect(family, song.key, None)
                )
            else:
                natural = vibe_palette()
            pinned = named_palette(pin.color, "override") if pin and pin.color else None
            palette = pinned or natural
            row["source"] = (
                "pinned" if pinned else "cover" if natural.source == "cover" else "vibe"
            )
            row["pin"] = pin.color if pinned and pin else None
            row["effect_pin"] = pin.effect if pin and pin.effect in EFFECTS else None
            row["auto"] = to_hex(solid_colour(natural))
            row["colour"] = to_hex(solid_colour(palette))
            row["gradient"] = gradient(palette)
            row["areas"] = [
                to_hex(rgb) for rgb in area_colors(palette, len(self.config.areas))
            ]
            rows.append(row)
        return rows

    @callback
    def pin_song(
        self,
        title: str,
        artist: str,
        album: str,
        *,
        colour: str | None | Keep = KEEP,
        effect: str | None | Keep = KEEP,
    ) -> None:
        """Pin a colour and/or effect to a song's album (or the song, without
        an album); None unpins that one, KEEP leaves it."""
        self.pins.pins.update(title, artist, album, color=colour, effect=effect)
        self.pins.save()
        async_dispatcher_send(self.hass, CONTROLS_UPDATED)
        self._reapply()

    def _music_assistant(self) -> Any:
        for entry in self.hass.config_entries.async_entries("music_assistant"):
            data = getattr(entry, "runtime_data", None)
            if data is not None and getattr(data, "mass", None) is not None:
                return data.mass
        return None

    def _notify(self, notify: bool, message: str) -> None:
        _LOGGER.info(message)
        if not notify:
            return
        self.hass.async_create_task(
            self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Music Director",
                    "message": message,
                    "notification_id": "music_director_prepare",
                },
            )
        )

    async def _queued_cover(self, queued: QueuedTrack) -> tuple[Palette | None, bool]:
        return await self._art_palette(queued.art_url)

    async def _art_palette(self, url: str | None) -> tuple[Palette | None, bool]:
        """The cover palette at url, and whether the art was decoded (so a
        None palette means the cover has no usable colour)."""
        if not url:
            return None, False
        data = await self._download(url)
        if data is None:
            return None, False
        try:
            return await self.hass.async_add_executor_job(_cover_palette, data), True
        except (OSError, ValueError) as error:
            _LOGGER.debug("Cover unreadable: %s", type(error).__name__)
            return None, False

    async def _spotify_queue(self) -> Any:
        """Spotify's queue for the account behind the Spotify player, with
        the Spotify integration's own sign-in. None when unavailable."""
        entity = er.async_get(self.hass).async_get(self.config.spotify)
        entry = (
            self.hass.config_entries.async_get_entry(entity.config_entry_id)
            if entity is not None and entity.config_entry_id
            else None
        )
        if entry is None:
            return None
        try:
            implementation = (
                await config_entry_oauth2_flow.async_get_config_entry_implementation(
                    self.hass, entry
                )
            )
            session = config_entry_oauth2_flow.OAuth2Session(
                self.hass, entry, implementation
            )
            await session.async_ensure_token_valid()
            token = session.token["access_token"]
        except Exception as error:  # noqa: BLE001 - lookahead is best effort
            _LOGGER.debug("Spotify sign-in unavailable: %s", type(error).__name__)
            return None
        try:
            async with self._session.get(
                SPOTIFY_QUEUE_URL,
                headers={"Authorization": f"Bearer {token}"},
                timeout=HTTP_TIMEOUT,
            ) as response:
                if response.status != 200:
                    _LOGGER.debug("Spotify queue returned HTTP %s", response.status)
                    return None
                return await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            _LOGGER.debug("Spotify queue failed: %s", type(error).__name__)
            return None

    def _idle_decision(self) -> Decision:
        idle = vibe_palette()
        return self._compose(
            "", "", "", TrackInfo(), idle, "default", "energy", idle.intensity
        )

    async def _decide(self, player: State, key: str) -> Decision:
        title = player.attributes.get("media_title") or ""
        artist = player.attributes.get("media_artist") or ""
        album = player.attributes.get("media_album_name") or ""
        entry = self.tracks.cache.find(key)
        if entry is None:
            info, cover = await asyncio.gather(
                self._player_info(player), self._cover_for(key)
            )
            entry = Entry(info, cover)
            self.tracks.put(key, entry)
        elif entry.cover is None and not entry.checked:
            # A missed cover is not remembered: try again on replay.
            entry = Entry(entry.info, await self._cover_for(key))
            self.tracks.put(key, entry)
        else:
            _LOGGER.debug("Cached: %s", key.replace("\n", " - "))
            if key not in self.tracks.cache.entries:
                # Found under another spelling: remember this one too.
                self.tracks.put(key, entry)
        info = entry.info
        palette, family, level = _natural(entry)
        # Colours come from pins (seeded from configured overrides); the
        # configured overrides still set effects.
        song = find_override(self._effect_overrides, title, artist, album)
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

    async def _player_info(self, player: State) -> TrackInfo:
        title = player.attributes.get("media_title") or ""
        artist = player.attributes.get("media_artist") or ""
        return await self._gather_info(
            title,
            artist,
            player.attributes.get("media_duration"),
            await self._spotify_id(title, artist),
        )

    async def _gather_info(
        self,
        title: str,
        artist: str,
        duration: Any,
        spotify_id: str | None,
        recco: Any = None,
        artist_tags: dict[str, Any] | None = None,
    ) -> TrackInfo:
        """Look the track up. recco is an already fetched ReccoBeats body,
        and artist_tags remembers Last.fm artist tags, for batch runs."""
        info = TrackInfo()
        if recco is not None:
            apply_reccobeats(info, recco)
        elif spotify_id:
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
                if method == "artist.gettoptags" and artist_tags is not None:
                    if artist not in artist_tags:
                        artist_tags[artist] = await self._lastfm(method, artist, extra)
                    body = artist_tags[artist]
                else:
                    body = await self._lastfm(method, artist, extra)
                apply_lastfm(info, body, artist)
                if info.genres:
                    break
        return info

    async def _lastfm(self, method: str, artist: str, extra: dict[str, str]) -> Any:
        assert self.config.lastfm_key
        return await self._get_json(
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
        return await self._download(picture)

    async def _download(self, picture: str) -> bytes | None:
        try:
            async with self._session.get(picture, timeout=HTTP_TIMEOUT) as response:
                if response.status != 200:
                    return None
                # content.read(n) returns only what has arrived so far, which
                # truncated larger covers: read every chunk, up to the limit.
                data = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    data += chunk
                    if len(data) > MAX_ART_BYTES:
                        _LOGGER.debug("Cover art over %d bytes", MAX_ART_BYTES)
                        return None
                return bytes(data)
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
                "effect_mode": "manual"
                if decision.manual
                else "pinned"
                if decision.auto_effect != decision.natural_effect
                else "auto",
                "effect_config": {
                    k: v for k, v in decision.config.items() if k != "gradient"
                },
                "palette_source": decision.palette.source,
                "palette_spread": spread_label(decision.palette),
                "colour_mode": "manual" if self.manual_rgb else "auto",
                "album": decision.album,
                "pinned_colour": self.current_pin(),
                "pinned_effect": self.current_pinned_effect(),
                "natural_palette_source": decision.natural.source,
                "spotify_status": self.spotify_status,
                "ledfx_status": self.ledfx_status,
                "section": self.section,
                "brightness_pct": self.brightness_pct,
                "area_brightness_pct": bulb_brightness_pct(
                    self.brightness_pct, self.section
                ),
                "auto_palette_source": decision.auto_palette.source,
                "intensity": round(decision.level, 3),
                "next_ready": self.next_ready,
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

    async def _ledfx(self, method: str, payload: dict[str, Any] | None = None) -> bool:
        """Call the strip virtual's effects API. True when LedFx accepted it."""
        url = (
            f"{self.config.ledfx_url}/api/virtuals/{self.config.ledfx_virtual}/effects"
        )
        try:
            async with self._session.request(
                method, url, json=payload, timeout=LEDFX_TIMEOUT
            ) as response:
                if response.status >= 400:
                    _LOGGER.warning(
                        "LedFx %s returned HTTP %s: %s",
                        method,
                        response.status,
                        (await response.text())[:200],
                    )
                    self._set_ledfx_status("error")
                    return False
                self._set_ledfx_status("ok")
                return True
        except (aiohttp.ClientError, TimeoutError) as error:
            _LOGGER.warning("LedFx %s failed: %s", method, error)
            self._set_ledfx_status("unreachable")
            return False

    async def _ensure_transition(self) -> bool:
        """Set the strip's crossfade between effects. LedFx keeps virtual
        config on POST /api/virtuals with an id, so send the full config back
        with only transition_time changed. Returns True once it is in place."""
        base = f"{self.config.ledfx_url}/api/virtuals"
        virtual = self.config.ledfx_virtual
        try:
            async with self._session.get(
                f"{base}/{virtual}", timeout=LEDFX_TIMEOUT
            ) as response:
                body = await response.json(content_type=None)
            config = dict(body[virtual]["config"])
            if config.get("transition_time") == self.config.transition_time:
                return True
            config["transition_time"] = self.config.transition_time
            async with self._session.post(
                base, json={"id": virtual, "config": config}, timeout=LEDFX_TIMEOUT
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

    async def _clear_strip(self) -> None:
        async with self._strip_lock:
            self._sent = None
            await self._ledfx("DELETE")

    async def _stop(self) -> None:
        self._track_key = None
        self._decision = None
        self.hass.states.async_set(
            SENSOR, "off", {"friendly_name": "Music Director", "icon": "mdi:palette"}
        )
        await self._clear_strip()
