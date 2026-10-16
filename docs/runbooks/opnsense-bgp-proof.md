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

An absent peer, wrong ASN, missing route, or non-running service is an apply failure. Restore connectivity using the OPNsense console recovery runbook before attempting another configuration change.
