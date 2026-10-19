# Registry node credential rotation

## Status and authorization

The 2026-10-17 projectctl preflight verified that the retired migration script's
committed node password matched the live Zot bcrypt entry. Verification
ran htpasswd in read-only verification mode on nas-01, without a registry API
request or printing the historical password. Source removal did not revoke it.

The operator subsequently approved the coordinated node rotation, its CI/image
pull maintenance window, supported Woodpecker settings updates and rolling K3s
reloads. The operator also explicitly approved canceling queued pipelines after
running jobs finished. Five homelab pipelines (2252, 2258, 2262, 2263 and 2267)
were canceled without replaying their deployment tasks. The remaining registry
import finished before Zot restarted.

The replacement is encrypted in secrets/registry-node.sops.json with the same
two operator recovery recipients as projectctl.sops.json. Protected host backups
live in unique /persist/registry-node-backup-* directories, mode 0700, with
mode 0600 backup files. Local ignored receipts and encrypted CI recovery payloads
are under .agent-state/registry-node-rotation in the rotation worktree.

Zot, all five K3s nodes, the NAS pull client, thirteen repository registry
entries and both protected homelab CI secrets now use the replacement. Live
HTTPS checks reject the retired password with 401 and deny node uploads and
deletes with 403. Other Zot account entries and the complete ACL were preserved.
Both persistent and runtime K3s files match their reviewed replacement hashes.
All five CRI pulls and the NAS Podman pull succeeded. All five nodes are Ready;
control-plane etcd readiness passed after sequential reloads. No NixOS generation
was changed. CI scheduling resumed, with fresh validation and protected registry
checks tracked in the local receipts.

The separately approved projectctl-pull reader was installed and live-verified.
It is independent of node and must retain its existing password and two exact
read-only image grants. Zot-only reader approval does not authorize this node
rotation, cluster reloads, or CI credential changes.

## Observed consumers

Repeat this metadata inventory immediately before a rotation. Never select
password or secret-value columns from service databases.

| Consumer | Protected material | Required update |
| --- | --- | --- |
| nas-01 Zot | /persist/zot/htpasswd | Replace only the node bcrypt entry; preserve all other entries and root:zot mode 0640 |
| homelab-01 through homelab-05 | /persist/secrets/k3s-registries.yaml | Replace only the registry.rupan.dev auth password; retain mirrors, rewrites, endpoints and username node |
| homelab-01 through homelab-05 | /etc/rancher/k3s/registries.yaml | Runtime copy loaded from the protected source during K3s startup |
| nas-01 pull client | /persist/secrets/registry-auth.json | Replace only the node auth value for registry.rupan.dev |
| Woodpecker homelab repository | registry_node_password_file | Replace the protected file-secret value through the trusted operator UI |
| Woodpecker homelab repository | registry_htpasswd_file | Refresh the protected bcrypt file from the accepted Zot account set, including projectctl-pull |
| Woodpecker repository registries | Thirteen active node entries below | Replace only registry.rupan.dev passwords through supported settings operations |

The observed Woodpecker repository IDs are 1, 2, 23, 24, 25, 26, 27, 28, 29, 31,
33, 34 and 35. Their names are JEFF7712/homelab, JEFF7712/darkbit-site,
JEFF7712/autocompress, JEFF7712/resume-agent, JEFF7712/cctop,
JEFF7712/roku-bulb-local, JEFF7712/predmarkbot,
JEFF7712/bayesian-reaction-optimization-engine, JEFF7712/musicthing,
JEFF7712/spotify-clone-temp-name, JEFF7712/neuralnet-in-c, JEFF7712/NaviSync
and JEFF7712/md-engine. Re-read current metadata to confirm this set. Registry
and secret values stay outside task records, tool output and Git.

## Reviewed execution sequence

1. Obtain explicit authorization for the complete rotation scope and a CI/image
   pull maintenance window. Pause new CI work, wait for running pipelines to
   finish, verify all five K3s nodes are Ready, and record etcd health. Keep
   the existing source deployment workflow paused until protected CI secrets
   match the accepted live account file.
2. Generate a fresh random node password on the trusted operator machine and
   retain it through SOPS and the password manager. Keep it separate from all
   projectctl credentials. Prepare bcrypt, node YAML and Docker auth payloads
   in memory or protected operator storage. Never place plaintext in the Nix
   store, command arguments, logs or Git.
3. Create unique root-only backups of each affected host file and record
   ownership, modes and content hashes. Preserve every unrelated registry
   account, ACL rule, mirror and authentication entry. Arrange an independent
   operator recovery copy of the affected CI settings before changing them.
4. In the trusted Woodpecker UI, update the thirteen repository registry entries
   and the two protected homelab secrets. Keep CI paused during this staged
   mismatch. Use supported settings operations. Do not edit the service database,
   derive a token from database material, or give an agent an administrator PAT.
   Existing automation credentials authorize only the projectctl pilot, so these
   wider CI changes require trusted operator access.
