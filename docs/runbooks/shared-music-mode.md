# Shared music mode

Music Mode (`input_boolean.shared_music_mode`) lights the shared spaces from
the track playing on the soundbar
(`media_player.homelab_05_speaker`). Once per track it
picks a palette from the album art and a LedFx effect from the song's genre,
tempo and energy.

## Participants

Each area has its own toggle next to Music Mode on the Shared Spaces
dashboard. The toggles keep their last values between sessions.

| Toggle | Lights | While taking part |
| --- | --- | --- |
| `input_boolean.music_mode_led_strip` | living room WLED strip | LedFx effect and colours |
| `input_boolean.music_mode_kitchen_ceiling` | kitchen ceiling 1, 2 | one shade |
| `input_boolean.music_mode_dining_table` | dining table 1, 2 | one shade |
| `input_boolean.music_mode_floor_lamp` | floor lamp | one shade |
| `input_boolean.music_mode_tulip_lamp` | tulip lamp | one shade |
| `input_boolean.music_mode_ceiling_strip` | Apollo ceiling strip | one shade |

The mushroom lamp (on/off only) has no toggle: Music Mode always switches it
off on entry (snapshot `scene.pre_music_mushroom_lamp`) and restores it on
exit. Switching it on by hand during Music Mode sticks.

While Music Mode is on, only selected lights are lit. Starting it snapshots
every area to `scene.pre_music_<area>` and the WLED strip
(`light.living_room_led_strip`) to `scene.pre_music_led_strip`, and locks
Adaptive Lighting for every area's lights. Selected areas are painted
(`script.shared_music_area` step `join`); the rest are switched off
(`standby`) with no fade, since the Zigbee bulbs fade to off slowly and
unreliably, and so is the strip when its toggle is off. Toggling an area or
the strip mid-session paints it or switches it off (`paint` / `off`) and
keeps the snapshot. Turning Music Mode off restores every area and the strip
and hands the areas back to Adaptive Lighting (`leave`). Bulbs change once
per track, and on colour or brightness changes, at
`number.music_mode_brightness` (default 70%), scaled by the song section
(see Song sections).

### Ending a session

Music Mode ends on its own after the soundbar has not been `playing` for
15 minutes (trigger `idle`). A template trigger that is already true when
Home Assistant starts does not fire, so after a restart the 15 minutes start
at the next soundbar or Music Mode change.

`script.shared_music_mode_end` turns Music Mode off and waits (up to a
minute) until `automation.lighting_shared_music_mode` has no runs left, so
the restore is finished. Everything that snapshots or switches the shared
lights runs it first: `script.shared_lights_off`, `script.shared_lights_on`
and `shared_spaces_empty_lights_off` (last person leaving, and a restart
with nobody home). Their snapshots therefore hold the lights as they were
before Music Mode, and the restore can never relight an empty house.

`sensor.music_director` changes attributes for many reasons (next song
ready, LedFx status). The automation's top-level condition lets a `palette`
trigger through only when `area_colors`, `brightness_pct` or
`area_brightness_pct` changed, so the rest never queue a run.

## Components

- `home-assistant/custom_components/music_director/`: watches the player,
  gathers metadata, decides the palette and effect, sets the strip through
  `POST /api/virtuals/living-room-led-strip/effects` and publishes
  `sensor.music_director`. Configured under `music_director:` in
  `home-assistant/core/configuration.yaml`. Service `music_director.refresh`
  re-decides the current track.
- `home-assistant/automations/shared_music_mode.yaml` and
  `home-assistant/scripts/shared_music_area.yaml`: area join, leave and
  paint from `sensor.music_director` attribute `area_colors`.
- `navidrome-lookup` sidecar (`gitops/media/music.yaml`, source
  `scripts/navidrome_lookup.py`, copied byte for byte to
  `gitops/media/navidrome-lookup/lookup.py` for the ConfigMap generator;
  `tests/test_navidrome_lookup.py` keeps them equal): read-only lookup into the Navidrome
  database at `http://navidrome-lookup.media.svc.cluster.local:4534/lookup`.
- `gitops/secrets/lastfm-api.sops.yaml`: Last.fm API key, exposed to Home
  Assistant as `LASTFM_API_KEY`.

## Metadata order

