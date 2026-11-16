# Monero onion P2P

`homelab-04` runs a Tor v3 onion service for monerod's anonymous P2P listener
on port 18084. Tor connects to `127.0.0.1:18084`; the onion address is read
from `/var/lib/tor/onion/monero/hostname` at startup and supplied to monerod
through `/run/monero-onion.env`. Tor's state under `/var/lib/tor` persists on
`/persist`, so rebooting does not change the onion address.

The building's upstream router never forwards WAN traffic, so clearnet inbound
P2P cannot arrive. The host sets `homelab.monero.openP2P = false`: the host
firewall keeps 18080 closed, the clearnet listener binds to `127.0.0.1`, and
`hide-my-port` stops monerod advertising it (monerod then logs `Incoming
connections disabled` instead of the hourly `No incoming connections`
warning). Outbound clearnet peers and onion inbound are unaffected.

## Exposure and egress filter

monerod parses untrusted peer traffic on an enforcement host, so the
`monero-egress` nftables table (`flake/modules/monero.nix`) limits new
outbound connections by socket owner (`meta skuid`). Replies on established
connections always pass, so onion-inbound P2P and LAN wallet RPC sessions work.

| User | Allowed new connections | Why |
| --- | --- | --- |
| `monero` | `127.0.0.1:9050/tcp` | Tor SOCKS: `tx-proxy` broadcasts and onion peers |
| `monero` | `127.0.0.53:53/udp+tcp` | systemd-resolved stub from `/etc/resolv.conf`: seed nodes, DNS blocklist, DNS checkpoints (libunbound and getaddrinfo) |
| `tor` | `127.0.0.1:18084/tcp` | onion service forward to monerod |
| both | public internet | clearnet peers; Tor relays |

Every other destination in RFC 1918, CGNAT `100.64.0.0/10`, loopback,
link-local, multicast, reserved, ULA and IPv6 link-local ranges is dropped,
which keeps a compromised daemon away from the approvals service, Woodpecker
gRPC, oplock, kubelet and the LAN. Drops are logged at most 6 per minute per
family with the kernel prefix `monero-egress-drop:` or `tor-egress-drop:`.
monerod needs no NTP and has UPnP (`igd`) disabled by default.

In Loki:

```logql
{hostname="homelab-04", transport="kernel"} |~ "(monero|tor)-egress-drop"
```

An occasional drop to a private address is a peer advertising one; a steady
stream to the same address and port means a legitimate need was missed. To
test the filter by hand on `homelab-04` (root required), each of these must
time out while the last succeeds:

```sh
sudo -u monero curl -m 5 http://127.0.0.1:8020/      # approvals: dropped
sudo -u monero curl -m 5 http://10.0.30.1/           # LAN: dropped
sudo -u tor curl -m 5 http://127.0.0.1:18089/        # tor to wallet RPC: dropped
sudo -u monero curl -m 5 -o /dev/null -w '%{http_code}\n' https://getmonero.org/
```

`sudo -n nft list table inet monero-egress` shows the rules.

## Sandbox

The nixpkgs unit already runs as `monero` with no capabilities,
`NoNewPrivileges`, `PrivateUsers`, `ProtectSystem=strict`, `ProtectProc=invisible`
and `@system-service`. The module adds `SystemCallArchitectures=native`,
`SystemCallFilter=~@privileged`, and drops `AF_NETLINK` from
`RestrictAddressFamilies` (glibc's address probe falls back without it).
`@resources` stays allowed: monerod calls `sched_setscheduler` (syscall 144)
at startup and crash-looped when it was denied. `MemoryDenyWriteExecute`
stays off because RandomX JIT-compiles. A blocked syscall kills the process
with `SIGSYS`, and the fleet deploy's failed-unit check does not catch a unit
in its restart delay, so check after every sandbox change:

```logql
{hostname="homelab-04", transport="audit"} |= "monerod"
``` The nixpkgs Tor unit is already
confined further (`RootDirectory`, `MemoryDenyWriteExecute`, native
architecture, narrowed syscall groups) and is left unchanged.

## Health

monerod logs at `log-level=0,blockchain:INFO`, so each accepted block appears
as a `HEIGHT n` line:

```logql
{hostname="homelab-04", unit="monero.service"} |= "HEIGHT"
```

After a restart, expect `Synced n/m` lines and `You are now synchronized with
the network`. Check `tor.service`, `monero-onion-env.service`, and
`monero.service` on `homelab-04`. Read the onion address from
`/var/lib/tor/onion/monero/hostname`; do not copy its private key. Use a Tor
client on a separate host to connect to the onion address on port 18084, then
check monerod's `get_info` response and logs for anonymous inbound activity.
If startup fails, check the three units' journals and the persistent Tor
directory before restarting monerod. The `monero-onion-env` unit deliberately
blocks monerod startup if Tor has not generated a hostname.
