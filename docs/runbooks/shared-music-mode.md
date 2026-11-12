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

An area with its toggle off is left alone and stays on Adaptive Lighting.
Joining snapshots the area to `scene.pre_music_<area>` and locks Adaptive
Lighting for its lights; leaving, or turning Music Mode off, restores the
snapshot and hands control back. Bulbs change once per track, and on colour
or brightness changes, at `number.music_mode_brightness` (default 70%).

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
   the soundbar (the integration refreshes that entity up to twice).
2. ReccoBeats `audio-features` by Spotify ID: tempo, energy, danceability,
   acousticness and ISRC. No key is needed.
3. Local library by ISRC, else by artist, cleaned title and duration: genres
   and BPM.
4. Last.fm top tags for the track, then the artist, when no genre is known.

The cover palette merges colour clusters of similar hue (so a cover-wide
tint such as Dawn FM's muted blue counts as one colour), then takes the most
vivid well-represented group. Skin tones, browns and olives (`palette.muddy`)
are judged per cluster, never merge with clean colours (so Whole Lotta Red's
dark drips cannot turn its red lettering brown), and count for 0.15 of their
score, so portrait covers do not turn the room amber.

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
(`rules.intensity`): the effect family's prior (calm 0.15, smooth 0.3,
default 0.5, bass 0.75, rock 0.8, drive 0.85) blended with ReccoBeats energy,
or with tempo when there is no energy. Intensity sets how vivid the output is
(`palette._led`):

- Intense tracks (rap, house, rock, high energy) are steered toward vivid reds
  and deep blues (`palette.steer`: oranges and yellows into reds, yellow-greens
  into green, sky blues into deep blue) and, at full intensity, the most
  saturated version of the colour the LEDs can show.
- Calm tracks (R&B, soul, acoustic) keep their cover's own hue and
  saturation, so an orange cover stays orange; a vivid cover stays vivid.
- At every intensity, yellows lean at least partly toward orange, blues and
  reds keep a lightness ceiling so they stay deep, and the yellow-green band
  keeps a lightness floor so nothing renders as mustard or olive.

In the vibe fallback the genre pulls the mood-axis hue toward its own colour
by 95% for calm tracks and 30% for intense ones, so calm R&B stays warm
orange (genre hue) and intense rap follows mood between deep blue and red.
`sensor.music_director` attributes `metadata_sources`, `palette_source`
(`cover`, `vibe`, `default`, `override` or `manual`), `auto_palette_source`
and `intensity` show what each decision used.

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
same value.

`music_director.overrides` in `home-assistant/core/configuration.yaml` pins
a colour and/or effect for matching tracks. Each entry needs at least one of
`artist`, `album` or `title` (artist and album match by prefix, title
exactly, case and punctuation ignored) and at least one of `color` (a
`NAMED_HUES` name) or `effect` (an effect from the table above). The first
matching entry wins; the dropdowns still take precedence. Example:

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
