# OPNsense BGP Proof

Run after an FRR configuration apply and before migrating application traffic.

For a dedicated agent API identity restricted to BGP diagnostic reads, use
the [read-only agent access runbook](opnsense-agent-access.md).

1. Confirm FRR service status is running.
2. Confirm every Cilium peer is established with the expected ASN.
3. Confirm the canary Cilium LoadBalancer `/32` is present in the IPv4 BGP RIB.
4. Prove the data plane: `python -m opnsense_reconciler.dataplane --targets opnsense_reconciler/lb-data-plane-targets.json`
   (also wired as the `opnsense_dataplane` CI job). The RIB check alone is not
   sufficient: in September 2026 FRR held the canary `/32` in its BGP table while
   the route was missing from the kernel FIB, so every LoadBalancer VIP was
   unreachable from the LAN until the routing service was restarted.
5. Retain redacted proof output with the pipeline artifact.

## VIPs time out while BGP is up

FRR can keep the VIP `/32`s in its BGP RIB without installing them in the
kernel FIB. OPNsense then sends VIP traffic to the empty `load_balancers`
VLAN (its connected `10.0.40.0/24`), so every VIP except the statically
routed Jellyfin `10.0.40.16` times out from the LAN and from CI. It happened
in September 2026 and again on 2026-11-02, after an FRR restart.

Signs: `opnsense-plan`'s data-plane probe reports `TimeoutError` for the
VIPs while the Cilium peers are established. `just status network` reports
the routes present but not installed, because it compares
`/api/diagnostics/interface/get_routes` with
`/api/quagga/diagnostics/search_bgproute4`. Agent VMs run it with their
read-only key (`~/.config/sops/age/agent-rupan.key`).

Fix: `just ship --resume <main> --deploy opnsense-frr-heal`. The operation
(`scripts/ci/opnsense_frr_heal.py`) acts only on this exact condition:
every peer established and every expected route in FRR, with some missing
from the kernel. It confirms the dropout again after a minute, restarts FRR
only when every peer session is older than six hours (sessions reset on any
FRR restart, so this bounds it to one restart per six hours with no stored
state), and passes only when every expected `/32` is installed within three
minutes. A hold or a failed heal exits nonzero and needs a human: restart
FRR in the OPNsense UI (Services, FRR) and rerun `opnsense-plan`.
`opnsense-fib-check` runs the same detection read-only and fails with the
command to run.

An absent peer, wrong ASN, missing route, or non-running service is an apply failure. Restore connectivity using the OPNsense console recovery runbook before attempting another configuration change.
