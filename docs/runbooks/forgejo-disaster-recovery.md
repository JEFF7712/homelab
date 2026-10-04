# Forgejo deployment and disaster recovery

## Completion boundary

Source checks do not establish live acceptance. Publication, API changes,
credential provisioning and fleet deployment require shared-infrastructure
authorization. Drain both schedulers before activation or authority changes.

## Deployment transaction

1. Inspect current Forgejo and GitLab heads and tags. Reconcile divergent
   GitLab main through a normal merge preserving both histories. Refresh the
   staged recovery merge in task evidence if either remote advances.
2. Disable the GitLab maintenance schedule and pause old runners. Delete
   Forgejo's built-in mirror record. The replacement runs every five minutes,
   uses normal atomic pushes, verifies refs and rejects divergence.
3. Provision repository-scoped publisher and Renovate identities. Supply
   FORGEJO_PUBLISH_TOKEN and FORGEJO_RENOVATE_TOKEN through protected inputs.
   FORGEJO_DASHBOARD_APPROVAL_TOKEN must belong to an authorized human operator,
   scoped to repository reads and issue comments. A bot cannot approve itself.
4. Provision read-only OPNsense and Cloudflare plan identities and separate
   deploy identities. The mode-0600 JSON input to prepare_platform.py needs
   OPNSENSE_PLAN_API_KEY, OPNSENSE_PLAN_API_SECRET, OPNSENSE_DEPLOY_API_KEY,
   OPNSENSE_DEPLOY_API_SECRET, CLOUDFLARE_PLAN_API_TOKEN, and
   CLOUDFLARE_DEPLOY_API_TOKEN plus other catalog-required credentials.
   Missing explicit values fail preparation; never substitute broad write
   credentials into read-only jobs.
5. Freeze state writers. Run scripts.ci.prepare_platform in the pinned shell
   with its required --overrides and --output arguments. It reads current
   Garage objects and produces an age-encrypted archive. Compare recorded
   fingerprints against the frozen objects. Import preserves exact bytes,
   serials and lineages and starts with authority disabled. Old GitLab state
   objects are historical copies, not current fallback state.
6. Decrypt only into private temporary storage. Install nas-01/tofu-state
   contents under /persist/tofu-state, directory mode 0700, owner tofu-state,
   file mode 0600. Install its CA as state_ca_file in Woodpecker and the
   matching protected GitLab file variable. Clients verify TLS at
   https://s3.internal:3902. Rotate the certificate and client CA together
   before its one-year expiry.
7. Install root-owned mode-0600 Woodpecker server.env, separate agent tier
   EnvironmentFiles, policy.env containing POLICY_API_TOKEN, and
   signature-public.pem from the running server's public-key endpoint.
   Register replacement agents with separate credentials; rotate exposed
   legacy credentials and revoke obsolete registered agents and any old global
   agent secret. Install the bundle's Woodpecker secret payload.
8. Verify repository identity before applying
   config/ci/woodpecker-repository.json to Woodpecker repository 1. This
   removes trusted permissions and per-repository extension overrides.
   Stage the main maintenance cron in a paused state. Stage
   config/ci/forgejo-protection.json for main, including administrators.
   Apply protection only after the new validation context is proven in step 12.
9. Regenerate homelab-values.sops.yaml with scripts.ci.migrate_secrets
   --overrides using the new Forgejo credentials. Verify every ExternalSecret
   property is present without printing values. Publish only encrypted data.
10. Remove the direct GitLab GitOps-write step from the external Obsidian
    workflow. The task evidence contains the reviewed patch. Preserve image
    publication and retention; Woodpecker promotion now creates review PRs.
11. Provision dedicated encrypted offsite Restic storage on NAS and homelab-04.
    Install /persist/platform-backup.env with RESTIC_REPOSITORY,
    RESTIC_PASSWORD and provider credentials. Initialize a fresh repository
    prefix once and preserve recovery inputs in separate encrypted custody.
    Install /persist/forgejo/dr-mirror.env with GITLAB_MIRROR_TOKEN, scoped
    to normal repository writes. GitLab main must permit this mirror's
    fast-forward replication while remaining protected from other writers.
12. Cancel queued pipelines compiled under the old CI policy. Deploy reviewed
    NAS, homelab-04 and AdGuard configurations with fleet preflight while state
    authority remains disabled. Start the sandbox agent first, keeping trusted
    and deploy agents stopped. Publish the encrypted review branch and verify
    validation-v2 on its PR. Apply main protection, then merge normally.
    Flux owns Kubernetes changes. Verify the local ESO store and all
    ExternalSecrets before retiring GitLab secret access. Preserve the
    encrypted GitLab ESO credential as escrow until acceptance.
