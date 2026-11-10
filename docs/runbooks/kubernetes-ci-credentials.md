# Short-lived Home Assistant CI credentials

The `ha-deployer` Kubernetes role remains namespace-scoped. Its TokenRequest
issuer is a fixed SSH capability on homelab-01: the `kube-ci-ha` account accepts
only the dedicated CI-host key, rejects SSH commands and forwarding, and can sudo
only a root-owned helper with no arguments. The helper requests a one-hour token
for `home-assistant/ha-deployer` and returns a kubeconfig with the real cluster CA.
It cannot select another ServiceAccount or token lifetime.

The encrypted issuer private key is in `secrets/ha-ci-issuer.sops.json` and is
available only to the existing operator recovery identities and homelab-04.
sops-nix installs it as root-owned mode 0400 `/run/secrets/ha-ci-issuer-key` on
homelab-04. The public half is `config/ci/ha-issuer.pub`. Never print the private
key, token, or kubeconfig. The script captures API and SSH results in memory and
only the explicit `issue` mode writes a kubeconfig to its intended SSH transport.

## Two-phase rollout

1. Obtain exact-change approval and deploy the issuer on homelab-01 and runtime
   key on homelab-04. Keep current CI callers and the existing
   `ha_deployer_kubeconfig` secret unchanged during this phase.
2. From a private shell on homelab-04, use the existing Woodpecker operator/API
   credential file to inspect the create-only provisioning plan:

   ```sh
   python -m scripts.ci.kube_credentials provision-plan --api-token-file /private/woodpecker-token
   ```

   Check the repository identity and allowed events/images. After approval, run
   `provision` with the same token file, `--key-file /run/secrets/ha-ci-issuer-key`
   and `--expected-metadata-sha256` from that plan under `HOMELAB_AUTHORIZED=1`.
   This creates only `ha_issuer_ssh_key`, preserving existing event/image scopes;
   it refuses an existing new secret and never overwrites the legacy credential.
3. Verify the forced-command path using `fetch --key-file ... --known-hosts ...
   --output /private/ha-kubeconfig` from the CI host. The known-hosts file must
   contain the pinned homelab-01 key. The kubeconfig is written atomically with
   mode 0600. Inspect only derived claims: the HA ServiceAccount subject, expiry
   within one hour, and allowed/denied Kubernetes operations. Verify another
   namespace's Secrets are denied. Remove the temporary kubeconfig afterward.
4. Ship the separate operation-catalog change that fetches one scoped kubeconfig
   per HA job and removes it with an exit trap. Run the dry-run and apply routes
   against the same validated source. No broad fleet SSH key is given to HA jobs.
5. After the new route passes, remove the legacy service-account token Secret
   and protected CI kubeconfig through a separately reviewed cleanup. This
   revokes any old copied token. Until then the original token remains valid,
   even when no job uses it.

## Recovery

Before the catalog switch, current jobs continue unchanged. If new issuance or
job authentication fails after the switch, restore the prior catalog reference
through source and the CI-host route; the old secret is retained for this
transition. Do not remove old credentials until new issuance, live RBAC behavior,
and job acceptance pass. After old-token revocation, recovery must repair the
issuer rather than silently recreating an unbounded token.

## Registry advancement profile

`registry-advance` uses a separate `kube-ci-registry` SSH account/key and the
existing `automation/registry-advance` read-only ServiceAccount. It cannot request
HA deployment credentials. Its root helper has the same fixed one-hour lifetime,
no user-supplied namespace/account arguments, and independent sudo command. No
new Kubernetes RBAC rights are granted.

Use `--profile registry-advance` for `provision-plan`, `provision`, and `fetch`.
The encrypted source is `secrets/registry-ci-issuer.sops.json`, the CI-host runtime
path is `/run/secrets/registry-ci-issuer-key`, and the new protected file secret is
`registry_issuer_ssh_key`. The source creation rule uses only the same existing
operator/CI-host recipients as the HA profile. Apply phase1, verify scoped issuance
and denial of Secrets/write operations, and provision the new secret before
shipping the separate `registry-advance` operation-catalog switch. Keep its old
`registry_advance_kubeconfig` and `registry-advance-token` until new issuance and
actual scheduled advancement pass acceptance, then revoke them separately.
