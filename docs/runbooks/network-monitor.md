# Network outage monitor

The `network-monitor` Deployment in `observability` checks connectivity from a
cluster pod every five seconds (best effort, subject to probe and delivery
timeouts). It posts outage and recovery notifications to the
existing ntfy `homelab-alerts` topic. Subscribe to that topic with the existing
ntfy account to receive them.

Checks cover the router's TCP DNS listener at `10.0.30.1:53`, an actual DNS
lookup through that router, public TCP connectivity to Cloudflare and Google,
and certificate-verified HTTPS to their public DNS addresses. Either public
endpoint succeeding is sufficient for its layer. A single provider failing
does not declare an Internet outage.

An observed continuous failure lasting 60 seconds confirms an outage.
Thirty seconds of continuous healthy observations confirms recovery. These
thresholds use elapsed time, independently of the five-second sampling rate.
Messages identify failed layers and recovery includes the observed duration.
These checks identify symptoms from the cluster, not a proven upstream cause.

State and pending notifications persist on `network-monitor-state`. Failed
ntfy deliveries retry each cycle, in order. Delivery is at least once: a crash
after ntfy accepts a message but before state is saved can repeat it.
ntfy retains accepted messages according to its configured cache policy.

Local ntfy delivery can be delayed by WAN failure. Independently, a healthy
cycle sends a POST heartbeat no more than once every 30 seconds to hosted
Healthchecks.io. Any failed layer suppresses a heartbeat when it is due.
Recovery does not trigger extra pings. A cluster, NAS, or monitor failure also
stops heartbeats, so an external alert indicates lost monitoring or connectivity,
not a proven ISP failure. A heartbeat delivery failure is logged without its URL
and does not interrupt local probing or ntfy delivery.

The Healthchecks check uses a Simple schedule with a one-minute Period and a
one-minute Grace Time. With heartbeats every 30 seconds, a sustained outage
becomes Down approximately 90 to 120 seconds after it begins, plus service
notification latency. Shorter outages may not trigger an external alert.
The next successful heartbeat restores Up status. Notification destinations
must be external to the homelab, such as email received over cellular data.

The ping URL is the `url` key of the `network-monitor-heartbeat` Secret in
`observability`, owned by `gitops/secrets/network-monitor-heartbeat.sops.yaml`.
Keep it encrypted with the repository's existing SOPS age recipient. Mount it
at `/heartbeat/url`; do not log it or put it in a ConfigMap.

Inspect the monitor:

```sh
kubectl -n observability logs deployment/network-monitor --tail=20
kubectl -n observability exec deployment/network-monitor -- cat /state/state.json
```

Read retained history without relying on container log retention:

```sh
kubectl -n observability exec deployment/network-monitor -- python /config/monitor.py --history
```

The `/state/history/` directory holds rotating JSONL chunks up to 1 MiB each,
with a 16 MiB total cap and 30-day retention. Oldest chunks are removed first;
retention can be shorter under heavy failure activity. Chunk age is measured
from its first record. Healthy samples remain in container logs only.

Each failed individual probe cycle produces a `sample` record with its timestamp
and all probe results. A single provider failure is recorded even when its
layer remains healthy. Consecutive layer failures form an event stored as
`brief_event` in the state file while open. The first healthy cycle closes it
with an `outage` history record: `start`, `last_failure`, `end`, `failed_layers`,
`duration_seconds`, `observed_span_seconds`, and `observation_gaps`. All times
are Unix seconds in UTC. Each brief event is recorded even when no alert fires.

Restarts, clock reversal, or more than 15 seconds between samples produce a
`gap` record. Open events survive restarts, but their duration is then null;
the span is not confirmed continuous downtime. Alert confirmation timers reset
across gaps. An already-alerted outage remains open without duplicate alerts,
and its recovery message labels the duration uncertain. Sampling can miss
interruptions shorter than five seconds; timeouts can widen that window.

History is flushed before atomic state updates. A crash between those writes
can duplicate a history record, so consumers should deduplicate by record type
and timestamps. History chunks contain no notification tokens or heartbeat URL.

Logs contain each cycle's individual results. The state file contains the
current outage start, affected layers, and pending notifications. No public
endpoint or credentials are exposed by this Deployment.

Verification uses simulated probe outcomes in `tests/test_network_monitor.py`.
For live acceptance, inspect a real cycle, then use an isolated copy of the
monitor's state and a controlled failing probe to verify outage and recovery
messages. Do not interrupt the router or the production monitor to test it.
For external acceptance during initial setup, send a heartbeat from the homelab,
allow it to expire before enabling the production sender, and verify both Down
and Up notifications in Healthchecks and at the chosen destination. After
deployment, verify the check's Last Ping keeps advancing at 30-second intervals.
Rollback by reverting the monitor's GitOps commit; retain the PVC until any
pending messages and outage evidence have been inspected.
