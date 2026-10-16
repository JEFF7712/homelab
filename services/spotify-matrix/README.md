# Spotify Matrix Streamer

Streams the current Spotify album art rendered as a rotating vinyl disc to a Raspberry Pi Pico W driving a 64x64 HUB75 RGB LED matrix. TCP over Wi-Fi is the default transport at 20 FPS. Dedicated USB CDC is also available.

## Home Assistant source (preferred)

`ha_matrix_streamer.py` follows the existing `media_player.spotify_rupan` entity
through Home Assistant's WebSocket API. It reads that entity's cached state once
per connection, subscribes to its state changes, and downloads artwork in a
background worker with an eight-image cache. Rendering and acknowledged TCP
delivery run independently at 20 FPS.

This entry point makes no Spotify Web API or OAuth requests and has no direct
Spotify fallback. Home Assistant's existing Spotify integration still uses its
own API quota. Reusing its metadata avoids adding a second playback poller.
The legacy laptop polling service remains disabled following the quota warning.

Playing metadata older than 120 seconds, unavailable entities, and disconnected
HA sessions render the idle screen. Paused artwork stays visible without
rotating. Missing or loading artwork also renders idle. HA and Pico connections
recover independently. HA application heartbeats detect stalled connections;
subscription events also count as proof that the connection is alive.

For local qualification, pass a protected HA token file through
`--ha-token-file`, plus `--duration` and `--stats-file` for measured runs.
`--health-file` writes an atomic private JSON health record. Readiness requires
an active HA session and a recent Pico acknowledgement; an idle player is healthy.
No Spotify credentials or saved Spotify tokens are needed.

Deployment, credential provisioning, image publication, and acceptance are
documented in [DEPLOYMENT.md](DEPLOYMENT.md). Kubernetes desired state lives in
`gitops/spotify-matrix`; it has not been applied to the homelab.

## Architecture

- **Pico W:** CircuitPython 10.3.1, SeenGreat V3.9 adapter, 64x64 panel, four-bit matrix color depth. Firmware source: `/home/rupan/projects/raspberry_pi/pico/code.py`, installed as `CIRCUITPY/code.py`.
- **Host:** Follows HA playback events, renders the rotating disc with Pillow, and sends little-endian RGB565 frames. It waits for a display acknowledgement before sending another frame, keeping at most one frame in flight. The legacy standalone Spotify entry point below is optional.
- **USB:** Connect the Pico directly to the machine running this service. `boot.py` enables separate console and data endpoints. A hardware reset is required after changing USB descriptors. The data endpoint is auto-detected by its `CircuitPython CDC2 control` interface, currently `/dev/ttyACM1`. Console `/dev/ttyACM0` remains available for diagnostics. Stable data path: `/dev/serial/by-id/usb-Raspberry_Pi_Pico_W_E661A4D41730262D-if02`.
- **Wi-Fi:** TCP port `9999` at `10.0.20.131`, selected by default. TCP_NODELAY is enabled on both ends, and Wi-Fi power saving is disabled. USB takes priority while its data port is open. UDP is no longer accepted.
- **Display:** Native `readinto`/`recv_into` reads directly into the RGBMatrix framebuffer. Only a complete frame triggers `matrix.refresh()`. The idle UI uses manual displayio refresh. Partial frames time out and are discarded.

USB framing is `S565` followed by exactly 8192 payload bytes. TCP sends a receiver-ready greeting, `R565`, after accepting a connection; the sender waits for the complete greeting before transmitting pixels. TCP then uses exactly 8192 payload bytes per frame. Both transports return one byte, `A`, after matrix refresh. Payloads contain 4096 row-major RGB565 pixels, little-endian. Transport errors close the sender connection and retry with a newly rendered frame. The firmware returns to the idle UI after eight seconds without a complete frame.

The receiver disables CircuitPython automatic reload so host filesystem metadata writes cannot interrupt playback. After updating `code.py`, explicitly reload through the REPL with Ctrl-D or reset the Pico.

## Requirements

- Python 3.11+
- `uv`

## Quick Hardware Test (No Spotify Required)

To test the panel over Wi-Fi without Spotify:

```bash
uv run spotify_matrix_streamer.py --test
```

This streams an animated vinyl pattern at 20 FPS. For USB, select `--transport usb`. `usb_streamer.py` uses the same USB transport and renderer with a faster test rotation.

For a measured run, including receiver counters and acknowledged FPS:

