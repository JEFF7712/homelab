# Secret handling

Use the owning service runbook to decide which secret source a workload uses.
This repository has both SOPS-encrypted Kubernetes Secrets and External Secrets
Operator resources. Do not add a second secret mechanism or replace an existing
source without checking the consumer and its deployment path.

## SOPS-encrypted Kubernetes Secrets

For Kubernetes, `.sops.yaml` applies to `gitops/secrets/*.sops.yaml`. Keep new
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

Host secrets are owned by sops-nix. Each host decrypts
`secrets/hosts/<host>.sops.yaml` (set as `sops.defaultSopsFile` in its host
directory) into mode-0400 files under `/run/secrets`, plus rendered templates
under `/run/secrets/rendered`. `flake/modules/host-secrets.nix` points every
host at its persisted SSH host key, `/persist/etc/ssh/ssh_host_ed25519_key`,
because the root filesystem is a tmpfs. Plaintext is never evaluated into the
Nix store. A value that several hosts need, such as a broker credential with
a consumer on another host, lives once in `secrets/shared/<purpose>.sops.yaml`
and its consumers set `sopsFile` to it. Kubernetes secrets keep their Flux/ESO
ownership. The projectctl credentials predate this layout and stay in
`secrets/projectctl.sops.json`.

Consumers read the sops path directly, or a `sops.templates` file when a
service needs a composed file such as an `EnvironmentFile`. Declare
`restartUnits` on each secret or template so that activation restarts the
consumer when the value changes; deploy jobs do not restart services for
secret changes. Do not reintroduce files under `/persist/secrets` or CI steps
that copy secrets onto a host.

Recipients are the anchored `keys` in `.sops.yaml`: the two operator
recovery identities plus one age recipient per host, derived with `ssh-to-age`
from the public half of the persisted host key. A host file's rule lists the
operators and that host; a shared file's rule lists the operators and only the
hosts that consume it. Never install an operator recovery identity on a host.
`tests/test_host_secrets.py` checks that the encrypted recipients match the
policy and that every declared key exists. When replacing a host key, update
the rule, rewrap with `sops updatekeys secrets/hosts/<host>.sops.yaml`, and
verify recovery access before deployment. Rewrap every shared file that lists
the host as well.

To change a value, run `sops` on the file that owns it, then ship the deploy
route of each consuming host. Activation installs the new value before the
restarted units start. A host rollback also rolls back its encrypted secret source;
revoked upstream credentials still require service-specific recovery.

## Apply and verify

Merging an authorized GitOps change can cause Flux to apply it. Treat that merge
as deployment authorization, then verify Flux reconciliation and the consumer's
observable behavior using the owning service runbook. Never include Secret
`data` or `stringData` in verification output. If verification fails, preserve
the failed evidence and use the owning runbook's recovery procedure; do not
blindly restore a credential that may already have been revoked.
