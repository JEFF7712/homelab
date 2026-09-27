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
- `flake/modules/adguard-netbird/roku-bridge.py` — bridge Python vendored from `github.com/JEFF7712/roku-bulb-local@7b71741` (`scripts/bridge.py`); canonical source for the deployed closure. Re-pin by copying the file and updating the rev in `roku-bridge.nix` (`tests/test_roku_bridge_contract.py` enforces the hash).
- `flake/modules/adguard-netbird/roku-cloud-bridge.nix` + `roku-cloud-bridge.py` — cloud fallback for bulbs whose firmware closed the LAN port-88 server (LS1016X strip on 1.2.1.13, zero LAN ports on a full 1-1024 sweep). Speaks the same MQTT topics and discovery identity as the LAN bridge, so HA needs no changes. Only list a bulb here after REMOVING it from the LAN bridge config. Reuses the roku-bridge broker credential. Session cookies self-renew into `/var/lib/roku-cloud-bridge/cookies.json`; the seed in `/persist/secrets/roku-cloud-cookies.json` is only copied when state is missing. `scripts/roku_cloud_bridge.py` renders both secret files from `ROKU_CLOUD_BULBS_JSON` / `ROKU_CLOUD_COOKIES_JSON`.
- `/home/rupan/projects/roku-bulb-local/scripts/local_set.py` — single-PID tester, useful for bypassing HA and the bridge entirely.
- `/home/rupan/projects/roku-bulb-local/tests/test_bridge.py` — unit tests for `ha_to_plist` and `commanded_state`.
- `/home/rupan/projects/roku-bulb-local/docs/local-http-api.md` — full protocol reference (encryption, pids, OUI checks, crash warning about nested-object `characteristics`).
- `/home/rupan/projects/roku-bulb-local/docs/bulb-inventory.md` — bulb MAC/IP/`enr` inventory (sensitive bits live in gitignored `captures/`).

## Bedroom music mode (isolated LedFx on nas-01)

The bedroom runs its **own** LedFx instance, on `nas-01`, and is deliberately
isolated from shared spaces. Nothing in the living room can reach bedroom
state, and the bedroom cannot reach living-room state.

```
Bedroom audio (Bluetooth Bose Flex 2 on nas-01)
  ↓ PipeWire monitor bluez_output.E4_58_BC_10_CA_C9.1.monitor
LedFx instance on nas-01 (scene bedroom-music-mode, effect energy)
  ↓ OSC "All To One" [[R,G,B] x2] floats 0.0-1.0 to /bedroom, UDP 10.0.30.10:9000 @10Hz
ledfx-roku-bridge on adguard-netbird-01   (shared transport only)
  ↓ throttles, then roku/light/<slug>/set
roku-bridge → encrypted POST http://<bulb-ip>:88/device_request
```

Why a second instance rather than sharing the one on homelab-05: LedFx can
only analyse audio that reaches the host it runs on. The bedroom speaker is
Bluetooth attached to `nas-01`, so the audio physically exists there;
homelab-05's local tap never sees it. Running the instance next to the audio
is what makes the effect work at all.

Isolation is enforced in three places, all covered by
`tests/test_ledfx_bedroom_isolation.py`:

- **Deployment**: `flake/modules/ledfx-bedroom.nix` is imported only by
  `flake/hosts/nas-01`, never by a shared-spaces host.
- **REST**: separate `ledfx_bedroom_*` rest commands in
  `home-assistant/core/configuration.yaml` point at `10.0.30.20`, while the
  shared `ledfx_*` commands stay on `10.0.30.15`. The NAS API port is opened
  only to the k3s nodes.
- **Content**: the bedroom automations reference only bedroom lights and only
  the bedroom rest commands; the shared Govee automation references only shared
  lights. Each LedFx instance holds its own devices, virtuals, and scenes, and
  the relay is a dumb transport with no shared state between paths.

Deployment: `deploy_nas_ledfx_bedroom` writes a podman authfile built from the
existing read-only `node` registry credential (the same account the k3s nodes
use, so no new secret exists), activates nas-01, and asserts the API answers.
The image is digest-pinned to the same build as the shared instance, and the
container graph lives under `/var/lib/ledfx-bedroom` inside `/persist`,
because the root filesystem is tmpfs and an unpinned image would otherwise be
re-pulled on every boot.

Two LedFx quirks the relay has to absorb, both learned by watching a live
frame stream rather than the docs:

