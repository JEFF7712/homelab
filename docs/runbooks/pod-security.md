# Pod Security

Every namespace in `gitops/` declares `pod-security.kubernetes.io/enforce`.
`tests/test_pod_security.py` fails when a namespace has no level or when
`privileged` appears outside the reviewed list below. The Flux bootstrap
namespace gets its label from a patch in
`gitops/clusters/homelab-01/flux-system/kustomization.yaml`, so a Flux upgrade
that regenerates `gotk-components.yaml` keeps it. `kube-system` and the other
k3s-created namespaces are not managed here.

Enforcement applies when a pod is created. A running pod that violates a newly
enforced level keeps running; its next rollout, restart or CronJob run is
rejected, and the ReplicaSet or Job reports `FailedCreate` with
`violates PodSecurity`.

## Levels (2026-10-20)

Levels were chosen by server-side dry-running every live Deployment,
StatefulSet, DaemonSet, CronJob and Job pod template into probe namespaces
enforcing `restricted` and `baseline`, so CronJobs with no running pod were
covered too.

- `restricted`: every other namespace.
- `baseline`:
  - `flux-system`: Reloader lacks `allowPrivilegeEscalation: false` and dropped capabilities.
  - `immich`: the server and machine-learning images run as root.
  - `obsidian`: CouchDB runs as root.
  - `storage`: the NFS provisioner chart sets no security context.
- `privileged`:
  - `automation`: GitHub Actions runners use privileged Docker-in-Docker with hostPath storage.
  - `home-assistant`: host networking, D-Bus hostPath and host ports for HomeKit and discovery.
  - `media`: Gluetun needs `NET_ADMIN`; Jellyfin mounts `/dev/dri`.
  - `music-assistant`: host networking for Spotify Connect and LedFx audio.
  - `observability`: node-exporter (host PID and network) and Alloy log hostPaths.
  - `voice`: satellite host networking and model hostPaths.

## Changing a level

Before tightening, dry-run the workloads the same way: create a throwaway
namespace enforcing the target level, `kubectl create --dry-run=server` each
pod template into it (dropping `serviceAccountName`), and delete the namespace.
Fix every violation in the owning manifest first. Loosening to `privileged`
also means updating `PRIVILEGED` in the test, with the reason recorded here.
