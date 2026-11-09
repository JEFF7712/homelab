# Forgejo deployment and disaster recovery

For loss of the NAS and cluster without public services, use the
[independent platform recovery plan](sovereign-platform-recovery.md).

There is no second CI forge. Recovery restores Forgejo, Garage state and the
Woodpecker database from the drilled platform backup. The CI-driven GitHub
mirror (`sync-to-github`, watched by [repo-monitor](repo-monitor.md)) is the
off-site copy of source history if the backup itself is lost. The GitLab
recovery lane was retired on 2026-11-09; its project is an inert historical
copy that nothing reads from or writes to.

## Completion boundary

Source checks do not establish live acceptance. Publication, API changes,
credential provisioning and fleet deployment require shared-infrastructure
authorization. Drain the scheduler before activation or authority changes.

## Deployment transaction

1. Inspect current Forgejo heads and tags and confirm repo-monitor reports
   the GitHub mirror healthy.
2. Provision repository-scoped publisher and Renovate identities. Supply
   FORGEJO_PUBLISH_TOKEN and FORGEJO_RENOVATE_TOKEN through protected inputs.
   The Renovate token requires `write:repository`, `write:issue`, `read:user`,
   and `read:organization`. Verify authenticated `/api/v1/user` access before
   provisioning it; repository writes alone do not permit initialization.
   Provision a separate read-only repository identity as
   FORGEJO_SOURCE_READ_TOKEN for current-main checks in operation jobs.
   FORGEJO_DASHBOARD_APPROVAL_TOKEN must belong to an authorized human operator,
   scoped to repository reads and issue comments. A bot cannot approve itself.
3. Provision read-only OPNsense and Cloudflare plan identities and separate
   deploy identities. The mode-0600 JSON input to prepare_platform.py needs
   OPNSENSE_PLAN_API_KEY, OPNSENSE_PLAN_API_SECRET, OPNSENSE_DEPLOY_API_KEY,
   OPNSENSE_DEPLOY_API_SECRET, CLOUDFLARE_PLAN_API_TOKEN, and
   CLOUDFLARE_DEPLOY_API_TOKEN plus every other catalog-required credential;
   the file is the only credential source. The live copies are Woodpecker
   repository secrets, restored with the Woodpecker database from the platform
   backup. Missing explicit values fail preparation; never substitute broad
   write credentials into read-only jobs.
4. Freeze state writers. Run scripts.ci.prepare_platform in the pinned shell
   with its required --overrides and --output arguments. It reads current
   Garage objects and produces an age-encrypted archive. Compare recorded
   fingerprints against the frozen objects. Import preserves exact bytes,
   serials and lineages and starts with authority disabled.
5. Decrypt only into private temporary storage. Install nas-01/tofu-state
   contents under /persist/tofu-state, directory mode 0700, owner tofu-state,
   file mode 0600. Install its CA as state_ca_file in Woodpecker. Clients
   verify TLS at https://s3.internal:3902. Rotate the certificate and client
   CA together before its one-year expiry.
6. Store the Woodpecker server, per-tier agent and policy (POLICY_API_TOKEN)
   environments in secrets/hosts/homelab-04.sops.yaml, and install
   signature-public.pem from the running server's public-key endpoint.
   Put the bundle's homelab-04/woodpecker/clone.env into
   secrets/woodpecker-clone.sops.env, which homelab-04 decrypts. Native clones use
   the repository-scoped read-only identity because queued workflows can
   outlive the OAuth token captured when they compile. Missing clone
   credentials fail closed. OAuth still owns user identity and status updates.
   Register replacement agents with separate credentials; rotate exposed
   legacy credentials and revoke obsolete registered agents and any old global
   agent secret. Install the bundle's Woodpecker secret payload.
   Command steps require empty secret image filters. Restrict secrets by event;
   the exclusive signed policy admits secret-bearing jobs only from current
   protected main and gives PRs only secret-free validation.
7. Verify repository identity before applying
   config/ci/woodpecker-repository.json to Woodpecker repository 1. This
   removes trusted permissions and per-repository extension overrides.
   Stage the main maintenance cron in a paused state. Stage
   config/ci/forgejo-protection.json for main, including administrators.
   Apply protection only after the new validation context is proven in step 10.
