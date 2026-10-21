# Secret handling

Use the owning service runbook to decide which secret source a workload uses.
This repository has both SOPS-encrypted Kubernetes Secrets and External Secrets
Operator resources. Do not add a second secret mechanism or replace an existing
source without checking the consumer and its deployment path.

## SOPS-encrypted Kubernetes Secrets

`.sops.yaml` currently applies to `gitops/secrets/*.sops.yaml`. Keep new
encrypted manifests under that path unless the repository's SOPS policy is
deliberately changed and reviewed. The configured age recipient is public; the
private age identity is never stored in this repository.

Before changing a secret:

1. Read the owning application runbook and inspect the manifest, its
   `Kustomization`, and the workload that consumes it. Confirm the key names,
   namespace, and deployment path without printing secret data.
2. Confirm the required age identity is available to the local SOPS process.
   If it is missing, stop and report the missing local prerequisite. Do not
   request a private key or secret value in chat, shell output, or a task export.
3. Edit an existing encrypted manifest by running `sops` with that manifest's
   path. Do not decrypt it to the terminal, redirect plaintext to a file, or
   paste decrypted content into a tool transcript.
4. Keep the manifest encrypted in Git. Run the secret scan and the GitOps
   validation:

   ```sh
   bash scripts/checks/secrets.sh
   bash scripts/checks/gitops.sh
   sops -d gitops/secrets/groq-stt.sops.yaml >/dev/null
   ```

   The last command checks that SOPS can decrypt the file while discarding its
   plaintext output. Use the changed manifest's path in this example command.
   Never replace `/dev/null` with a terminal or captured log.

For a new secret or recipient change, first update the reviewed SOPS policy and
the relevant service runbook. Confirm recipient access and the manifest's
Kustomize inclusion before authorizing deployment. Do not guess a recipient or
add plaintext Kubernetes Secret data to an ordinary YAML file.

## External Secrets Operator resources

Inspect the `ExternalSecret` or `SecretStore` and the owning service runbook to
find its remote secret owner, scope, key mapping, refresh policy, and consumer.
Change the source at its authoritative owner. Do not copy values into Git or
replace an `ExternalSecret` with a SOPS Secret for convenience. Credential
creation, rotation, and revocation follow the service-specific procedure
because the safe order depends on whether the consumer can overlap old and new
credentials.

## NixOS host secrets

The fleet imports sops-nix. The first consumer is the projectctl profile on
homelab-04: its five credentials come from `secrets/projectctl.sops.json`,
decrypt into mode-0400 files under `/run/secrets`. The two observer tokens
belong to `projectctl-worker` for direct read-only API discovery; write tokens
and the registry password belong to root and reach privileged invocations
through systemd `LoadCredential`. Plaintext is never evaluated into the Nix
store. Kubernetes secrets retain their existing Flux/ESO ownership.

The projectctl SOPS rule retains both operator recovery recipients and adds
homelab-04's age recipient derived from its public Ed25519 SSH host key.
Decryption uses `/persist/etc/ssh/ssh_host_ed25519_key`; persistence is required
because the host has a tmpfs root. Never install an operator recovery identity
on a host. When replacing a host key, update the recipient policy, rewrap the
bundle with `sops updatekeys`, and verify recovery access before deployment.

Credential changes ship as encrypted source through the held CI-host deployment
route. Boot or activation installs the secrets before normal services start;
subsequent projectctl invocations load the current files. Preserve existing
root-only credential backups until the new generation and read-only credential
verification pass. A host rollback also rolls back its encrypted secret source;
revoked upstream credentials still require service-specific recovery.

## Apply and verify

Merging an authorized GitOps change can cause Flux to apply it. Treat that merge
as deployment authorization, then verify Flux reconciliation and the consumer's
observable behavior using the owning service runbook. Never include Secret
`data` or `stringData` in verification output. If verification fails, preserve
the failed evidence and use the owning runbook's recovery procedure; do not
blindly restore a credential that may already have been revoked.