```bash
uv run python benchmark_stream.py --seconds 60 --fps 20 --console /dev/ttyACM0
```

The benchmark fails if a send is unacknowledged or delivered FPS is below 95% of the requested rate. It measures transfer through completed matrix refresh, not visual panel inspection. Results are saved beside the Pico firmware as `validation-20261016-wifi.json` and `validation-20261016-usb.json`.

On 2026-10-16, Wi-Fi delivered 1193/1193 acknowledged frames over 60 seconds at 19.88 FPS, with zero incomplete frames. Mean render-to-display latency was 34.6 ms; maximum was 99.6 ms. Wi-Fi occasionally exceeds a 50 ms frame interval, so delivery is not guaranteed to have uniform frame spacing. The USB reference run delivered 1198/1198 acknowledged frames at 19.96 FPS, with 33.6 ms mean and 39.3 ms maximum latency. Five offline transport tests and Pyright passed. The test animation was subsequently replaced by the authenticated Spotify service described below.

The sender can run on a remote homelab host without a USB data connection:

```bash
uv run spotify_matrix_streamer.py --test --transport tcp --pico-ip 10.0.20.131
```

The earlier 6–14 FPS Wi-Fi figures predated the final TCP_NODELAY and direct-framebuffer receiver changes. The current receiver meets the 15–20 FPS target over Wi-Fi.

## Legacy direct Spotify setup

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and create an app.
2. In the app settings, add this Redirect URI:
   ```text
   http://127.0.0.1:8888/callback
   ```
3. Start the local setup form:
   ```bash
   uv run python setup_spotify.py
   ```
   Open `http://127.0.0.1:8888`, enter the client ID and secret, and continue to Spotify to approve currently-playing access. The server listens only on loopback, does not log requests, and expires after 30 minutes. Credentials are never placed in the authorization URL or printed to the terminal. After approval, it saves `.env` and the refreshable token cache with owner-only permissions (0600), then exits.

4. Run the streamer:
   ```bash
   uv run spotify_matrix_streamer.py
   ```
   The streamer reuses `~/.cache/spotify_matrix_token.json` and refreshes the access token as needed. Token cache updates are atomic and retain owner-only permissions.

## Local unattended service

`spotify-matrix.service` is the laptop user-service definition. It is installed at
`~/.config/systemd/user/spotify-matrix.service`. It runs the
locked Python environment, restarts after process failures, and reuses the
private credentials and refreshable token cache. It requires the laptop to be
awake and the user service manager running. This is not a homelab deployment.

On 2026-10-16, real Spotify playback reads and an explicit token refresh both
succeeded. The display shows album art while paused and spins while playing.
The previous `pico-matrix-vinyl-demo.service` was stopped before starting this
service so only one sender controls the panel.
An injected process failure also triggered automatic restart after five seconds;
the restarted process loaded saved credentials and resumed Spotify streaming.

After a quota warning in Spotifast, the matrix service was stopped and login
startup disabled pending quota recovery. Spotify pools development-mode quota
across apps owned by the same developer account, so separate client IDs do not
isolate the matrix from a personal Spotifast app on that account. No additional
live API calls were made during the quota investigation.

Playback polling now defaults to 15 seconds (previously 1.5 seconds), slowing to
at least 60 seconds while paused or idle. Rendering and Wi-Fi delivery remain
independent at 20 FPS. HTTP 429 responses honor `Retry-After`; a structured
`QUOTA_EXCEEDED` response stops API polling until manual restart. HTTP 401 gets
at most one refresh/retry. These paths are covered by offline tests. The quota
reason and rate-limit delay are logged explicitly.

Quota reference: [Spotify's July 2026 update](https://developer.spotify.com/blog/2026-07-23-web-api-quota-updates).

## Command Line Options

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--transport` | `tcp` | Wi-Fi TCP or dedicated USB CDC |
| `--serial-port` | auto-detected | Dedicated USB data endpoint, never the REPL console |
| `--pico-ip` | `10.0.20.131` | Pico W IP for TCP |
| `--port` | `9999` | TCP destination port |
| `--fps` | `20` | Requested streaming frame rate |
| `--poll-interval` | `15` | Minimum playback polling interval in seconds; paused/idle polls at least 60 seconds apart |
| `--rotation-speed` | `45.0` | Disc rotation speed in degrees per second |
| `--brightness` | `0.7` | Display brightness scale factor (0.1 to 1.0) |
| `--test` | `False` | Run animated test pattern without Spotify |
