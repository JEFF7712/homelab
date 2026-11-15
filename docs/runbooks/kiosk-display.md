# Kiosk display

The shared kiosk module (`flake/modules/kiosk.nix`) runs a single full-screen
Chromium under `cage` on a dedicated host and reuses it to show one dashboard
or rotate through several. Two hosts use it:

- `homelab-05`: the Jarvis voice face, a single `url`.
- `homelab-04`: the event-driven workshop display described below.

## Views and rotation

`homelab.kiosk.views` is an ordered list of `{ url, duration, css }`. With two
or more views the module enables `kiosk-rotate.service`
(`flake/modules/kiosk-rotate.py`), which drives the already-running browser over
its local DevTools port (`127.0.0.1:9222`): navigate, inject the optional `css`
to strip the site's own chrome, hold for `duration` seconds, advance, repeat.
With zero views the single `url` is shown. With one view that view's URL is shown.
Neither case runs the rotation driver; only two or more views enable it. The tmpfs root
wipes the Chromium profile every boot, so rotation always starts clean.

Cage draws a pointer at boot even with no pointer device attached, and a page's
`cursor: none` only applies after the pointer enters the surface, so the module
points cage at a blank Xcursor theme (`XCURSOR_THEME=invisible`).

`kiosk-rotate.py` is standard-library only and reconnects if the browser
restarts. Debug it with `journalctl -u kiosk-rotate -f` on the host; it logs
`showing view N/M: <url>` per step.

## Home Assistant dashboard auth

A plain HA dashboard authenticates like any browser session: the frontend reads
`hassTokens` from localStorage for the exact HA origin. The rotating dashboard
uses its own long-lived token; the Jarvis face's separate token seeder is not a
replacement for dashboard session initialization.

`home-assistant/core/configuration.yaml` owns Git-managed HA configuration.
`home-assistant/coverage.yaml` describes reconciler capabilities, not active HTTP
proxy settings. If a live login fails with a proxy or trusted-network error,
inspect the loaded HA configuration and request origin before changing it.

The rotation driver seeds its session from a long-lived access token:

- `homelab.kiosk.haDashboardTokenFile` + `homelab.kiosk.haDashboardUrl`
  (set on `homelab-04` to the `kiosk-ha-token` sops-nix secret,
  `/run/secrets/kiosk-ha-token`, and `http://10.0.40.13:8123`).
- `kiosk-rotate.service` receives the token as a systemd credential
  (`LoadCredential`), so the file stays root-only; the process reads it from
  `$CREDENTIALS_DIRECTORY`. `hassTokens` is per-origin localStorage, so the
  driver seeds it while the HA view itself is loaded (set the item, then
  re-navigate so the page picks it up) on every pass through that view.
- The token is encrypted in `secrets/hosts/homelab-04.sops.yaml`; plaintext
  exists only under `/run/secrets` and in the browser profile.

### Rotating the dashboard token

1. In the HA UI, create a long-lived access token (profile > Security).
2. Replace `kiosk-ha-token` with `sops secrets/hosts/homelab-04.sops.yaml`
   and ship the change. Activation restarts `kiosk-rotate`; the log should
   show `seeded Home Assistant session for http://10.0.40.13:8123`.

The re-seed exists only because each boot starts from a fresh browser profile.

### Debugging the HA view

- Still shows the login page: confirm the file is non-empty on the host
  (`ssh homelab-04 "sudo wc -c /run/secrets/kiosk-ha-token"`), then
  `journalctl -u kiosk-rotate | grep 'seed'`.
- The seeded `hassTokens` entry must have `hassUrl` equal to the page origin;
  the seeder derives it from `haDashboardUrl`, so a mismatch there is the
  first thing to check.

## Workshop display v1 (homelab-04)

`homelab.workshopDisplay.enable` replaces dashboard rotation on homelab-04.
The owning module is `flake/modules/workshop-display.nix`; its standard-library
HTTP service serves the UI and polls the A1's Home Assistant entities every five
seconds using a systemd credential. The browser never receives the HA token.
An idle display shows the clock and the pairing QR code; between 22:00 and
07:00 it dims. Running, preparing, paused, and failed prints select the custom
print screen: a live camera (HA's MJPEG `camera_proxy_stream`, proxied at
`/camera.mjpeg` and reconnected every minute or after an error), progress,
finish time and time left, stage, layer, and nozzle/bed temperatures against
their targets. A failed print turns red and lists the error text from the
`print_error` and `hms_errors` binary sensors. When the service observes a
print go from running/preparing/paused to `finish`, it shows a finished screen
(finish time, duration, filament weight and length) for 30 minutes or until
**Return to dashboard** is pressed. A service restart drops that record. Missing/stale HA observations
are visibly identified. Nighttime does not dim an active print, a finished
print, or shared content.

