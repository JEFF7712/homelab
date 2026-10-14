# Switch and access-point port map

OPNsense is the only inter-VLAN router, DHCP server, DNS provider, and inter-VLAN firewall. A host may route only its explicitly isolated per-VM workspace segments under `flake/modules/agent-workspace-network.nix`; those segments are not VLANs and do not extend onto the physical switches.
The wall run is an 802.1Q transit link: VLAN 99 carries the ISP handoff to the
OPNsense WAN NIC, while VLANs 10, 20, and 30 provide downstairs management,
trusted-client, and cluster worker access. VLAN 99 never reaches the OPNsense LAN trunk.

## Upstairs TL-SG108E

Management: `10.0.10.2/24`, gateway `10.0.10.1`. Configure through port 8
so the management connection stays on VLAN 10.

| Port | Role | Untagged VLAN | Tagged VLANs | PVID |
|---|---|---:|---|---:|
| 1 | Unmanaged cluster access switch | 30 | | 30 |
| 2 | OPNsense WAN NIC (see WAN/LAN note) | 99 | | 99 |
| 3 | OPNsense LAN NIC (see WAN/LAN note) | | 10, 20, 30, 40, 50, 60 | 1 |
| 4 | Downstairs wall trunk | | 10, 20, 30, 99 | 1 |
| 5 | AdGuard and NetBird appliance | 30 | 60 | 30 |
| 6 | NAS (`nas-01`) | 30 | | 30 |
| 7 | Upstairs TP-Link RE505X AP | 20 | | 20 |
| 8 | Management access | 10 | | 10 |

The hardware-reserved default VLAN 1 remains untagged on every port in this
switch firmware. Only trunk ports use PVID 1. No access port carries
production traffic in VLAN 1.

The unmanaged cluster switch uses a single uplink on port 1. Its attached HP
Mini and two CLI ar9070 nodes share VLAN 30 and that one gigabit uplink.

### 802.1Q VLAN membership (upstairs)

Enter on the switch's 802.1Q VLAN page. For each listed VLAN, set all other
ports to Not Member, removing their old memberships.

| VLAN | Tagged ports | Untagged ports |
|---|---|---|
| 10 | 3, 4 | 8 |
| 20 | 3, 4 | 7 |
| 30 | 3, 4 | 1, 5, 6 |
| 40 | 3 | None |
| 50 | 3 | None |
| 60 | 3, 5 | None |
| 99 | 4 | 2 |

### PVID settings (upstairs)

Enter on the switch's 802.1Q VLAN PVID Settings page, then click Apply.
PVID assigns incoming untagged traffic to a VLAN. Tagged traffic keeps its
VLAN ID, so trunk ports use PVID 1 while carrying the tagged VLANs above.
Changing VLAN membership alone does not set PVID.

| Port | PVID |
|---|---:|
| 1 | 30 |
| 2 | 99 |
| 3 | 1 |
| 4 | 1 |
| 5 | 30 |
| 6 | 30 |
| 7 | 20 |
| 8 | 10 |

### OPNsense WAN/LAN identification

Ports 2 and 3 assume port 2 is WAN and port 3 is LAN. If they are reversed,
swap only those two rows (including tagged/untagged membership and PVID).
Identify them before applying: open OPNsense Interfaces -> Assignments to see
which physical interface is assigned to WAN, then Interfaces -> Overview for
link status. Briefly unplug the cable from switch port 2 and see which
physical interface loses link, then reconnect it. If access disappears,
reconnect and use the local OPNsense console to check instead.

## Downstairs managed switch

Management: `10.0.10.3/24`, gateway `10.0.10.1`.

| Port | Role | Untagged VLAN | Tagged VLANs | PVID |
|---|---|---:|---|---:|
| 1 | ISP/modem handoff | 99 | | 99 |
| 2 | Upstairs wall trunk | | 10, 20, 30, 99 | 1 |
| 3 | Downstairs AP/router in AP mode | 20 | | 20 |
| 4 | Living room cluster switch drop | 30 | | 30 |
| 5 | Trusted-client spare | 20 | | 20 |

VLAN 99 is Layer-2 WAN transit only. Do not connect a client or AP to port 1,
and do not connect the downstairs switch to the upstairs switch by any path
other than the wall trunk on port 2.

Port 4 connects to a living room wall drop feeding a secondary NETGEAR R6400v2
used temporarily as an unmanaged switch for two k3s cluster PCs. The uplink
from the wall and both PCs must use the router's LAN ports (never the WAN
port). DHCP and all Wi-Fi radios must be disabled on this router so wireless
clients cannot bridge into the VLAN 30 infrastructure network.

## Access points

Both APs are ordinary VLAN 20 clients. They are not routers and do not tag
their uplinks.

| Location | Device | Switch port | Addressing | Required mode |
|---|---|---:|---|---|
| Upstairs | TP-Link RE505X (AX1500) | TL-SG108E port 7 | Static `10.0.20.2/24` | Access Point; DHCP, WPS, and guest network off |
| Downstairs | NETGEAR R6400v2 | Downstairs port 3 | Reserved static `10.0.20.3/24` (MAC `80:cc:9c:21:88:e7`, `tofu/opnsense/homelab.auto.tfvars`) | Access Point; DHCP, NAT, and routing off |

Use a LAN port, never the WAN/Internet port, for each AP uplink. Both APs use
the same SSID and security settings for client roaming. Assign different
non-overlapping radio channels where their coverage overlaps.