- **Frames are normalized floats, not 0-255.** The docs describe `All_To_One` as
  `[[R, G, B], ...]`, but the device emits values in `0.0-1.0`. Casting them to
  int truncates every channel below 1.0 to zero, so the effect renders as a
  solid black frame and the bulbs never light. The relay rescales a frame only
  when it is float-typed and entirely within `0.0-1.0`.
- **Color and brightness are separate bulb properties.** LedFx sends color
  only, so a color-only command leaves brightness wherever it was and a dark
  frame renders as "on but invisible". The relay splits each frame into a
  level and a hue: `brightness` carries the frame intensity, and the color is
  normalized so its brightest channel is full. The result reproduces the
  intended RGB instead of squaring it with the level.

Pixel order is desk, floor and must match the LedFx group-virtual
segment order. The bedroom light strip (`7C67AB2A0505`) is excluded from
both: a firmware update broke its local control and a separate cloud
implementation is in progress. Slugs (`7C67AB0A83AB`, `7C67AB1623B7`) are
MAC-derived topic fragments, already visible as retained discovery topics,
so they live in the plain store config in `ledfx-roku-bridge.nix`. The relay
reuses the `roku-bridge` broker credential, so no new mosquitto user or
secret was needed. The firewall allows UDP 9000 only from homelab-05
(`10.0.30.15`, the LedFx host); see `adguard-netbird-appliance.nix`.
The 3 bedroom light entities are excluded from the HA recorder because music
mode streams per-frame optimistic state over MQTT.

Live LedFx objects. The bedroom trio lives on the **nas-01** instance
(`http://10.0.30.20:8888`), persisted in `/var/lib/ledfx-bedroom/config`:

- device `bedroom-osc` (type `osc`, `10.0.30.10:9000`, 2 pixels, All_To_One, `/bedroom`, 10Hz)
- virtual `bedroom-music-mode-bulbs` (segments `bedroom-osc` pixels 0/1)
- scene `bedroom-music-mode` (energy)

The shared-spaces instance on homelab-05 (`http://10.0.30.15:8888`) holds only
the Govee devices, the `music-mode-bulbs` virtual, and the `music-mode` scene.
Bedroom objects were removed from it so the two instances cannot address each
other's bulbs.

Coordination note for the strip cloud implementation: keep it off the
`roku/light/7C67AB2A0505/set` topic or throttle there. If that topic ever
drives cloud calls, this relay's per-frame output would become per-frame
cloud API calls and hit rate limits. The relay only publishes for its
configured pixels, so today that topic is silent from music mode.

API gotchas, learned live: create devices with `POST /api/devices` **with**
`id`; create virtuals/scenes with `POST` **without** `id` (the id slugifies
from `name`); set virtual segments with `POST /api/virtuals/<id>`
`{"segments": [...]}` (`PUT` on that path only toggles `active`, and
`POST /api/virtuals` with an `id` only updates `config`). Recovery is
`DELETE /api/devices/bedroom-osc`, `DELETE /api/virtuals/bedroom-music-mode-bulbs`,
`DELETE /api/scenes/bedroom-music-mode`.

## Bulb ownership must stay disjoint

A MAC belongs to exactly one bridge. The local bridge serves
`ROKU_BRIDGE_BULBS_JSON`, the cloud bridge serves `ROKU_CLOUD_BULBS_JSON`, and
they must not overlap: both subscribe to `roku/light/<slug>/set` and publish
`roku/light/<slug>/state`, so an overlap means two writers on one bulb and
flapping optimistic state. Current split: desk and floor on the local bridge,
the LS1016X strip on the cloud bridge.

The list is delivered out of band, so a redeploy does not by itself reload it.
Both daemons therefore re-stage their config in `ExecStartPre` (the `+` prefix
runs it as root, since the source secret is root-owned), and
`deploy_zigbee_gateway` restarts both after writing new secrets. Without that,
a `RemainAfterExit` staging oneshot leaves the daemon serving the previous list
from memory: this happened once, the strip stayed double-answered after being
moved to the cloud bridge, and the local bridge logged a 3s timeout per command
for every strip command the cloud bridge handled correctly.

To confirm the split on a live box:

```bash
ssh adguard "sudo cat /persist/secrets/roku-bridge-bulbs.yaml \
  /persist/secrets/roku-cloud-bulbs.yaml"
ssh adguard "sudo journalctl -u roku-bridge -n 20 --no-pager | grep <mac>"
```

A MAC appearing in both files, or a bridge logging a MAC that is not in its
own file, is the failure signature.

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
