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
snapshot and hands control back. Bulbs change once per track at 70%
brightness.

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
vivid well-represented group plus a contrasting accent. Skin tones, browns
and olives (`palette.muddy`) count for 0.15 of their score, so portrait
covers do not turn the room amber. When no group scores at least
`MIN_COVER_SCORE`, the palette comes from the song's vibe
(`palette.vibe_palette`): valence picks a point on a deep blue (sad) to
purple to deep red (happy) axis, the genre pulls it 30% toward its own hue,
energy (or tempo) raises saturation and brightness, and the accent is the
other end of the axis, after Palmer et al. (PNAS 2013).

On output the lights favour vivid blues and reds (`palette.steer`): oranges
and yellows become reds and corals, yellow-greens become green, and sky blues
become deep blue. Blues and reds have a lightness ceiling so they stay deep,
and the yellow-green band keeps a lightness floor so nothing renders as
mustard or olive, as Material's DislikeAnalyzer does. `sensor.music_director`
attributes `metadata_sources` and `palette_source` (`cover`, `vibe` or
`default`) show what each decision used.

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