13. With executors drained, select woodpecker using NAS ci-authority. Start
    trusted and deploy agents, resume the maintenance cron, and run fresh
    current-main validation, both plans, a harmless manual diagnostic,
    mirroring, an immediate backup and an isolated restore drill.

## Live acceptance

- Confirm Docker agents, stopped legacy local agents, no host Nix trust,
  no job Docker socket or host credentials, and restrictive secret modes.
- Run a harmless PR containing hostile pipeline YAML. It must receive only
  validation. Test a policy outage and tampered signature: both fail closed.
- Verify restart variable overrides, unauthorized deployment operators, and
  stale SHA deployments are rejected by the patched native server.
- Verify native parent-number interpolation and saved-plan transfer. Confirm
  read-only provider identities, TLS and imported state lineages.
- Verify main protection blocks direct writes and merges without validation.
  Test exact-head Renovate approval through both PR comments and dashboard.
- Verify all Flux Kustomizations and ExternalSecrets Ready, and actual
  Forgejo webhook delivery. Polling alone does not prove the webhook.
- Verify the GitLab mirror matches heads and tags after a normal source update.
  Exercise divergence only against an isolated repository.
- Verify offsite snapshots and actually restore them. A timer or Restic exit
  code alone is not restoration proof.

## Forgejo outage with NAS available

1. Stop new Woodpecker work and drain operations. Suspend promotion and
   external writers. Stop git-dr-mirror.timer. Record accepted source SHA,
   state serials, and the latest verified recovery mirror.
2. Confirm GitLab has that source and the recovery configuration. Register
   fresh project-locked, tag-only runners: homelab-dr-validation accepts
   unprotected branches; homelab-dr-operations is protected. Disable shared
   and obsolete runners. Protect main and production, restricting deployers.
3. Generate /persist/gitlab-dr/config.toml with scripts.ci.prepare_runner
   using its two fresh token-file inputs. Keep the output root-owned mode
   0600. Deploy homelab-04-dr and verify all Woodpecker agents stopped.
4. Set GitLab's CI config path to config/ci/gitlab-dr.yml and CI_DR_ACTIVE=1.
   Scope all provider, SSH, registry, state and publication variables to
   protected production. Validation must receive no production secrets.
   The helper maps catalog secret names to uppercase GitLab variable names;
   file variables use GitLab file type. Use DR_STATE_PLAN_PASSWORD and
   DR_STATE_DEPLOY_PASSWORD from the bundle and GITLAB_PUBLISH_TOKEN for
   recovery merge-request publication. Never use stale legacy state.
5. Select gitlab with NAS ci-authority. Active OpenTofu locks prevent the
   switch. Drain non-OpenTofu operations first as well. Old credentials can
   no longer lock, write state, upload artifacts or obtain operation permits.
6. Run fresh validation. Set CI_OPERATION to a catalog key for the protected
   manual operate job. Run plans first; apply consumes their saved plans for
   this pipeline. Promotion creates a GitLab MR rather than writing main.
7. Explicitly change the Flux GitRepository URL to the recovery GitLab SSH
   remote and install matching known_hosts/private-key authentication through
   recovery source. Preserve the original object and encrypted credentials.
   Verify artifact SHA and every dependent Kustomization. The gitlab-push
   receiver alone does not change the source.

Existing workloads keep running during the outage; new reconciliations need
an accessible source. Pause Renovate while recovering, since its platform is
Forgejo. Operator-reviewed GitLab changes retain validation and manual operations.

## NAS or state-service loss

Freeze both schedulers. Never initialize a second empty backend. Restore the
offsite snapshot into staging. Verify SQLite integrity, state lineages, serials
and resource identities before installing authoritative data. Restore Forgejo
data with its repositories, Garage metadata with object data, and Woodpecker
database with credential files. Re-create datasets and verify exact mounts.

Backups run hourly with up to five minutes of delay. The recovery point is the
last verified snapshot. Later writes require reconciliation against actual
resources. Local state versions and Git mirrors alone cannot survive NAS loss.

For an isolated drill, restore into a mode-0700 directory. Run SQLite integrity
checks, compare state bytes and lineages to recorded fingerprints, inspect Git
refs and git fsck, and verify representative Garage objects against restored
metadata. Start services with test ports and credentials. Never connect the
restored scheduler to production during the drill.

## Returning to Forgejo

Drain GitLab, set CI_DR_ACTIVE=0 and pause recovery runners. Merge recovery
history normally into Forgejo, preserving both main tips. With both schedulers
stopped, select woodpecker and deploy normal homelab-04. Restore the Forgejo
Flux URL and auth Secret. Verify fresh validation, new plans, Flux readiness,
webhook delivery and replication before resuming timers. No force push.
