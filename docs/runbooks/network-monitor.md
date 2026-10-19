# Network outage monitor

The `network-monitor` Deployment in `observability` checks connectivity from a
cluster pod every 30 seconds. It posts outage and recovery notifications to the
existing ntfy `homelab-alerts` topic. Subscribe to that topic with the existing
ntfy account to receive them.

Checks cover the router's TCP DNS listener at `10.0.30.1:53`, an actual DNS
lookup through that router, public TCP connectivity to Cloudflare and Google,
and certificate-verified HTTPS to their public DNS addresses. Either public
endpoint succeeding is sufficient for its layer. A single provider failing
does not declare an Internet outage.

Three consecutive failed cycles confirm an outage, approximately 60 to 90
seconds after it starts. Two completely healthy cycles confirm recovery.
Messages identify failed layers and recovery includes the observed duration.
These checks identify symptoms from the cluster, not a proven upstream cause.

State and pending notifications persist on `network-monitor-state`. Failed
ntfy deliveries retry each cycle, in order. Delivery is at least once: a crash
after ntfy accepts a message but before state is saved can repeat it.
ntfy retains accepted messages according to its configured cache policy.

This is local monitoring. WAN failure prevents remote delivery until the WAN
returns. A failed cluster, NAS, or monitor cannot report its own outage. An
external missed-heartbeat monitor is needed for independent outage paging.

Inspect the monitor:

```sh
kubectl -n observability logs deployment/network-monitor --tail=20
kubectl -n observability exec deployment/network-monitor -- cat /state/state.json
```

Logs contain each cycle's individual results. The state file contains the
current outage start, affected layers, and pending notifications. No public
endpoint or credentials are exposed by this Deployment.

Verification uses simulated probe outcomes in `tests/test_network_monitor.py`.
For live acceptance, inspect a real cycle, then use an isolated copy of the
monitor's state and a controlled failing probe to verify outage and recovery
messages. Do not interrupt the router or the production monitor to test it.
Rollback by reverting the monitor's GitOps commit; retain the PVC until any
pending messages and outage evidence have been inspected.
