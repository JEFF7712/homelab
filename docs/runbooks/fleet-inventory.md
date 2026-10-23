# Shared fleet inventory

`inventory/hosts.nix` owns the seven fleet host identities, primary IPv4
addresses, primary networks, k3s membership, deployment types, and deployment
order. `inventory/networks.nix`
owns VLANs, gateway addresses, CIDRs, and DHCP network settings.
`inventory/default.nix` validates these declarations without importing NixOS
or Tofunix. Types are shared with the firewall inventory.

`inventory/dns.nix` owns static DNS records. Fleet destinations reference host
keys; external addresses and service VIPs use explicit addresses. Independent
`adguardOrder` and `unboundOrder` values select each resolver view and preserve
its ordering. Optional `coredns = { server = "registry"; order = 1; };`
selects a record for one of the two existing Kubernetes CoreDNS server blocks.
CoreDNS ordering is independent of the other resolver views. Enabled AdGuard
and CoreDNS records must also appear in Unbound. Disabled records are omitted
from CoreDNS. The two
Unbound-only homelab VIP records remain outside the AdGuard rewrite view.
`just opnsense-render` generates `inventory/adguard-rewrites.json` and
`opnsense_reconciler/unbound-host-overrides.json`, plus the exact CoreDNS
manifest at `gitops/platform/coredns-custom.yaml`; never edit these outputs.
The narrow renderer in `inventory/coredns.nix` preserves cache, fallthrough and
forwarding settings. CI rejects any difference in the generated CoreDNS text,
including plugin changes. Deliver actual manifest changes through Flux.
After delivery, verify the live ConfigMap and resolve all five configured names
from a pod using `ClusterFirst` DNS; host DNS alone does not verify CoreDNS.
NixOS consumes the same evaluated rewrites directly. AdGuard and Mosquitto
listen addresses and the Zigbee2MQTT broker endpoint follow the appliance host
address; service ports remain in their owning modules.

The flake reads the inventory to select canonical hosts and registry variants.
Host profiles derive k3s node addresses and join endpoints from it. Network
modules derive their primary addresses, gateways, DNS resolver addresses,
registry host mapping, and the Cilium BGP peer address. Tofunix uses the same
network declarations, static host addresses, and k3s membership for firewall
policy and the matrix sender alias. Topology continues to read evaluated
NixOS configuration and generated OPNsense assignments.
The fleet deployer, deployment-report and agent-status tools read generated
`inventory/fleet.json`, preserving their existing ordering and health-check
roles. CI checks this file against the Nix declarations alongside the firewall
outputs; do not edit it directly.
The generated file also exposes cluster membership and the Kubernetes API
endpoint, so discovery does not depend on Nix source syntax.
Deployment types select the existing drain and health-check behavior; control
plane roles remain in host profiles. Deployment order keeps the server profiles
ahead of the agent profiles to avoid kubelet/API-server version skew.

## Change a fleet address

1. Edit the host declaration in `inventory/hosts.nix`. Network changes belong
   in `inventory/networks.nix`. Hardware interface names and service-specific
   configuration stay in the owning NixOS profiles and modules.
2. Run `just opnsense-render` and review any generated firewall changes.
3. Run `just check-changed` and `just fmt-check`. Shared inventory changes
   select the full validation gate. The flake's `fleet-inventory` check verifies
   host wiring, address membership, uniqueness, bootstrap membership, alias
   propagation, DNS view parity and ordering, service binding propagation, and
   rejection of invalid declarations.
4. Deliver with `just ship`. Inventory-only changes select both fleet and
   OPNsense operations, including their dry-run and plan prerequisites.
   Review the evaluated host changes and saved firewall plan before accepting
   changes to connectivity. See [host deployment](nixos-host-deployment.md)
   and [OPNsense recovery](opnsense-recovery.md).

Primary addresses are centralized. Secondary overlay addresses, hardware
interfaces, other application URLs, GitOps manifests, and reconciler inputs such
BGP neighbor definitions retain their existing owners. Adding a physical host
also requires its hardware/profile files; inventory membership alone cannot
provision hardware.

## Refactors without runtime changes

Capture the toplevel derivation of every canonical and deployment-variant
configuration before editing, then compare after the refactor. Matching
derivations prove that the evaluated systems did not change. Compare generated
OpenTofu values and interface assignments as well. If all match, ship the source
with `--deploy opnsense-plan` to obtain current protected plan evidence without
activating unchanged systems or applying unrelated existing drift.

The CI host captures a repository authority snapshot in its system derivation.
Source edits can therefore change homelab-04's policy bundle path and generated
package digest even when the policy logic, contracts, and network settings are
unchanged. Trace those derivation differences explicitly and distinguish this
metadata from a runtime configuration change. Do not activate unrelated policy
updates merely to deliver an inventory refactor.

For DNS refactors, compare evaluated AdGuard settings, Mosquitto command and
Zigbee2MQTT endpoint, the appliance derivation, and generated Unbound records
including order and descriptions. Query both resolver addresses before and
after delivery and compare observed answer sets. Unbound can return multiple
addresses for its own router hostname; preserve the baseline answer set.