5. Atomically install the prepared persistent K3s registry files and NAS client
   auth file, retaining their original restrictive ownership and permissions.
   Inspect each live K3s unit before assuming the current source's LoadCredential
   and preStart behavior is deployed. During this rotation, the first deployed
   control-plane unit lacked that behavior. Back up and explicitly replace the
   runtime /etc/rancher/k3s/registries.yaml file when necessary, using the same
   reviewed payload and a hash precondition. Verify persistent and runtime hashes
   agree after every reload. Do not activate a NixOS upgrade to bridge this gap.
   Atomically replace only Zot's node bcrypt entry, then restart Zot. Confirm
   the new credential works and anonymous access remains denied before moving
   on. Pulls using the old in-memory K3s credentials can fail until reload.
6. Reload K3s on one node at a time, control-plane members sequentially, using
   the owning cluster maintenance procedure. Check readiness and etcd quorum
   after each control-plane member, and preserve workload disruption budgets.
   Stop on a degraded member. Do not combine this credential change with a
   NixOS upgrade, image update or unrelated source reconciliation.
7. Verify authenticated image pulls from each node and the NAS client, a fresh
   CI clone/validation pipeline, the homelab registry checks, and projectctl-pull
   allowed reads and denied writes. Confirm the historical password fails its
   bcrypt verification and cannot authenticate to the registry. Confirm the
   node identity still cannot upload or delete images. Resume CI only after
   the protected settings and live consumers agree.
8. Record sanitized verification receipts and encrypted recovery material.
   Search history and retained logs for exposure without printing matching
   values. Revocation comes first; a history rewrite requires separate review.

## Recovery

Keep CI paused on any failed cutover. Restore only the affected files from
their checked root-only backups and reload only the services that consumed
them. Preserve concurrent registry changes by comparing installed hashes before
restoring a backup. Restoring the old node verifier re-enables an exposed
credential, so treat that as temporary recovery and require a new rotation
before worker activation. Check etcd quorum and node readiness before continuing
a rolling reload. Keep operator recovery access independent of registry pulls.

See the [registry operator procedure](local-registry.md) and
[projectctl runbook](projectctl.md).

## Observed Woodpecker UI compatibility issue

The deployed hardening patch rejects arbitrary pipeline variables. Woodpecker
3.18's deployment dialog also sends an empty deploy_task query parameter, which
the upstream handler does not classify as reserved. Together these produce an
empty HTTP 400 response before policy evaluation, and the dialog can remain in
its loading state after the failure.

For the two read-only registry checks during this maintenance window, the
dedicated browser session removed only the empty deploy_task parameter for the
exact approved parent pipeline and operation names. The application retained
its normal authenticated request and all server policy checks. No session
credential was extracted. Closing the browser discards that temporary request
correction. A permanent server/UI compatibility fix is separate work; preserve
the prohibition on arbitrary pipeline variables.

Concurrent main changes can independently invalidate an already approved
operation. The first consistency check, pipeline 2271, stopped at the runtime
current-main guard after another task merged commit 82d51a65d11a. Its failure
was verified from the named source guard without exposing CI logs or secrets.
Use fresh successful main validation for retries; do not weaken that guard.

## Importer alignment after node rotation

The first protected consistency check, pipeline 2275, exposed an existing
importer password mismatch. The importer Zot entry was byte-identical before
and after node rotation. The operator separately approved rotating only that
entry, updating registry_importer_auth_file and registry_htpasswd_file, pausing
CI, canceling queued importer work after active work ended, restarting Zot and
rerunning both protected checks.

The replacement is encrypted in secrets/registry-importer.sops.json with the
same two recovery recipients. After active work drained, queued importer
pipeline 2365 was canceled. Both secrets were saved through the authenticated
operator UI, and their IDs, repository ownership, event filters, plugin image
filters and notes were independently verified unchanged. No service database
credential values were read or written. The checked root-only Zot backup is
/persist/zot/registry-importer-backup-n589gm3e/htpasswd on nas-01.

Only the importer account entry changed. Every other account entry and the
complete ACL were preserved. HTTPS verification wrote the exact existing clone
manifest back to its digest and confirmed identical bytes afterward. Application
writes and importer deletes returned 403. Node and projectctl-pull reads still
worked, their writes and deletes remained denied, and unrelated reader access
remained denied. CI scheduling resumed after these checks passed.

Fresh validation 2366 and consistency check 2369 passed on commit 4858b409ee7a.
Drift check 2370 found one Spotify Matrix retention tag mismatch against that
older lock; 118 other image records verified. Current main subsequently replaced
that application record. Read-only verification confirmed its new digest and
both required tags. Fresh protected checks must use the current lock and a new
successful validation parent. Do not retag application images to satisfy an
obsolete lock or treat credential alignment as successful drift verification.

On 2026-10-18, main validation 2537 and protected consistency/drift checks 2539
and 2540 passed for 9b48677c24d24f65f5139043ca82a5b35b6e5ff2. This completed
the node/importer rotation verification. The subsequent projectctl trial restored
its prior host generation after an unrelated identity-verifier serialization
error; the registry credentials and ACL were unaffected, and CI resumed.

The htpasswd-file fields in rotation bundles are snapshots of their respective
cutovers. Never replay an older complete account file after another identity has
rotated. Discover the current account file, preserve unrelated entries, and use
checked hash preconditions for every replacement.

Any retained GitLab DR importer credential must be synchronized before DR use.
The legacy pipeline is DR-only. Its live activation state and retained credential
values were not inspected or changed during this rotation.