8. Regenerate homelab-values.sops.yaml with scripts.ci.migrate_secrets
   --overrides using the new Forgejo credentials. Verify every ExternalSecret
   property is present without printing values. Publish only encrypted data.
   The helper preserves the existing encrypted values.
9. Provision dedicated encrypted offsite Restic storage on NAS and homelab-04.
   Store platform-backup.env, with RESTIC_REPOSITORY, RESTIC_PASSWORD and
   provider credentials, in secrets/shared/platform-backup.sops.yaml. Initialize a fresh repository
   prefix once and preserve recovery inputs in separate encrypted custody.
   Backup snapshots stage under `/persist/platform-backup`, with private
   temporary directories removed after each run. The backup service requires
   `/persist` to be mounted. Keep enough free space there for the CI database
   and the NAS source snapshots; the small ephemeral root is not staging storage.
10. Cancel queued pipelines compiled under the old CI policy. Deploy reviewed
    NAS, homelab-04 and AdGuard configurations with fleet preflight while state
    authority remains disabled. Start the sandbox agent first, keeping trusted
    and deploy agents stopped. Publish the encrypted review branch and verify
    validation-v2 on its PR. Apply main protection, then merge normally.
    Flux owns Kubernetes changes. Verify the local ESO store and all
    ExternalSecrets.
11. With executors drained, select woodpecker using NAS ci-authority. Start
    trusted and deploy agents, resume the maintenance cron, and run fresh
    current-main validation, both plans, a harmless manual diagnostic,
    the GitHub mirror, an immediate backup and an isolated restore drill.

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
- Verify repo-monitor reports the GitHub mirror healthy after a normal
  source update.
- Verify offsite snapshots and actually restore them. A timer or Restic exit
  code alone is not restoration proof.

## Forgejo outage with NAS available

Existing workloads keep running; Flux serves its last fetched artifact and new
reconciliations wait for the source. Do not point the Flux GitRepository at
GitHub: the GitHub branch carries GitHub-only commits and sits behind no
deploy gate.

1. Stop new Woodpecker work, drain operations, and pause Renovate and
   promotion. Record the accepted source SHA and state serials.
2. Diagnose Forgejo with `just diag forgejo` and repair it in place. If its
   data is damaged, restore Forgejo data from the latest verified platform
   snapshot as below, leaving Garage and Woodpecker alone unless they are
   damaged too.
3. If the snapshot lacks commits that reached main afterwards, recover them
   from the GitHub mirror. `scripts/ci/mirror.sh` pushes the Forgejo head plus
   one "Automated: Add GitHub Mirror Warning" commit on top, sometimes
   wrapped in an "Automated: Preserve public mirror history" merge. Fetch
   GitHub main into a scratch clone and follow first parents past those
   automated commits to reach the last mirrored Forgejo head. Push that commit
   to Forgejo main as a fast-forward. Never push the automated GitHub-only
   commits to Forgejo.
4. Verify Flux readiness, webhook delivery, fresh validation and the GitHub
   mirror before resuming Woodpecker, Renovate and promotion.

## NAS or state-service loss

Freeze the scheduler. Never initialize a second empty backend. Restore the
offsite snapshot into staging. Verify SQLite integrity, state lineages, serials
and resource identities before installing authoritative data. Restore Forgejo
data with its repositories, Garage metadata with object data, and Woodpecker
database with credential files. Re-create datasets and verify exact mounts.

Backups run hourly with up to five minutes of delay. The recovery point is the
last verified snapshot. Later writes require reconciliation against actual
resources. Local state versions alone cannot survive NAS loss; the GitHub
mirror covers source history only.

For an isolated drill, restore into a mode-0700 directory. Run SQLite integrity
checks, compare state bytes and lineages to recorded fingerprints, inspect Git
refs and git fsck, and verify representative Garage objects against restored
metadata. Start services with test ports and credentials. Never connect the
restored scheduler to production during the drill. The scheduled nas-01 drill
(`platform_backup --mode drill`) does this automatically and publishes its
result to Prometheus.
