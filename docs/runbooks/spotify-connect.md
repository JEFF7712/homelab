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
at stereo s16le / 44.1 kHz with a half-second startup burst. Its nonblocking
downstream buffer holds at most half a second; it drops oldest buffered audio
only when MA stops reading. This bounds memory and latency, prevents decoding
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