1. Spotify track ID from `media_player.spotify_rupan` when its title matches
   the soundbar. The soundbar plays through Spotify Connect into Music
   Assistant and reports no track ID, so this is the only source. The Web
   API quota is shared with Home Assistant's own polling: the director asks
   for at most one refresh per track change, none within 30 s of the last
   (`SPOTIFY_REFRESH_GAP`) and none while the entity is unavailable (Spotify
   rate limiting, `429 QUOTA_EXCEEDED`). `sensor.music_director` reports
   `spotify_status` (`ok`, `other track` or `unavailable`), and the preview
   card warns when Spotify is unavailable, since energy and tempo are then
   estimated from the library and Last.fm.
2. ReccoBeats `audio-features` by Spotify ID: tempo, energy, danceability,
   acousticness and ISRC. No key is needed.
3. Local library by ISRC, else by artist, cleaned title and duration: genres
   and BPM.
4. Last.fm top tags for the track, then the artist, when no genre is known.

Cover art comes from Spotify's media player when it reports the same
track (its art changes with the title), else from the speaker's player. When
no art can be downloaded the director retries twice, 2 s apart
(`COVER_ATTEMPTS`), and a track whose cover was missed is retried on replay
rather than cached; the log says `No cover art` or `No usable cover colour`. A
track that fell back to the vibe palette checks its cover again 8 s and
25 s in (`COVER_RECHECK_SECONDS`) and switches to it if the art arrived
late (log: `Late cover colour`).

### Track cache and next-song lookahead

Looked-up info and cover palettes for the last 10,000 tracks are kept in
`.storage/music_director.tracks` (`trackcache.py`), so replays skip every
lookup, also after a restart. The stored cache carries a hash of
`palette.py` and `metadata.py`; a deploy that changes either starts from an
empty cache, so colour changes apply to every song straight away. To clear
it by hand, stop Home Assistant and delete that file.

