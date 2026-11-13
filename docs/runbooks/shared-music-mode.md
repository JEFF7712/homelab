# Shared music mode

Music Mode (`input_boolean.shared_music_mode`) lights the shared spaces from
the track playing on the soundbar
(`media_player.homelab_05_satellite_media_player_2`). Once per track it
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
(`standby`), and so is the strip when its toggle is off. Toggling an area or
the strip mid-session paints it or switches it off (`paint` / `off`) and
keeps the snapshot. Turning Music Mode off restores every area and the strip
and hands the areas back to Adaptive Lighting (`leave`). Bulbs change once
per track, and on colour or brightness changes, at
`number.music_mode_brightness` (default 70%).

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
  `scripts/navidrome_lookup.py`): read-only lookup into the Navidrome
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
rather than cached; the log says `No cover art` or `No usable cover colour`.

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
al. (PNAS 2013).

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

## Effects

| Family | Chosen when | LedFx effects |
| --- | --- | --- |
| calm | ambient, classical, acoustic, folk, soundtrack; energy < 0.35 | melt, lava_lamp |
| smooth | R&B, soul, funk, jazz, reggae; hip-hop with energy < 0.5 | melt, energy |
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

WLED scales LedFx's DDP stream by its own brightness. When the strip takes
part, Music Mode switches `light.living_room_led_strip` on at full
brightness, so `number.music_mode_brightness` alone sets the strip's level;
the WLED snapshot restores its own brightness afterwards.

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
