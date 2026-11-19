# Spotify Connect on Music Assistant

Music Assistant owns the Connect devices for the soundbar satellite on
`homelab-05` and the bedroom player on `nas-01`. Its `spotify_connect` provider
uses go-librespot; the Spotify music provider is separate. Runtime provider
settings and pairing credentials belong to MA's persistent `/data` directory.

## Speaker availability buttons

`input_boolean.spotify_connect` selects the soundbar and
`input_boolean.spotify_connect_bedroom` selects the bedroom speaker.
`automation.spotify_connect_sync` applies both helpers together through
`shell_command.set_music_assistant_spotify_connect` in HA's core configuration.
The command uses the MA integration's saved URL and token, reads the current
provider config, saves only when needed, and verifies the selected players.
It never starts playback. Both buttons off leaves the provider enabled but
idle, with an empty connected-player list and no Connect daemons advertised.

The automation queues changes so rapid taps cannot leave an older request
winning. It also reconciles every minute and at HA startup, so an MA restart
or temporary API outage recovers without another tap. Unknown/unavailable
helper states are ignored. Failed requests create a persistent HA notification;
the buttons retain the requested state until reconciliation succeeds. A
successful verification dismisses the notification. Matching settings make no
provider write and do not interrupt existing sessions.

Observed failure before this fix: YAML folding joined a Python `if` statement
after a semicolon in the inline command. The process failed before calling MA,
while the helper/card still changed color. Preserve the literal multiline block
and run `python -m unittest tests.test_spotify_connect_toggle -v`; these tests
execute the rendered command for every speaker combination and failure paths.
After deployment, exercise both helpers, compare MA's `connected_players`, and
confirm the corresponding daemon disappears/reappears. Restore the initial
helper states afterwards. Use idle players and do not start playback.

## Pause/resume stops responding

Observed with MA 2.10.4 and go-librespot 0.9.0: music works until pause/stop,
then the phone stays on Connecting. The affected daemon remains alive, but its
loopback API (including `/` and `/status`) times out. Other MA playback works.
Restarting only the affected daemon recovers the connection temporarily.

The [go-librespot pipe backend](https://github.com/devgianlu/go-librespot/blob/master/output/driver-pipe.go)
holds its output lock while writing PCM; pause and close need that same lock.
MA stops reading stdout when detaching the stream, so a full pipe can wedge
the control loop. A living process does not prove this API is responsive.

`gitops/music-assistant/spotify-connect-relay.py` continuously drains the child
at stereo s16le / 44.1 kHz with a 0.1-second startup burst (`BURST_SECONDS`).
Its nonblocking downstream buffer holds at most 0.1 seconds; it drops oldest
buffered audio only when MA stops reading. Every buffered second delays skip,
pause and resume, so keep it small. After an idle gap (the daemon loading the
next track) its clock may catch up only 20 ms (`CATCH_UP_SECONDS`): MA paces
its read at exactly realtime and never drains extra lead, so a full burst per
gap used to stack up across tracks (measured 2.3 s pause-to-silence on
2026-11-19). The child pipe is shrunk to one page (`CHILD_PIPE_BYTES`) because
the unpaced daemon keeps it full. MA's own 0.5 s input read-ahead for the source
is cut to 0.1 s by `install_spotify_connect_pacing` in
`gitops/music-assistant/runtime/recovery.py`. To measure the lag, pause
go-librespot through its loopback API (port 38800) while recording the
soundbar monitor from the satellite pod with `parec`; the director's
`audio_delay` in `home-assistant/core/configuration.yaml` should match it.
This bounds memory and latency, prevents decoding
the entire track ahead, and keeps pause/stop requests responsive. The wrapper
retains the existing distinct discovery/API port ranges and LAN interface.
Kustomize generates a hashed ConfigMap so relay edits replace the pod.

Validate with `python -m unittest tests.test_spotify_connect_relay -v`, then
`just check-changed` and `just fmt-check`. The subprocess tests cover exact PCM
delivery, exit status, pacing, stalled-reader control and child cleanup.

For live verification, obtain playback permission, set the satellite volume
to 1%, and verify that value before starting. Test play, pause, resume and
stop; query the daemon API with a bounded timeout after each transition and
after leaving the player idle. Then reconnect. Stop both the Spotify source
and satellite afterwards. Never print daemon command lines indiscriminately:
other playback engines can carry credentials in their arguments.

If a daemon is already wedged, reload the Spotify Connect provider or restart
only the affected process after verifying its executable and per-player
`--config_dir`. The provider supervises it. Keep credentials and state files.
Roll back the relay by reverting its deployment/configuration commit through
`just ship`; this restores the original streaming path without changing data.

## Queueing songs during a Spotify Connect session

While Spotify Connect plays, the Spotify app owns the queue; Music Assistant
only plays the stream. MA's "Add to queue" lands in MA's own (inactive) queue,
and "Play now"/"Replace" takes the speaker away from Spotify Connect. To add to
Spotify's queue from Home Assistant, use the Shared Spaces "Add to Spotify
queue" card (`input_text.spotify_queue_request` + `script.spotify_add_to_queue`):
it finds the track on Spotify through MA's search and calls `play_media` with
`enqueue: add` on `media_player.spotify_rupan`. It only reaches sessions on
Rupan's Spotify account, and posts a notification when nothing is playing or
nothing matches. Guests on their own accounts use a Spotify Jam instead.