Scan the QR code on the physical monitor and bookmark the resulting paired
phone page. Paste a YouTube link or upload a PNG/JPEG/WebP/HEIC image under
6 MB. HEIC photos are converted server-side to JPEG (at most 3840 px) with
`pillow-heif`; the file's contents decide its type, not the declared MIME type.
Shared content takes priority until **Return to dashboard** is pressed. The
pairing key persists in `/var/lib/private/workshop-display/pairing-key`
(DynamicUser state, kept under `/persist`), so bookmarks survive restarts and
reboots. To revoke every paired phone, delete that file and restart
`workshop-display`. Shared content stays in memory only and is cleared by a
restart. No images are persisted.
YouTube embedding depends on the video's embed permissions and network access;
use AirPlay for videos that disallow embedding. The kiosk permits autoplay.

While a shared YouTube video is on screen, the paired page shows play/pause,
±10 s, and a seek slider. The phone posts keyed `/media` commands
(`play`, `pause`, `seek` with seconds). The server keeps the last eight in a
sequenced queue so taps between kiosk polls are not lost, and the kiosk polls
every second while a video shows. It runs commands through YouTube's iframe
`postMessage` API (`enablejsapi=1`) and reports the player's position to the
loopback-only, keyless `/player` endpoint; the phone reads it from keyed
`GET /media`. A reloaded kiosk skips commands queued before it loaded. Sharing
or dismissing content clears the reported position.

Avahi publishes IPv4 discovery only on homelab-04's physical `enp0s31f6`
interface. Publishing on Docker/Cilium/VM bridges caused the Workshop service
to appear without resolving its SRV/TXT records and could return bridge addresses
for the hostname. Verify `avahi-browse -rpt _airplay._tcp` includes a resolved
`=` record at `10.0.30.14:7101`, rather than only a `+` discovery record.

Sway imports its display environment into the kiosk user manager and restarts
`workshop-airplay.service`, advertising **Workshop**. The user service has
`Restart=always` so receiver crashes recover automatically. On iPhone use
Control Center → Screen Mirroring → Workshop. Its software X11 video window runs under XWayland. The native Wayland sink
failed on this monitor with `Invalid stride (1496)`; the OpenGL sink also failed
to initialize reliably. Sway matches the actual X11 class `GStreamer` and
instance `Workshop`, then makes that window fullscreen and focuses it over the
dashboard. Stopping mirroring closes it and reveals the dashboard again.
The launcher waits for the previous kiosk receiver to exit before starting a
replacement, avoiding a bind race on the fixed ports. Normal receiver logs are
line-buffered in the user-service journal; debug logging is not enabled in
deployed config. Optional HLS/direct-YouTube mode (`-hls`) is disabled: it crashed
with `video_renderer_choose_codec: assertion failed: (!hls_video)` during a
real disconnect/reconnect cycle. Use screen mirroring or the paired YouTube-link
page instead. Check recovery with the user service
`systemctl --user show workshop-airplay -p NRestarts -p MainPID`; a forced
receiver exit must restore both TCP 7101 and its resolved AirPlay advertisement.
AirPlay playback and audio must be tested with a real iPhone. The receiver is
for the trusted LAN; there is no print overlay over AirPlay.

Phone controls listen on TCP 8094. Dashboard observations, camera proxy, and
pairing information are loopback-only; writes require the pairing key. AirPlay
uses TCP/UDP 7100–7102 plus Avahi mDNS. These host firewall additions require
operator approval under the repository's host-exposure policy. No WAN ingress is added. Narrow OPNsense client-VLAN rules allow only these
TCP/UDP ports to homelab-04. Existing client/infrastructure mDNS relay policy is
retained; actual discovery must be checked from an iPhone on client Wi-Fi. If the phone is on a different VLAN, mDNS discovery
and routing depend on existing network rules; do not open unrelated networks.

Check `systemctl status workshop-display sway-tty1`,
`curl --fail http://127.0.0.1:8094/health`, and Sway's process tree for UxPlay.
Inspect the monitor via Chromium's loopback CDP endpoint. For rollback, revert
the workshop module import/enable and restore the previous host kiosk views,
then ship the revert through the fleet deployment path. homelab-05 is unchanged.


## Browser clock preview

Open `http://10.0.30.14:8094/preview` from client Wi-Fi. It scales the shared
clock UI to the monitor's 3440×1440 aspect ratio, with Day, Night and Refresh
controls. `/preview/clock?mode=clock` and `?mode=night` serve that same layout
without the preview toolbar. These modes do not poll Home Assistant/display
state or fetch the real pairing QR; they use placeholder pairing details.
They do not select a mode or change content on the physical monitor.

Edit `flake/modules/workshop-display/display.html` to change the shared layout.
After deployment, refresh the preview to see the new design. For quick local
iteration, serve `flake/modules/workshop-display/` with `python -m http.server`
and open `/display.html?preview=clock` (or `?preview=night`); no HA credentials
or running backend are needed. The iframe preview is same-origin only; live
state, camera and real QR endpoints remain loopback-only.
