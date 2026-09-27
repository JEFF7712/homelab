# Roku Bulb Bridge Architecture

The bedroom Roku bulbs (`light.desk_lamp_desk_lamp`, `light.floor_lamp_floor_lamp`, `light.light_strip_light_strip`) are **not** on Z2M and **not** on the HA Roku integration. They are Wyze-derived Roku BC1000X / RK_BA19C hardware controlled by a custom MQTT bridge daemon that translates HA light commands into encrypted HTTP requests on TCP 88. If you are debugging bulb behavior and reach for the Z2M UI, you are looking at the wrong system.

## Data flow

```
HA UI / API
  ↓ publish JSON to roku/light/<mac>/set (schema=json, optimistic)
mosquitto 10.0.30.10:1883
  ACL: home-assistant rw homeassistant/#, rw roku/#
  ACL: roku-bridge   rw homeassistant/#, rw roku/#
  ↓ subscribe
roku-bridge daemon
  systemd unit on adguard-netbird-01, runs as user roku-bridge
  source: /home/rupan/projects/roku-bulb-local/scripts/bridge.py
  deployed: /nix/store/<hash>-roku-bridge/bin/roku-bridge
  ↓ AES-CBC encrypt + POST http://<bulb-ip>:88/device_request
Bulb (each on 10.0.20.x)
```

After each command the bridge publishes optimistic state to `roku/light/<mac>/state`. HA reads it and updates the UI. Bulbs do **not** report state back to HA — every state value in HA is what HA last sent, not what the bulb is currently doing. Verify physical behavior by looking at the bulb or by reading the bridge log, never by trusting the HA UI alone.

## PIDs

- `P3` — power, `0` or `1`
- `P1501` — brightness, `1`–`100` (percent)
- `P1502` — color temperature, `1800`–`6500` (kelvin)
- `P1507` — RGB, six uppercase hex chars (`FF0000` = red)

The MQTT JSON light command translates as:

| HA payload field | Bridge action |
| --- | --- |
| `state: "OFF"` | early-return P3=0 |
| `state: "ON"` | append P3=1 |
| `color_temp: <mireds>` (when no `color` field) | append P1502=kelvin |
| `color: {r, g, b}` **or** `rgb_color: [r, g, b]` | append P1507=RRGGBB |
| `brightness: <0-255>` | append P1501=pct(%) |

Both the modern `color: {r, g, b}` schema and the legacy `rgb_color: [r, g, b]` schema are accepted. `color_temp` wins over both when present.

## Why both schemas

HA's MQTT JSON light integration drifted from `rgb_color: [r, g, b]` (flat list) to `color: {r, g, b}` (nested object). The bridge originally only matched the legacy form, so color commands silently fell through and bulbs never received `P1507`; the UI updated optimistically but the physical lights stayed put. The bridge now handles both — see commit `953fe41 fix(roku-bridge): handle modern HA MQTT JSON light color schema`. If you touch `ha_to_plist` in the future, keep both branches.

## Files

- `flake/modules/adguard-netbird/roku-bridge.nix` — systemd unit, secrets staging, and bridge user; loads the pinned script below. `mosquitto.nix` in the same directory sets up the broker user/ACL.
- `flake/modules/adguard-netbird/roku-bridge.py` — bridge Python vendored from `github.com/JEFF7712/roku-bulb-local@53fc6d2` (`scripts/bridge.py`); canonical source for the deployed closure. Re-pin by copying the file and updating the rev in `roku-bridge.nix` (`tests/test_roku_bridge_contract.py` enforces the hash).
- `flake/modules/adguard-netbird/ledfx-roku-bridge.nix` + `ledfx-roku-bridge.py` — bedroom music-mode relay (see below). Authored in-repo, not vendored.
- `/home/rupan/projects/roku-bulb-local/scripts/local_set.py` — single-PID tester, useful for bypassing HA and the bridge entirely.
- `/home/rupan/projects/roku-bulb-local/tests/test_bridge.py` — unit tests for `ha_to_plist` and `commanded_state`.
- `/home/rupan/projects/roku-bulb-local/docs/local-http-api.md` — full protocol reference (encryption, pids, OUI checks, crash warning about nested-object `characteristics`).
- `/home/rupan/projects/roku-bulb-local/docs/bulb-inventory.md` — bulb MAC/IP/`enr` inventory (sensitive bits live in gitignored `captures/`).

## Bedroom music mode (LedFx relay)

LedFx has no device type for these bulbs, so the bedroom path is
LedFx OSC output → `ledfx-roku-bridge` → MQTT → `roku-bridge` → bulb:

