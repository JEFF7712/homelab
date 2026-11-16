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
