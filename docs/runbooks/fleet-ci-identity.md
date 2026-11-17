# Dedicated fleet CI SSH identity

Fleet deployments run as a privileged administrative role. Their SSH private
key must be separate from an operator's workstation identity. Public
`config/ci/fleet-deploy.pub` is accepted by `common-base.nix` only from
homelab-04; SSH forwarding, PTYs, and user RCs are disabled. Homelab-04 also
accepts its local Docker bridge sources (172.17 through 172.30) for CI
self-deployment. Other fleet hosts accept only its infrastructure address.
The personal operator guest at 172.31.10.2 is excluded from the CI identity.
The deliberately authorized operator workstation keys remain separate.

`secrets/ci-fleet-deploy.sops.json` contains the dedicated private key, encrypted
to the existing operator Flux, operator recovery, and homelab-04 identities.
Only homelab-04 installs it as root-owned mode-0400
`/run/secrets/ci-fleet-deploy-key`. No other host receives the private key.
The one-shot provisioning helper reads it into memory, uses an anonymous
memory file for SSH, and never logs private data or API tokens.

## Phase 1: authorize and provision

This introduces a new privileged identity and a SOPS rule. The operator must
approve the exact source change through the merge policy/approvals dashboard
before activation. Do not perform a direct live authorization as a workaround.

1. Ship phase-one public authorization and the homelab-04 SOPS runtime secret
   using the existing catalog and its old `ssh_deploy_key`. Wait for every
   intended fleet host to activate. Preserve the operator keys and old CI
   secret throughout this stage.
2. On homelab-04, use the reviewed helper from this same revision and a
   Woodpecker token authorized to manage this repository's secrets. Keep the
   token in a protected file or environment variable. Never print it. Supply a
   known-hosts file containing the pinned inventory host identities.
3. Run a read-only plan:

   ```sh
   python -m scripts.ci.fleet_deploy_key plan \
     --token-file /path/to/protected/woodpecker-token \
     --plan /run/fleet-ci-key-plan.json
   ```

   The report contains only the public fingerprint and hashes binding the
   reviewed ciphertext and existing secret's scope metadata. An existing
   `fleet_deploy_ssh_key` causes refusal instead of replacement.
4. Provision after reviewing the plan:

   ```sh
   HOMELAB_AUTHORIZED=1 python -m scripts.ci.fleet_deploy_key apply \
     --token-file /path/to/protected/woodpecker-token \
     --plan /run/fleet-ci-key-plan.json \
     --known-hosts /path/to/pinned-known-hosts
   ```

   This first proves the runtime private/public pair and noninteractive sudo
   access on every inventory host with strict SSH verification. It then creates
   only `fleet_deploy_ssh_key`, preserving the old secret's event, image and note
   scopes. It never reads or changes the old secret value. Current allowed events
   are `cron` and `deployment`; untrusted pipeline events are rejected.

If creation times out, inspect whether the new secret exists before retrying.
The old secret is untouched, so existing catalog operations continue to work.
Run `verify --known-hosts ...` to repeat SSH acceptance without an API token.
A provisioned key remains root-equivalent from the trusted CI host; source
restrictions do not make a compromised CI control host unprivileged.

## Phase 2: switch callers and verify

Ship the separate catalog change from `ssh_deploy_key` to
`fleet_deploy_ssh_key` only after phase one is proven. Activate the immutable
catalog on homelab-04 through the normal CI-host route. Existing jobs retain
their `SSH_DEPLOY_KEY` variable and file-secret handling; only the source
credential changes.

Run protected fleet dry-run and a bounded acceptance deployment, including CI
self-deployment. Verify remote SSH sessions use the dedicated public
fingerprint and that no forwarding or PTY was required. Do not claim completed
rotation from API creation or a successful SSH test alone.

Before acceptance, rollback switches the catalog back to `ssh_deploy_key` and
reactivates the prior validated CI-host configuration. Both public keys and the
old secret remain available; no operator private-key export is needed. After
successful acceptance, revoke the old CI secret copy through an explicitly
approved protected change and confirm no active catalog callers remain. Keep
operator workstation access itself. The old copy is temporary rollback material,
so rotation is not complete until that CI copy is revoked.
