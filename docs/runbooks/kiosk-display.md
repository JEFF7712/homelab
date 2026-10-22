# Kiosk display

The shared kiosk module (`flake/modules/kiosk.nix`) runs a single full-screen
Chromium under `cage` on a dedicated host and reuses it to show one dashboard
or rotate through several. Two hosts use it:

- `homelab-05`: the Jarvis voice face, a single `url`.
- `homelab-04`: a wall panel that rotates the Grafana fleet overview and the
  Home Assistant 3D-printing dashboard.

## Views and rotation

`homelab.kiosk.views` is an ordered list of `{ url, duration, css }`. With two
or more views the module enables `kiosk-rotate.service`
(`flake/modules/kiosk-rotate.py`), which drives the already-running browser over
its local DevTools port (`127.0.0.1:9222`): navigate, inject the optional `css`
to strip the site's own chrome, hold for `duration` seconds, advance, repeat.
With zero views the single `url` is shown and no driver runs. The tmpfs root
wipes the Chromium profile every boot, so rotation always starts clean.

Cage draws a pointer at boot even with no pointer device attached, and a page's
`cursor: none` only applies after the pointer enters the surface, so the module
points cage at a blank Xcursor theme (`XCURSOR_THEME=invisible`).

`kiosk-rotate.py` is standard-library only and reconnects if the browser
restarts. Debug it with `journalctl -u kiosk-rotate -f` on the host; it logs
`showing view N/M: <url>` per step.

## Home Assistant dashboard auth

A plain HA dashboard authenticates like any browser session: the frontend reads
the `hassTokens` entry from localStorage for the HA origin. The kiosk cannot use
the Jarvis `trusted_networks` bypass: HA is configured with
`use_x_forwarded_for` and `trusted_proxies` covering the LAN (see
`home-assistant/coverage.yaml`), and HA refuses a `trusted_networks` login when
the client address is inside `trusted_proxies` ("Can't allow access from a proxy
server"). Do not remove the LAN entries from `trusted_proxies` to work around
this; Cloudflare Tunnel ingress and the observability probes rely on them.

Instead the kiosk seeds its own session from a long-lived access token:

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
