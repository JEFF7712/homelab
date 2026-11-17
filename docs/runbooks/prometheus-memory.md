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
