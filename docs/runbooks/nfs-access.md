# NAS NFS access

`flake/modules/nas-data.nix` renders exports from the fleet inventory. Only the
five hosts marked `k3s = true` may mount `/tank/media` and `/tank/cluster` writable.
Management retains read-only media access with root squashing. Adding an unrelated
machine to the infrastructure VLAN does not grant storage access.

`gitops/platform/nfs-client-isolation.yaml` also denies pod endpoints direct TCP
and UDP access to RPC/NFS ports 111 and 2049. AUTH_SYS client identifiers can be
forged by application code, so host export restrictions alone would not contain
a pod whose traffic is masqueraded by its node. The Cilium deny has precedence
over namespace allow rules and does not enable a new default-deny mode. Kubelet
NFS mounts use the host client and remain available through PVCs. Host-networked
services and node administrators remain inside the trusted-node boundary.

The application exports retain `no_root_squash` for these explicit Kubernetes
clients. This is a compatibility exception, not an authentication boundary:
AUTH_SYS trusts the client node's supplied UID. The provisioner creates directories
under root-owned `/tank/cluster`; existing Music Assistant and Ollama state includes
root-owned mode-0600 files, and media startup containers perform ownership changes.
Removing UID 0 access without migrating those consumers would break storage.
A future migration needs per-workload UID ownership and provisioner validation;
do not recursively chown live data or assume root squashing prevents UID spoofing.

`/tank/backups` has no NFS consumers and is not exported. Independent local
backups are pulled by the separate backup host over the existing read-only
SFTP interface; see [external backup recovery](external-backup-recovery.md).
Kubernetes clients cannot mount or write that backup storage through NFS.

After deployment, inspect `exportfs -v` on nas-01 and confirm all five Kubernetes
nodes retain their mounts and healthy workloads. Check a new PVC can provision,
mount and be removed using disposable data. From an ordinary pod, require direct
NFS connection attempts to time out while mounted-file reads and writes work.
Verify independent backup freshness.
If clients lose access, restore the prior export
configuration and reload exports; no data ownership or contents are changed by
this policy update.

## Media pool layout

`/tank/media` is a mergerfs union of `tank/media` (mounted at
`/mnt/media-tank`) and the single-disk `media2/media` and `media3/media` pools
(mounted at `/mnt/media-2tb` and `/mnt/media-3tb`). New files land on the
branch with the most free space; mergerfs keeps hardlinks and renames on the
source branch, so *arr imports still hardlink. The export carries a fixed `fsid` because FUSE has no stable device
id. A file lives on exactly one disk: losing a salvaged disk loses the media on
it, not the rest of the library. Operate on files through `/tank/media`; the
branch mounts are for diagnosis.

The 2 TB branch is no-create (`=NC`, `mediaNoCreateBranches`): its existing
files stay readable and writable, but new media goes to `tank/media` and the
3 TB disk, which keeps the healthier disk for the offline archive.
`media-extra-branches` sets the mode by rewriting the whole branch list, never
by removing and re-adding the branch.

## Offline archive

`media2/offline` (2 TB Seagate, mounted at `/tank/offline`) holds the Kiwix
library that `gitops/offline` serves at `http://kiwix.homelab` (10.0.40.18); see
[offline library](offline-library.md).
`offline-archive-dataset` creates the dataset on the running pool, and
`offline-archive-layout` creates `kiwix/` owned by uid 1000 before
`nfs-server` starts. The k3s nodes mount it writable and management read-only
(for copying it to a portable drive). It is excluded from restic because it
can be downloaded again; the `offline` sanoid template keeps three daily
snapshots.

To add a disk, create its single-disk pool, declare it in
`flake/hosts/nas-01/tank-config.nix`, and append its mount point to
`mediaExtraBranches` in `flake/modules/nas-data.nix`. The
`media-extra-branches` oneshot attaches it to the running union through
`/tank/media/.mergerfs`, before `nfs-server` at boot, with no downtime.

Do not change the `/tank/media` device or options: the switch then restarts
`tank-media.mount`, which fails while NFS clients hold it, and the deployment
and its rollback both fail (2026-11-19). If that is unavoidable, suspend the
parent `flux-system` Kustomization as well as `media` and `music-assistant`
(the parent reverts their `suspend`), scale consumers to zero, confirm no pod
mounts `media-library`, and only then unexport `/tank/media` and deploy.
