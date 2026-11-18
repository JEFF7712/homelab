# Prometheus memory

Owner: `gitops/observability/kube-prometheus-stack/release.yaml`.

k3s exposes control-plane metrics on both the kubelet `/metrics` endpoint
and the API server endpoint. The kubelet ServiceMonitor drops metric names
matching `(apiserver|etcd|scheduler)_.*`; the apiserver job retains control-plane
metrics. This rule applies only to the main kubelet endpoint, leaving cAdvisor
container metrics and probes intact. Keep retention and scrape intervals unchanged.

Before deployment, confirm the apiserver job contains each dropped metric family
needed by alerts and dashboards. After reconciliation, verify kubelet targets stay
up, control-plane series are absent from the kubelet job, and API-server and
container metrics remain available. Existing head series age out through normal
compaction; an immediate RAM drop is not required for successful deployment.

Rollback: remove `kubelet.serviceMonitor.metricRelabelings` and ship through Flux.
No stored history needs deletion. Measure RAM after several hours of normal load
before considering lower requests or limits.

## Requests and limits

The Prometheus container requests 1536Mi and is limited to 3Gi (raised from
1Gi and 2Gi on 2026-11-18). Measured over the 7 days before the change, from
`container_memory_working_set_bytes{container="prometheus"}`:

| Measure | Value |
| --- | --- |
| Peak (raw samples) | 2,046 MiB, 99.9% of the old 2Gi limit (2026-11-11, 8 min after a restart) |
| 99th percentile (1 min) | 1,842 MiB |
| Median | 1,211 MiB |
| Peak since etcd scraping (2026-11-16 on) | 1,945 MiB (p99 1,836, median 1,193) |
| `prometheus_tsdb_head_series` | about 215k to 350k typical; 465k just after the 2026-11-18 restart |

There were no OOM kills (`container_oom_events_total` 0, no `OOMKilled`
termination), but the pod started 17 times in those 7 days, mostly from fleet
drains of homelab-05. The highest peaks come in the first hour after a start:
WAL replay plus a head inflated by the series that the drain itself churned.
The etcd scrape added 112 series, which does not change the picture.

3Gi puts the worst observed peak at 67% of the limit, about 1 GiB of headroom.
The 1536Mi request sits above the median so the scheduler accounts for the
steady state; the p99 stays inside the limit, not the request.

homelab-05 absorbs it: 12.9 GiB allocatable (the host has 31 GiB, with the
rest reserved for the Omarchy VM and system), pod requests were 5,250 MiB
(40%) before the change and about 5,760 MiB (45%) after, and the pods' working
set was 4.6 GiB. Limits are overcommitted at about 157%, as before (149%), but
host `MemAvailable` never fell below 14.9 GiB in the same 7 days, far above
the 500Mi eviction threshold. If usage approaches 3Gi, reduce series
(relabel drops, as above) or retention before raising the limit again.

To remeasure, port-forward `svc/kube-prometheus-stack-prometheus` and query
`max_over_time(container_memory_working_set_bytes{namespace="observability",container="prometheus"}[7d])`
and `quantile_over_time(0.99, max(container_memory_working_set_bytes{namespace="observability",container="prometheus"})[7d:1m])`.
