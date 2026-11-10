# K3s secret encryption and audit logs

`flake/modules/k3s-server.nix` enables API Secret encryption on the three
control-plane servers and writes metadata-only API audit records to
`/persist/k3s-audit/audit.log`. Logs exclude probe URLs and request/response
bodies, rotate at 50 MiB, retain at most five backups, and age out after seven
days. The directory is root-only. Agent nodes receive neither setting.

## First enablement on the existing HA cluster

This is a coordinated data migration. Do not activate the encryption flag on
an existing unencrypted cluster before the preparation step. Use the
[upstream existing-cluster procedure](https://docs.k3s.io/cli/secrets-encrypt#enable-secrets-encryption-on-an-existing-cluster),
supported by the pinned K3s version.

1. Verify all three control-plane nodes are Ready, an etcd snapshot has been
   completed, and the existing external Restic repository contains a verified matching
   recovery generation (snapshot, server token and manifest). Use the producer
   and streamed read-back procedure in [external backup recovery](external-backup-recovery.md).
   Record its immutable generation, snapshot identity and repository receipt. Never print the token or encryption keys.
2. On homelab-01, inspect `sudo k3s secrets-encrypt status`. If it reports no
   configuration file, run `sudo k3s secrets-encrypt enable` once. This prepares
   the shared encryption configuration without immediately encrypting data.
   If a configuration already exists, inspect the stage instead of resetting it.
3. Deliver this source through `just ship`. The fleet deployer activates the
   three servers serially with its existing readiness and recovery checks.
   Before rotation, all servers must report the `start` stage and matching
   server encryption hashes. Encryption is still disabled at this stage.
4. Run `sudo k3s secrets-encrypt rotate-keys` on homelab-01. Wait for
   `reencrypt_finished`; this rewrites existing Secrets, not just newly created
   ones. Do not start another rotation while this operation is active.
5. Restart `k3s` on homelab-01, then homelab-02, then homelab-03. Wait for each
   API server and node to recover before touching the next. K3s uses
   `KillMode=process`, so these service restarts retain running containers.
6. On every server, require `Encryption Status: Enabled`, rotation stage
   `reencrypt_finished`, and `Server Encryption Hashes: All hashes match`.
   Verify Flux and application readiness, make a new etcd snapshot, and refresh
   the external Restic recovery generation including
   `encryption-config.json` and the matching token. Require critical-member
   read-back verification after both the prepared stage and final rotation.

Carry `HOMELAB_AUTHORIZED=1` when invoking live mutation commands. Use strict
SSH host-key verification and the inventory addresses. Commands above show
the target-side operations; do not place credentials in command arguments.

## Recovery and subsequent deployments

Before key rotation, recovery can stop at the prepared identity-provider stage.
After encryption is active, keep the encryption flag and key configuration on
every server. **Do not blindly activate an older generation lacking the flag**
or remove the encryption file: the API needs the keys to read encrypted data.
Fix forward while preserving keys, or follow the complete upstream coordinated
disable/re-encryption procedure. A datastore restore requires its matching
server token and encryption configuration; protect all three together.

Ordinary later fleet deployments preserve the configuration and need no new
rotation. Key rotation is a deliberate cluster operation. For audit acceptance,
perform a harmless API read and verify a new metadata record appears without
resource bodies; never display audit entries containing sensitive URL arguments.
