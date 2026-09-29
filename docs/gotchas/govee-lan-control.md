# Govee H6004 LAN control

Verified 2026-09-27 against four H6004 bulbs at `10.0.20.166`-`.169`
(see static reservations in `tofu/opnsense/homelab.auto.tfvars`).

## What works

The H6004 answers the Govee LAN API over UDP despite third-party notes
claiming the model lacks it. Discovery and control both work:

- Discovery: multicast `239.255.255.250:4001`
  `{"msg":{"cmd":"scan","data":{"account_topic":"reserve"}}}`
- Status: unicast `<ip>:4003`
  `{"msg":{"cmd":"devStatus","data":{}}}`
- Power: `{"msg":{"cmd":"turn","data":{"value":0|1}}}`
- Brightness 0-100: `{"msg":{"cmd":"brightness","data":{"value":N}}}`
- Color: `{"msg":{"cmd":"colorwc","data":{"color":{"r":R,"g":G,"b":B},"colorTemInKelvin":0}}}`
- Color temperature: same `colorwc` command with an RGB of `0,0,0` and
  `colorTemInKelvin` set (verified at 2700 and 4000).

## Gotchas

- The client socket must bind source port **4002**. Unicast commands sent
  from an ephemeral port are silently ignored (no reply, no effect).
  `devStatus` replies compactly with no spaces after colons.
- A multicast scan from a multi-homed host can silently get zero responses
  even with bulbs on the same L2. The scan succeeds from the
  `home-assistant` pod (host network on `homelab-03`), which is also the
  path Home Assistant's Govee integration uses. The existing firewall rule
  `infrastructure-allow-matter-bulbs` plus the Govee 4001 rule already
  permit the controller path; no firewall change was needed.
- Write commands return no UDP ack. Confirm every write with a `devStatus`
  readback instead of waiting for a reply.
- `colorwc` sets one flat color per device. It cannot address a pixel range,
  so a multi-zone bulb renders as a single averaged color. The four bulbs here
  are all one zone, so there is no fidelity loss.
- Commands that look plausible but do not exist on this model: `setcolor` and
  `setBrightness`. Both are silently discarded, no reply and no state change.
  Use `colorwc` and `brightness`. Probing with the wrong command name reads as
  "the bulb ignores writes" and sends you chasing a firmware or LAN-control
  problem that does not exist.
- A bulb can report `colorTemInKelvin: 2700` with `color: {r:0,g:0,b:0}` and
  still be perfectly healthy. That is a bulb sitting in white/CCT mode from a
  previous Adaptive Lighting write, not a fault. `colorwc` with
  `colorTemInKelvin: 0` forces RGB mode; the automation relies on this.

## LedFx does not use this API

LedFx drives Govee bulbs through a reverse-engineered Razer Chroma tunnel
instead: `{"msg":{"cmd":"razer","data":{"pt":"<base64>"}}}` carrying a packet
with header `BB 00 FA B0 00`. The H6004 discards every one of those frames
while still answering `devStatus`, so LedFx reports each device as online and
each virtual as streaming while no color is ever applied. Verified 2026-09-27
by sending the exact upstream activate packet plus red LED data frames and
reading back unchanged state, including after the documented one-minute
Razer-protocol timeout.

Upstream's supported list (H6061, H6167, H61BA, H61D5, H615C) does not include
the H6004. Nothing in the UI surfaces this: `get_device_status()` only checks
that a `devStatus` reply arrived, so every health indicator stays green while
the color path is dead.

The fix is a local driver patch at
`gitops/music-assistant/ledfx-patches/govee.py`, mounted over the installed
module by `gitops/music-assistant/ledfx.yaml` via ConfigMap `subPath`. It
replaces `flush()` with a `colorwc` write and drops the tunnel handshake and
packet helpers. `flush()` caps sends at 10 Hz and skips near-duplicate
colors: the H6004 drops off the LAN when flooded at full render rate, and
Govee flags that flood as abnormal. The mount is deliberate: the upstream
image digest stays immutable, and the patch is reviewable as one file.
Covered by `tests/test_ledfx_govee_patch.py`.

Upstream file is `ledfx/devices/govee.py` at v2.1.9, GPL-3.0. The patch is
derivative work of a copy already in use here; it changes no licensing
posture. Re-check it against upstream on every LedFx bump, since a future
release may add real `colorwc` support and make the patch redundant.

- Matter was evaluated and rejected: the floor lamp (`.166`) advertises
  `_matter._tcp` on IPv6, but commissioning from HA fails because Matter
  discovery is link-local mDNS and the bulbs sit on the clients VLAN while
  HA sits on infrastructure, with no mDNS relay between them. Instead,
  normal control runs through the `govee_lan` custom integration
  (`home-assistant/custom_components/govee_lan/`), which speaks this same
  LAN API as YAML-configured light entities.
