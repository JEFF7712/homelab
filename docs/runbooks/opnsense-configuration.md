# OPNsense configuration

Edit `inventory/networks.nix` for shared networks and `inventory/hosts.nix` for
static fleet hosts. Edit `tofu/opnsense/inventory.nix` for DHCP devices, aliases,
firewall rules, and port forwards. `options.nix` defines the typed domain interface and
assertions. `resources.nix` projects it into provider resources using Tofunix,
pinned in `flake/flake.lock`.

## Change a network or device

1. Edit the owning inventory. Networks declare their VLAN, gateway, CIDR, OPNsense
   interface name, and optional DHCP settings. Devices reference a DHCP network
   and declare their address, MAC, hostname, and description. A host alias can
   reference DHCP device keys through `devices` or static fleet keys through
   `hosts`, so addresses follow their authoritative declarations. See the
   [shared fleet inventory](fleet-inventory.md) for NixOS consumers and routing.
2. Run `just opnsense-render`. It generates `tofu/opnsense/main.tf.json` and
   `opnsense_reconciler/assignments.json`, plus the deployer's
   `inventory/fleet.json`, `inventory/adguard-rewrites.json`, and
   `opnsense_reconciler/unbound-host-overrides.json`, and the CoreDNS manifest
   `gitops/platform/coredns-custom.yaml`. It also renders the Forgejo, Woodpecker
   and Approvals Service/Endpoints pairs under `gitops/cloudflare/` from
   `inventory/service-manifests.nix`. Static DNS records belong
   in `inventory/dns.nix`. Commit changed outputs with the source.
3. Run `just opnsense-check`, `just check-changed`, and `just fmt-check`.
4. Deliver through `just ship`. Inspect the protected refresh-only and desired
   plans before accepting a network change. Follow
   [recovery](opnsense-recovery.md) for changes that affect connectivity.

Never edit generated outputs directly. CI rejects semantic differences in JSON
and exact text differences in generated Kubernetes manifests from the
Nix source. The existing topology renderer reads the generated interface
assignments, so VLANs and gateway addresses have one authoritative declaration.
Other reconciler inputs, such as BGP and agent access, retain
their existing owners.

Rendering validates IPv4 networks, gateway and device membership, overlapping
subnets, DHCP pool bounds, duplicate device identities and VLAN tags, alias
device references, and rule sequence collisions. Static reservations inside
dynamic DHCP pools remain supported, matching existing Kea behavior.

## Load-balancer VIP next hop and static routes

Static routes and their gateways are repository-owned and declared in
`inventory/static-routes.nix`. They are reconciled via `opnsense_reconciler`:
- `10.0.40.16/32` via `10.0.30.14` (`homelab-04`, interface `opt3`): routes
  Jellyfin directly to its pinned backend node. This bypasses the node-to-node
  SNAT issue where Cilium forward paths from `homelab-01` translate source IPs
  to the node router address (`10.42.0.x`), which the media ingress NetworkPolicy
  drops.
- `100.64.0.0/10` via `10.0.30.10` (`adguard-netbird-01`, interface `opt3`):
  NetBird mesh return path so LAN replies reach tunnel clients directly.

These routes survive reboots and filter reloads and are managed by CI deployment
operation `opnsense-apply`.

## Provider updates

`tofu/opnsense/generated/provider.nix` contains provider options generated from
the locked OPNsense provider schema. Agents can inspect it to discover accepted
arguments. After updating the provider pin, lock, and provider mirror, run
`just opnsense-provider-options`, then render and validate again. The OpenTofu
check regenerates the options in isolation and rejects stale options.

Checked-in options keep normal module evaluation independent of provider
downloads and import-from-derivation builds. Generation uses the pinned
Tofunix code, Nix formatter, and provider mirror. Provider schemas cannot express
every provider constraint, and deferred Terraform expressions bypass literal
type checks, so OpenTofu validation and protected plans remain required.

## Execution and ownership

NAT port-forward protocols use the API's lowercase keys, such as `tcp` and
`udp`. The pinned provider writes and reads these selected values directly;
uppercase display labels otherwise produce recurring case-only updates after
refresh. The domain type rejects uppercase NAT protocols. Firewall filter
protocol declarations retain their existing provider representation.

Tofunix generates configuration only. The existing HTTP backend, provider lock,
import declarations, `managed` resource names and instance keys, protected CI
plan/apply commands, and reconciler execution remain authoritative.

Credentials stay in protected runtime environment variables and file secrets.
Never put secret values into the inventory, generated options, or Nix arguments:
evaluated configuration and rendered JSON enter the Nix store.

For the initial adoption, compare all 67 resource instances and interface
assignments against the prior configuration, then run the protected
`opnsense-plan` operation. A no-change plan accepts the migration without an
apply or fleet activation. Unexpected drift requires comparison with the prior
source before any apply.