```
LedFx scene bedroom-music-mode (effect: energy)
  ↓ OSC "All To One" [[R,G,B] x3] to /bedroom, UDP 10.0.30.10:9000 @10Hz
ledfx-roku-bridge daemon (systemd on adguard-netbird-01, user ledfx-roku-bridge)
  throttles per bulb (0.2s min interval, 12-step color delta) and publishes
  {"state": "ON", "color": {"r","g","b"}} to roku/light/<slug>/set
  ↓ (same translation as HA commands)
roku-bridge → encrypted POST http://<bulb-ip>:88/device_request
```

Pixel order is desk, floor, strip and must match the LedFx group-virtual
segment order. Slugs (`7C67AB0A83AB`, `7C67AB1623B7`, `7C67AB2A0505`) are
MAC-derived topic fragments, already visible as retained discovery topics,
so they live in the plain store config in `ledfx-roku-bridge.nix`. The relay
reuses the `roku-bridge` broker credential, so no new mosquitto user or
secret was needed. The firewall allows UDP 9000 only from homelab-05
(`10.0.30.15`, the LedFx host); see `adguard-netbird-appliance.nix`.
The 3 bedroom light entities are excluded from the HA recorder because music
mode streams per-frame optimistic state over MQTT.

Live LedFx objects (created via REST, persisted on the `ledfx-data` PVC):

- device `bedroom-osc` (type `osc`, `10.0.30.10:9000`, 3 pixels, All_To_One, `/bedroom`, 10Hz)
- virtual `bedroom-music-mode-bulbs` (segments `bedroom-osc` pixels 0/1/2)
- scene `bedroom-music-mode` (energy, same tuning as `music-mode`)

API gotchas, learned live: create devices with `POST /api/devices` **with**
`id`; create virtuals/scenes with `POST` **without** `id` (the id slugifies
from `name`); set virtual segments with `POST /api/virtuals/<id>`
`{"segments": [...]}` (`PUT` on that path only toggles `active`, and
`POST /api/virtuals` with an `id` only updates `config`). Recovery is
`DELETE /api/devices/bedroom-osc`, `DELETE /api/virtuals/bedroom-music-mode-bulbs`,
`DELETE /api/scenes/bedroom-music-mode`.

## Debug commands

```bash
# Bridge log (current generation).
ssh adguard "sudo journalctl -u roku-bridge -f"

# MQTT traffic — must run on the appliance itself; mosquitto only accepts
# 10.0.30.11-15 on port 1883 (k3s nodes, per the firewall rules in
# flake/modules/adguard-netbird-appliance.nix). Auth as the roku-bridge user.
ssh adguard
sudo /nix/store/0vggxw22b2c0ph8jf1iyfd9h6jgmkfpc-python3-3.14.7-env/bin/python3 -c '
import paho.mqtt.client as mqtt
import time, os
p = open("/var/lib/roku-bridge/mqtt.env").read().split("MQTT_PASS=")[1].strip()
c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
c.username_pw_set("roku-bridge", p)
c.on_message = lambda c, u, m: print(f"{m.topic}: {m.payload.decode()}", flush=True)
c.connect("10.0.30.10", 1883)
c.subscribe("#")
c.loop_start()
time.sleep(10)
'
# Then trigger a change from HA and watch the topics.

# Bypass HA entirely, hit the bulb directly. Isolates "bridge is sending the
# wrong command" from "bulb is rejecting it".
cd /home/rupan/projects/roku-bulb-local
uv venv /tmp/rk && uv pip install --python /tmp/rk/bin/python pycryptodome
BULB_ENR=<enr> /tmp/rk/bin/python scripts/local_set.py <ip> <mac> P1507 FF0000
```

## Deploy and recovery

- Permanent changes go through `nixos-rebuild switch` (driven by `python -m scripts.deploy_fleet --target adguard-netbird-01`). The bridge script is baked into `/nix/store/<hash>-roku-bridge/bin/roku-bridge` and the systemd unit points directly at it.
- Emergency / hot-fix path: copy `bridge.py` to `/var/lib/roku-bridge/bridge.py` on the appliance and add a runtime drop-in at `/run/systemd/system/roku-bridge.service.d/override.conf` that overrides `ExecStart` to point at the writable copy. The drop-in disappears on `systemctl daemon-reload` or reboot, so this is for short-term unblocks only — always follow with a real `nixos-rebuild` so the closure catches up.
- See `docs/gotchas/nix-heredoc-indentation.md` for the indentation pitfall that crashed the bridge during one fix and required exactly this hot-fix dance.
