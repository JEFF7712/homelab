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
An idle display shows the clock; between 22:00 and 07:00 it dims. Running,
preparing, paused, and failed prints select the custom print screen, with a
camera snapshot refreshed every five seconds. Missing/stale HA observations
are visibly identified. Nighttime does not dim an active print or shared content.

Scan the QR code on the physical monitor and bookmark the resulting paired
phone page. Paste a YouTube link or upload a PNG/JPEG/WebP under 6 MB. Shared
content takes priority until **Return to dashboard** is pressed. The random
pairing key and shared content live in memory: service restarts revoke bookmarks
and clear content, so scan again. No images are persisted. iPhone HEIC photos
need conversion to JPEG/PNG; a screenshot is a convenient alternative.
YouTube embedding depends on the video's embed permissions and network access;
use AirPlay for videos that disallow embedding. The kiosk permits autoplay.

UxPlay starts inside the Sway session, advertising **Workshop**. On iPhone use
Control Center → Screen Mirroring → Workshop. Its Wayland video window covers
the dashboard; stopping mirroring closes it and reveals the dashboard again.
AirPlay playback and audio must be tested with a real iPhone. The receiver is
for the trusted LAN; v1 has no print overlay over AirPlay. Print completion
returns to the clock rather than retaining a finished screen.

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