15 s into each song (`PREPARE_DELAY_SECONDS`) the director reads Spotify's
queue (`GET /v1/me/player/queue`, one call per song, signed in through the
Spotify integration's own config entry) and looks up the next song: info by
its Spotify ID and the cover from Spotify's 300 px art. When it plays, the
director finds it in the cache and only updates LedFx. The queue is ignored
unless it belongs to the playing song, and a skip or shuffle that plays a
different song falls back to the normal lookup. `sensor.music_director`
reports `next_ready` ("Artist - Title"), shown on the preview card; the log
says `Prepared next track`.

`music_director.prepare_playlists` fills the cache from every Spotify
playlist in the Music Assistant library (about 7,400 songs in 2026-11), or
only the playlists named in `playlists`. It reads the playlists through
Music Assistant, whose Spotify sign-in is separate from the Spotify
integration's rate-limited Web API quota, takes art straight from Spotify's
image CDN, and batches ReccoBeats 40 songs a call. Songs already cached are
skipped, so reruns only do new songs. It runs in the background, logs
`Prepared N of M playlist songs` per batch and shows a notification at the
start and end (`notify: false` turns that off). The automation
`music_director_prepare_playlists` runs it quietly at 04:30 each night,
which also refills the cache after a colour change emptied it. A full run
takes roughly 2.5 hours, mostly Last.fm genre lookups for songs missing from
the local library. A cover that downloads and decodes but has no usable
colour is stored as checked and not looked up again; only failed downloads
are retried.

### Music Colours panel

The **Music Colours** sidebar panel (`panel.py`, `panel.js`, served by the
integration at `/music_director_static/panel.js`) reviews prepared
playlists. Each run of `prepare_playlists` stores playlist membership in
`.storage/music_director.playlists`. For each song the panel shows the LED
strip gradient, the room light colours, the source (cover, genre and mood,
pinned, or not prepared yet), genres and intensity, with filters and
search. The colour picker pins that colour to the song's album, the same pin
the dashboard's Music Mode Pin sets; **Unpin colour** returns the album to
automatic colours. The effect menu shows the automatic effect as "Auto
(Melt)" and pins another effect to the album; choosing Auto unpins it. The panel uses the websocket commands
`music_director/playlists`, `music_director/playlist` and
`music_director/pin`.

The cover palette merges colour clusters within 20° of hue (so a cover-wide
tint such as Dawn FM's muted blue counts as one colour), then takes the most
vivid well-represented group. Skin tones, browns and olives (`palette.muddy`)
are judged per cluster, never merge with clean colours (so Whole Lotta Red's
dark drips cannot turn its red lettering brown), and count for 0.15 of their
score, so portrait covers do not turn the room amber. Light, soft coral
groups (hue 15-45°, lightness ≥ 0.68, chroma < 0.16; `palette.pastel_pink`)
read as pink on a cover but saturate to red-orange, so they take a hot pink
hue instead (Funk Wav Bounces).

How far the lights spread in hue follows the cover (`extract_palette`,
`shade_hues`). Groups holding at least 10% of the cover's clean colour count:

- One-colour cover (the primary holds 75% or more, or nothing else counts):
  every light takes that hue (channel ORANGE, Dawn FM, Whole Lotta Red).
- Related colours (every other group within 60° of the primary): the lights
  alternate between the cover's own hues.
- Unrelated colours, and the vibe fallback: ±10° around the primary.

Intensity narrows any spread toward the primary, so intense tracks show one
sharp colour on every light and across the strip gradient. Lights sharing a
hue still differ slightly in brightness. When no group scores at least
`MIN_COVER_SCORE`, the palette comes from the song's vibe
(`palette.vibe_palette`): valence picks a point on a deep blue (sad) to
purple to deep red (happy) axis, the genre pulls it 30% toward its own hue,
and energy (or tempo) raises saturation and brightness, after Palmer et
al. (PNAS 2013). For intense tracks the hue is then pulled toward
the nearer of deep red and deep blue (`palette.polarize`, by
`boost(intensity)`), so intense rap is red or blue rather than the magenta
between them.

Each track gets an intensity from 0 (calm) to 1 (intense)
(`rules.intensity`): the family prior (calm 0.15, smooth 0.3, default 0.5,
bass 0.75, rock 0.8, drive 0.85) of the genre, else of the chosen effect
family, blended with ReccoBeats energy, or with tempo when there is no
energy. Mellow rap that plays a smooth effect therefore still rates fairly
intense (Graduation's I Wonder: about 0.6). Intensity sets how vivid the output is
(`palette._led`):

- Intense tracks (rap, house, rock, high energy) are steered toward vivid reds
  and deep blues (`palette.steer`: oranges and yellows into reds, yellow-greens
  into green, sky blues into deep blue) and, at full intensity, the most
  saturated version of the colour the LEDs can show.
- Calm tracks (R&B, soul, acoustic) keep their cover's own hue, so an
  orange cover stays orange, saturated to at least `CALM_MIN_CHROMA` (0.17)
  because softer colours read as white or pastel on bulbs.
- At every intensity, yellows lean at least partly toward orange, blues and
  reds keep a lightness ceiling so they stay deep, and the yellow-green band
  keeps a lightness floor so nothing renders as mustard or olive.

In the vibe fallback the genre pulls the mood-axis hue toward its own colour
by 95% for calm tracks and 30% for intense ones, so calm R&B stays warm
orange (genre hue) and intense rap follows mood between deep blue and red.
`sensor.music_director` attributes `metadata_sources`, `palette_source`
(`cover`, `vibe`, `default`, `override` or `manual`), `auto_palette_source`
and `intensity` show what each decision used; `palette_spread`
(`palette.spread_label`) says how the lights spread. While Music Mode is on,
the dashboard's preview card under the toggle shows the strip gradient, each
area's colour, where the colour came from, its spread and the intensity.

## Song sections

While Music Mode is on, the director follows LedFx's live loudness (the
`graph_update` melbank feed on LedFx's websocket, about 60 per second) and
turns sustained changes into sections (`sections.py`): a 6 s average is
compared with the song's 40 s baseline; 1.3× or more is `loud`, 0.65× or
less is `quiet`, with hysteresis, a 20 s warm-up per track and at least 16 s
per section, so beats, fills and gaps between phrases do not flip it.

| Section | Strip brightness (× Music Mode Brightness) | Bulb brightness (×) | Colour intensity | Effect drive |
| --- | --- | --- | --- | --- |
| quiet | 0.8 | 0.8 | −0.1 | −0.15 |
| normal | 0.9 | 1.0 | ±0 | ±0 |
| loud | 1.0 | 1.15 (max 100%) | +0.15 | +0.25 |

Bulbs follow sections too. `sensor.music_director` publishes
`area_brightness_pct` (`sections.bulb_brightness_pct`), which bulbs take when
they join or are relit. On a section change the automation runs
`script.shared_music_area` step `section` with `scale` = new / old
`area_brightness_pct`: each lit bulb is recoloured and its own brightness
multiplied by that ratio over 2 s. A bulb dimmed by hand therefore stays
dimmer than the rest, and one switched off stays off. A new track returns to
normal, which scales back the same way.

The section intensity nudge applies only to the published palette. The
decision keeps the un-nudged cover or vibe palette, so recomposing (a section
change, a brightness or colour change) never stacks the nudge. Before
2026-11-20 it did, and long songs drifted to fully vivid or fully muted
colours. The section and drop trackers restart as soon as a new title shows,
so the previous song's audio cannot set the section while the new one is
looked up.

### Drop flash

`sections.DropDetector` watches the same audio feed for drops: a one second
average jumping 1.5× above the last few seconds and 1.25× above the song's
baseline, held for 0.6 s. None fire in the first 20 s of a song, within 12 s
of near-silence (music resuming after a pause is not a drop) or within 45 s
of the last one. On a drop the strip's background lights up in the song's
colour under the running effect and fades out over about a second
(`FLASH_STEPS`, LedFx `background_color` and `background_brightness` via
`PUT`, so the effect does not restart). Strip updates wait during the flash
and the latest decision is sent after it. Bulbs do not flash.
`switch.music_mode_drop_flash` ("Flash on drops") turns it off and keeps
that across restarts.

Effect drive shifts the 0–1 tempo and energy levels behind the effect's
speed and reactivity (`rules.dynamics`), so choruses and drops move faster
and react harder, and breakdowns calm down. A song with no known tempo or
energy starts from the middle when the section is not normal.

The strip's effect is updated in place (`PUT …/effects`), not restarted,
while the effect LedFx last accepted is the same; otherwise it is started
again (`POST`). Strip updates go out one at a time, and one superseded by a
newer decision is skipped, so the strip always ends on the latest settings.
Bulbs change at most once per section. Pins and the manual colour are not
changed. `sensor.music_director` reports `section`; the preview card shows
loud and quiet parts. Settings were tuned on live recordings on 2026-11-14:
hip-hop with gaps between phrases flipped every few seconds at shorter
averages.

## Effects

| Family | Chosen when | LedFx effects |
| --- | --- | --- |
| calm | ambient, classical, acoustic, folk, soundtrack; energy < 0.35 | melt, lava_lamp |
| smooth | R&B, soul, funk, jazz, reggae; hip-hop with energy < 0.5 | melt |
| bass | hip-hop, rap, trap, drill, reggaeton | power, energy |
| drive | electronic, house, dance; energetic danceable 110-140 BPM | scroll, bands, strobe |
| rock | rock, metal, punk | wavelength, bands |
| default | nothing known | energy |

Within a family the effect is a stable hash of the track, and consecutive
tracks avoid repeating the same effect. A manual change made in LedFx itself
holds until the next track.

Besides colours, the director sets tempo and energy dynamics inside each
LedFx schema range (`rules.dynamics`): speed scales from 70 BPM (slowest) to
160 BPM (fastest) for `melt`, `lava_lamp` and `scroll`; energy raises
`reactivity` (melt, lava_lamp), `sensitivity` (energy), `bass_decay_rate`
(power) and shortens `decay` (scroll). Unknown tempo or energy keeps the
LedFx default. On start it sets the strip virtual's `transition_time`
(crossfade between effects) to `music_director.transition_time` (1.5 s).

The player's title changes when Spotify starts decoding the next song, but the
soundbar plays it later (the Spotify Connect pipeline's buffering, see
`docs/runbooks/spotify-connect.md`). `music_director.audio_delay` (seconds,
default 0) holds a new song's colours, effect and loudness baseline for that
long after the title change, so the lights change with the music. A newer
track or Music Mode turning off cancels the hold. Re-measure and retune it
whenever the audio path changes.

## Manual colour and per-song overrides

`light.music_mode_colour` ("Colour" on the dashboard) is a virtual light
created by the integration (`light.py`); it drives no bulb. Off means Auto
(cover and vibe colours). Turning it on, or picking a colour on its hue
slider or colour wheel, applies that colour at once to the strip and every
taking-part area and holds it across tracks until it is switched off (the
"Auto colour" card does that). Chosen colours keep their hue
(`palette.chosen_palette`) and are never steered or narrowed by intensity.
The light restores its colour and on/off state after a restart.

`number.music_mode_brightness` ("Brightness", 10-100%, default 70%;
`number.py`) sets the brightness of the painted areas (`brightness_pct` on
`sensor.music_director`) and of the strip effect (LedFx `brightness`), in
Auto and manual colour alike. The colour light's brightness slider is the
same value. Areas take it when they join or are switched on, and moving it
relights every taking-part area (`relight`). New tracks and colour changes
only recolour lights that are on (`paint`), so a bulb's own brightness slider
holds across tracks and a bulb switched off by hand stays off until Music
Mode next starts.

`select.music_mode_strip_colours` ("LED Strip Colours", `select.py`) shows
the strip's colours as a Gradient (default), or as one Solid colour (`palette.solid_colour`) in every LedFx colour slot. It applies
at once, holds across tracks and restores after a restart;
`sensor.music_director` reports it as `strip_style`. Whites and pastels
look poor on the strip, so every strip colour (`palette._strip`) keeps at
least `STRIP_MIN_CHROMA` saturation and mid lightness (0.58-0.68), and the
gradient stays within `STRIP_HUE_SPREAD` (8°) of the primary: a slight shift,
not the bulbs' spread. The primary is steered once and the stops keep their
offsets, so a steering boundary cannot split the gradient.

Bulbs take a colour's hue and saturation and set brightness separately, so
a mid-lightness RGB colour shows paler on a bulb than on screen. Every light
and strip colour therefore goes out at full value with at least
`LED_MIN_SATURATION` (82%) saturation, rising to 100% for fully intense
tracks (`palette.led_native`); `area_colors` and the preview card show
exactly what is sent. Colours picked on the Colour or Pin wheels, and
`#RRGGBB` overrides, are sent exactly as picked.

WLED scales LedFx's DDP stream by its own brightness. When the strip takes
part, Music Mode switches `light.living_room_led_strip` on at full
brightness, so `number.music_mode_brightness` alone sets the strip's level;
the WLED snapshot restores its own brightness afterwards.

WLED (`10.0.20.181`, Gledopto) must apply gamma correction to realtime data
(Config → Sync Interfaces → Realtime, "Skip gamma correction" unticked;
`/json/cfg` `if.live.no-gc: false`, set 2026-11-14). Without it the weaker
channels of LedFx's colours show far too bright and the strip drifts toward
white. This device setting is not managed from the repo; after a WLED reset
or replacement, set it again and check with
`curl -s http://10.0.20.181/json/cfg` from a host on the IoT VLAN (the agent
VM reaches it through the Home Assistant pod).

Other WLED settings, also set live on 2026-11-14 and not managed from the
repo:

- One output: 103 WS2812B LEDs on GPIO 16 (WLED segment 0-103). A phantom
  second output (30 LEDs on GPIO 2, nothing attached) took 902 mA of the
  limit until it was removed on 2026-11-14.
- Power limiter 4000 mA (55 mA per LED), all on that output, with a 5 V 5 A
  supply fed from one end: keep it there; above ~4.5 A the first connector
  and strip copper carry too much without power injection.
- LedFx device `living-room-led-strip` has 103 pixels; its virtual uses two
  segments, 0-50 and 51-102, as a 52-pixel virtual so both halves show the
  same effect. Changing the device's pixel count resets the virtual to one
  segment: restore the two with `POST /api/virtuals/living-room-led-strip`
  `{"segments": [["living-room-led-strip", 0, 50, false],
  ["living-room-led-strip", 51, 102, false]]}`.
- OTA lock on with a passcode and Arduino OTA off, so firmware cannot be
  flashed from the network without the passcode. The fallback access point
  (`WLED-AP`, opened when Wi-Fi fails after boot) has its own password. Both
  are in `gitops/secrets/wled-living-room.sops.yaml` (Secret
  `home-assistant/wled-living-room`, keys `ota-passcode`, `ap-password`);
  enter the passcode in WLED's Security settings to update firmware.
- Stays off at power-on (`def.on: false`). Boot preset 1 "Default 51-52"
  restores the two WLED segments matching the strip's sections, 0-51 and
  51-103, saved with segment bounds and without brightness or power, so it
  does not turn the strip on. WLED forgets segments on reboot without it.
  LedFx's stream ignores WLED segments. HA's WLED integration makes one light
  per segment, so `light.living_room_led_strip` is a light group of
  `light.living_room_wled_gledopto_segment_0` and `_segment_1`
  (`home-assistant/core/configuration.yaml`); controlling one segment alone
  leaves the other half running its effect.
- UDP sync receive off, so other WLED devices cannot change it.
- DNS `10.0.20.1` (OPNsense), Wi-Fi sleep off.
- Firmware WLED 16.0.1 (official `WLED_16.0.1_ESP32.bin`, flashed over the
  network on 2026-11-14 from 0.15.4; every setting above survived). To
  update: unlock OTA in Security settings with the passcode, upload the
  ESP32 build, check the settings above, lock OTA again.

### Album pins

`light.music_mode_pin` ("Pin colour to this album", `light.py`) pins a colour
to the playing album. On means the album is pinned: every time it plays it
shows that colour exactly. Switching it on pins the colour showing now (the
held manual colour if any, which then goes back to Auto); its colour wheel
edits the pin; switching it off removes the pin, so the album goes back to
automatic colours. It is unavailable while nothing plays. Pins match the
first credited artist and the album by prefix, so features and deluxe
editions follow; a track without an album is pinned by title.

Pins live in Home Assistant storage (`.storage/music_director.pins`,
`pins.py`), so they survive restarts and change without a deploy.
`sensor.music_director` reports `album`, `pinned_colour` and
`palette_source: override` for pinned tracks. The held manual colour still
beats a pin.

`select.music_mode_effect_pin` ("Pin effect to this album", `select.py`)
pins an effect the same way: choose an effect to pin it to the playing
album, or Auto to unpin it. A pin holds a colour, an effect or both, and
each is set and cleared on its own (unpinning the colour keeps a pinned
effect). A pinned effect replaces the automatic choice and a configured
override's effect; the LED Strip Effect dropdown still beats it.
`sensor.music_director` reports `pinned_effect`, and `effect_mode` is
`pinned`, `manual` or `auto`.

`music_director.overrides` in `home-assistant/core/configuration.yaml` seeds
pins and sets effects. Each colour entry becomes a pin once (keys recorded
in the store), so removing it from the dashboard sticks; edit or remove
seeded colours from the dashboard, not the YAML. Each entry needs at least
one of `artist`, `album` or `title` (artist and album match by prefix,
title exactly, case and punctuation ignored) and at least one of `color` (a
`NAMED_HUES` name, or a quoted `"#RRGGBB"` colour that every light shows
exactly) or `effect` (an effect from the table above). The first matching
entry wins; the dropdowns still take precedence. Example:

```yaml
  overrides:
    - artist: The Weeknd
      album: Dawn FM
      color: deep blue
```

## Manual effect override

`input_select.music_mode_effect` ("LED Strip Effect" on the dashboard)
offers Auto plus every effect above. Any choice other than Auto applies at
once and holds across tracks, still with each track's colours and dynamics,
until it is set back to Auto, which restores that track's automatic effect.
`sensor.music_director` reports `effect_mode` (auto or manual) and
`auto_effect`.

## Device offline alert

`music_mode_device_alert` notifies `notify.rupan_phone` while Music Mode is
on when LedFx stops answering (`sensor.music_director` `ledfx_status` is
`unreachable` or `error` for a minute; set by the effect API calls and the
audio feed), or a light of an area taking part (or the WLED strip, with the
strip toggle on) is unavailable for a minute. It checks again a minute
after Music Mode starts, so devices already offline are reported. Lights of
areas not taking part are ignored. A test keeps its light list in step with
`scripts/shared_music_area.yaml`.

## Verify and troubleshoot

1. Turn on Music Mode and play a track on the soundbar. Read
   `sensor.music_director`: its state is the effect, and `metadata_sources`
   should list `reccobeats`, `library:*` or `lastfm`.
2. Check the strip with `GET http://10.0.30.15:8888/api/virtuals/living-room-led-strip`
   from the Home Assistant pod (the agent VM has no route to LedFx).
3. Home Assistant logs one `music_director` line per track with the family,
   effect, palette source and metadata sources. Set
   `custom_components.music_director` to debug for per-source failures.
4. Library lookup: `kubectl -n media logs deploy/navidrome -c lookup`; it
   needs the `navidrome` container's `/data` volume and opens the database
   read-only.
