# Host journal shipping

The k3s nodes ship their systemd journals to Loki through the Alloy DaemonSet
(`gitops/observability/alloy/release.yaml`). The two fleet hosts that are not
k3s nodes, `nas-01` and `adguard-netbird-01`, run Alloy as a NixOS service
from `flake/modules/host-log-shipping.nix` (`homelab.logShipping.enable`). The
pipeline uses the same component names and labels as the DaemonSet
(`component="system-journal"`, `hostname`, `unit`, `level`, `transport`), so
the journal alert rules in `gitops/observability/loki/rules-configmap.yaml`
(`ZfsPoolIoError`, `HostStorageIoError`, `HostKernelOOMKilled`, SSH and sudo
rules) cover both hosts unchanged. `hostname` is the journal's `_HOSTNAME`.

## Path to Loki

- Hosts push to `http://10.0.40.17/loki/api/v1/push`, the `loki-push`
  LoadBalancer Service (`gitops/observability/loki/push-service.yaml`) in front
  of the chart's nginx gateway. It uses `externalTrafficPolicy: Local` so the
  client address survives, and the gateway is pinned to control-plane nodes
  (the only BGP peers) in `loki/release.yaml`.
- `loki/push-policy.yaml` (CiliumNetworkPolicy) admits the observability
  namespace (loki-canary) and, from outside the cluster, only `10.0.30.20/32`
  and `10.0.30.10/32`, through Cilium's HTTP proxy for `POST
  /loki/api/v1/push` only. Other paths from those hosts get HTTP 403; other
  sources are dropped. There is no push credential: the source allowlist and
  the push-only rule are the control, and Loki runs with `auth_enabled: false`.
- OPNsense already passes infrastructure to the VIP range ("Allow
  infrastructure to Kubernetes service VIPs"). Hosts have no nftables egress
  filter; the unit itself may connect only to loopback and the VIP
  (`IPAddressAllow`).
- A VIP that times out while BGP is established is usually the OPNsense FRR
  FIB dropout, not this policy: see [OPNsense BGP proof](opnsense-bgp-proof.md)
  and `just ship --deploy opnsense-frr-heal`.

## Service

The unit runs as the static `alloy` user in the `systemd-journal` group with
systemd sandboxing, memory limits (`memoryHigh`, `memoryMax`, `goMemLimit`
options; tighter on adguard-netbird-01) and a 25% CPU quota. Journal read
positions live in `/var/lib/alloy/data`, persisted to `/persist`, so a restart
resumes instead of re-sending history. On a first start Alloy reads at most the
last 7 hours.

A unit that floods Loki can be dropped on the host with
`homelab.logShipping.dropUnits = [ "<unit>.service" ];`. Never drop the kernel,
ZFS, smartd, sshd or authentication entries the alert rules match.

## Checks

```sh
nix develop ./flake -c python -m unittest tests.test_host_log_shipping -v
```

Live, with a read-only port-forward to `svc/loki` (3100):

```logql
sum by (hostname) (bytes_over_time({component="system-journal", hostname=~"nas-01|adguard-netbird-01"}[1h]))
```

The fleet deploy step log shows `alloy.service` active; no failed units may be
left behind, because a failed unit fails the next NixOS deploy.
