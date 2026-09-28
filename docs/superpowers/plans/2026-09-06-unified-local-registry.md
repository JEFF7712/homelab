# Unified local container registry implementation handoff

Date: 2026-09-06
Repository inspected: `/home/rupan/homelab`, revision `465512037c8f6e0ef890f3781ec8e6d1c1757650`.
Legacy repository: `/home/rupan/homelab-old2`, still being migrated.
Status: implementation plan, no registry deployed by this task.

## Objective and accepted decisions

Give Rupan one local place to publish, browse, and pull container images: zot on `nas-01`. Publish homegrown builds there and explicitly copy required third-party releases there before deployment. Keep Attic as the separate Nix binary cache. Do not install Forgejo, Harbor, another Git server, or an S3 service for this project. GitLab remains the Git, CI, secrets-provider, and OpenTofu-state service.

The user requested this plan for another GPT model and its implementation agents. Implement and verify repository changes autonomously. This handoff does not itself authorize publishing branches, changing external repositories, pushing registry content, or deploying shared infrastructure. Complete a concrete reviewed patch and deployment preflight before requesting any still-missing external authorization. If the implementing session already supplies that authorization, continue through deployment and runtime verification. Do not repeatedly ask for the same authorization.

The registry migration is the primary deliverable. A later nix-snapshotter pilot is included because the user expressed interest; its results must not block or silently change the conventional OCI migration.

## Read first and rediscover

Run `just agent-context`, inspect `git status`, read applicable `AGENTS.md`, `AGENT_MAP.md`, and `docs/agent-workflow.md`. Follow applicable Nix skills and use current Nix tooling to confirm package/module availability. Do not assume proposed file names, command contracts, or options below already exist.

Read narrowly:

- `flake/modules/nas-data.nix`, `flake/modules/nas-base.nix`, `flake/hosts/nas-01/{default,tank-config,disk-config}.nix`: service ownership, persistence, datasets, backups.
- `flake/modules/adguard-netbird-appliance.nix`: DNS ownership; discover any competing authoritative records before adding one.
- `flake/modules/k3s-server.nix`, `flake/flake.nix`, `flake/flake.lock`: runtime, pins, host integration.
- `.gitlab-ci.yml`: implemented CI and deployment path. It currently runs `nixos-rebuild` from the NAS-tagged runner. README references to deploy-rs are not proof that deploy-rs is implemented.
- `gitops/`, including Flux-generated controllers, Helm releases, Jobs, CronJobs, init containers, and secrets integrations.
- Legacy `.gitlab-ci.yml`, `infrastructure/nix-cache/attic.yaml`, `infrastructure/automation/`, and image references. Read for migration inventory; do not turn this project into a Talos/ArgoCD rewrite.
- `research_registry/findings_{registries,snapshotter}.md` for earlier evidence. This plan supersedes their Forgejo recommendation.

Prior read-only observations, to refresh before deployment: three Ready NixOS k3s servers at `10.0.30.11` through `.13`; k3s `v1.35.7+k3s1`, containerd `2.2.5-k3s2`; NAS `10.0.30.20`; DNS appliance `10.0.30.10`; NAS Attic, NFS, and GitLab runner active. `/tank/attic` reported only 128 KiB used, so populated cache/substitution is unproven. These observations came from an earlier revision in this conversation, not a guarantee of current state. Worker inventory includes two 60 GB SSDs; NAS data-pool redundancy and free space need fresh checks.

## Architecture contract

### Service, storage, DNS, and TLS

- Proposed endpoint: `https://registry.rupan.dev`, resolving privately to the NAS. Verify domain ownership, existing records, address, and conflicts before using it; if available, adopt it without reopening the registry product decision.
- Run pinned zot as a native NixOS systemd service on the NAS. Prefer an existing suitable NixOS module; otherwise provide one small focused module around the packaged binary. Enable its UI. Use its local filesystem backend, not Kubernetes, NFS-mounted storage, or a new object-storage layer.
- Proposed storage: dataset `tank/registry`, mount `/tank/registry`, non-root dedicated service owner. Persist all auxiliary metadata/state required by the chosen zot build across the NAS's tmpfs-root reboots. Service startup must require the actual dataset mount and fail rather than silently writing under an unmounted directory.
- Add the dataset declaratively and provide a non-destructive existing-pool creation procedure for authorized deployment. Never rerun disko partitioning or recreate/import-over an existing pool merely to add a dataset.
- Terminate TLS on the NAS, independently of Kubernetes. Prefer a native NixOS reverse proxy with host-managed ACME DNS-01 and a narrowly scoped Cloudflare token. Persist ACME account/certificate state and verify renewal/reload. Do not depend on cluster cert-manager, cluster ingress, or browser-based Cloudflare Access for image pulls.
- Bind zot behind the proxy on loopback. Restrict NAS ingress to observed management, cluster, and remote-access source networks, accounting for NetBird routing/SNAT. Do not open broad internet access or weaken existing firewall policies. Keep TLS verification enabled for all clients.
- Use the existing DNS owner for one authoritative private record. Verify resolution from the runner, each node, a Pod, and an authorized remote client; explicitly report unavailable vantage points.
- NixOS owns host service/network/storage configuration; Flux owns Kubernetes objects; existing CI owns OPNsense changes if required. Do not introduce duplicate owners.

### Namespace and image identity

One hostname, with deterministic paths:

| Content | Destination repository path |
| --- | --- |
| First-party application | `apps/<stable-project-name>` |
| Third-party Docker Hub image | `upstream/docker.io/<owner>/<image>` |
| Third-party GHCR image | `upstream/ghcr.io/<owner>/<image>` |
| Other third-party registry | `upstream/<source-host>/<full-repository-path>` |

Normalize shorthand first: `nginx` means `docker.io/library/nginx`. Preserve the full upstream path to avoid collisions. Hostnames containing a port need an explicit validated collision-free namespace mapping; do not derive an invalid repository path. First-party images presently on GHCR/Docker Hub belong under `apps/`, not `upstream/`.

Resolve mutable source tags to immutable digests during a deliberate inventory/update operation. Copy the exact digest, all platform manifests in an index, and required signature/SBOM/provenance referrers. Verify raw manifest/index digest equality at the destination, not only matching tags. Preserve Docker schema 2 content rather than converting it to OCI and changing its digest. Validate the pinned zot release's compatibility configuration; researched docs specify `http.compat: ["docker2s2"]` and sync `preserveDigest: true` when that extension is used.

Deploy digest references. Tags remain useful for browsing, provenance, and retention. Copying an image does not establish publisher authenticity: preserve existing verification requirements and explicitly record whether signatures were verified, merely copied, absent, or unsupported. Do not claim the copy tool transfers referrers automatically; verify its actual capabilities against fixtures and real samples.

### Access and secret lifecycle

- Anonymous access denied by default. Node credentials: read-only on the image namespaces needed by this single-owner cluster. Document that node-wide credentials are broader than per-workload credentials.
- Project publisher credentials: write/read only their `apps/<project>` repositories. Upstream importer: write/read only `upstream/`. Migration of old first-party releases uses appropriately scoped application credentials. Reserve deletion and administration for a separate maintenance identity.
- Use zot's native supported authentication and repository authorization. Do not invent unsupported token claims or permissions. If a required restriction cannot be expressed in the pinned release, document the concrete limitation before changing the access model.
- No credentials, password hashes, private keys, or rendered authenticated registry configuration in Git plaintext, the Nix store, build logs, or artifacts. Use SOPS-encrypted sources and runtime secret files. Inspect current host secret provisioning; the current flake does not demonstrate an established general-purpose host SOPS module. Add only the integration needed here, or use the existing CI runtime-file provisioning pattern with documented recovery and rotation.
- Bootstrap host credentials without depending on ESO or a container image in the new registry. Kubernetes must not be required to restore its own registry access.
- Configure all three schedulable k3s servers, including future nodes through the owning module. Use the pinned NixOS/k3s supported runtime secret-file interface; inspect rendered containerd configuration. Do not simultaneously introduce competing generated `registries.yaml` files and module-managed settings.

### Local-only image consumption

First-party CI publishes only to zot after migration. Upstream releases enter through explicit, verified import jobs. Initial deployment must not depend on zot fetching missing upstream images on demand. Leave automatic pull-through synchronization off initially.

Rewrite directly owned manifests and supported Helm values to explicit local digest references. Handle k3s sandbox images, Cilium/Helm bootstrap helpers, chart hooks, operators' generated workloads, ephemeral Jobs, and Flux controller images as well as ordinary Deployments. Use durable Helm values, supported bootstrap controls, or regeneration-safe patches; do not hand-edit generated files and call them durable.

Where an upstream-generated image cannot reasonably be overridden, use a narrowly defined containerd mirror/rewrite into its inventoried `upstream/<host>/...` namespace. Record each exception and prove it uses zot. K3s defaults to trying upstream endpoints after mirrors; disable default fallback for configured upstreams after all required content is populated. That flag alone does not block unconfigured registries. Add a rendered-workload image-policy check and validate all exceptions. This is an operational local-image contract, not a claim that all network egress is blocked.

Do not break initial CNI startup to achieve the contract. Bootstrap images must already exist locally and node-level DNS, TLS trust, credentials, and routing must work before switching their references. Keep a tested recovery procedure using the old configuration and retained upstream images. External Helm chart downloads, GitLab, Nix caches, ACME, and application internet access remain separate dependencies; this is not a fully air-gapped lab.

### Publishing and ongoing updates

- Discover actual producer repositories and CI owners for every first-party image. Homelab manifests do not prove the producer pipeline lives here. Prepare exact producer patches or bounded handoffs when those repositories are unavailable. Never mark publishing migration complete while producers still publish solely upstream.
- A private registry requires runners with private network reachability. Route image publication/import to the existing NAS runner or an authorized equivalent. Public GitHub/GitLab hosted runners cannot be assumed to reach it. Keep untrusted pull-request code away from production push credentials and privileged NAS execution.
- Preserve source commit/build provenance. Build once, publish, obtain the verified destination digest, then update desired state. Never update a workload before its referenced image is present.
- Preserve upstream update discovery. Renovate looking only at locally imported tags cannot discover newer upstream releases. Store the upstream mapping and either configure its supported upstream datasource mapping or add a small explicit update workflow. An update must resolve upstream, import and verify, then propose the local digest change; CI must reject a deployment referencing missing content.
- Finish per-image migrations independently. Keep existing external repositories and credentials until their consumers/producers are accounted for; deleting Docker Hub/GHCR repositories or accounts is out of scope.

### Retention, backup, and recovery

Do not enable age-only deletion over release images. Initially retain all migrated/current/rollback releases. Enable blob garbage collection only with tested grace periods and reference handling. Pin deployed and supported rollback digests with retention tags or another proven zot-supported mechanism; untagged digest deployments must not disappear during cleanup. Recommended eventual policy: keep active releases, at least two prior successful releases per application, and at least 30 days of additional release history. These are additive protections, not permission to delete images used by the old cluster.

Back up the full initial registry while its size is small, plus configuration, metadata, and encrypted recovery material. Use local snapshots for quick recovery and an independently stored encrypted backup through an existing suitable backup destination. Confirm capacity, permissions, and actual coverage; existing photos/documents and database-dump jobs do not automatically back up this service. Coordinate snapshots with zot's documented consistency requirements. Separate recoverable caches from irreplaceable artifacts only after proving exact reconstruction from the inventory and upstream availability assumptions.

Restore into an isolated alternate location/instance and verify authenticated digest pulls before calling backup complete. Recovery must work with Kubernetes unavailable. Monitor disk usage, service/certificate health, backup failures, and failed imports using existing observability, without adding an unrelated monitoring stack.

## Implemented interfaces to deliver

These are proposed contracts, not existing commands. Prefer a small typed Python module under `scripts/registry/` with external OCI tools, clear pure planning functions, and no framework. Use the pinned development environment. Add wrappers to the existing `justfile` if useful.

Create `registry/images.lock.json`, schema version 1. Each record requires a stable ID, kind (`first-party` or `upstream`), source registry/repository, original source tag if applicable, resolved `sha256` digest, destination repository, media type, index platforms when applicable, desired-state consumers, producer owner/location for first-party content, and retention class (`deployed`, `rollback`, or `candidate`). Reject duplicate destinations with conflicting identities, invalid references, missing digests, unknown schema versions, and unclassified consumers. Derive a concise formal schema and commit a populated real inventory, not sample values.

| Command | Contract |
| --- | --- |
| `python -m scripts.registry inventory` | Offline discovery from owned source and pinned/rendered manifests; reports unresolved chart/runtime/producer inputs explicitly. Does not resolve tags, change state, or claim source-only inventory is exhaustive. |
| `python -m scripts.registry resolve --inventory PATH --output PATH` | Explicit network read: resolve tags/indexes, produce lock candidates, never push or edit workload references. |
| `python -m scripts.registry plan --lock PATH` | Validate and emit a deterministic copy/verification plan, no registry writes. |
| `python -m scripts.registry copy --lock PATH --report PATH` | Explicit mutation for authorized CI only. Idempotently copy locked content, verify digest/platform/referrer requirements, fail on mismatch. Reuse verified destination content without overwriting conflicting release tags. |
| `python -m scripts.registry verify --lock PATH --report PATH` | Read-only remote completeness and digest verification using the exact destination names consumed by workloads. |
| `python -m scripts.registry check --lock PATH` | Offline policy check: all rendered consumer references map to locked local digests or documented tested mirror exceptions; no undeclared public-image regressions. |

All commands: nonzero on incomplete required work or validation failure; bounded retries/timeouts; no secrets in argv or output; auth through protected runtime auth files/credentials; structured reports with schema version, timestamp, source revision, per-image outcomes, expected/observed digests, platform/referrer outcomes, and actionable errors. Network calls must be explicit, never hidden in startup context or the offline gate. Do not add a deletion command for the initial migration.

## Agent assignments and integration order

The receiving GPT model is the orchestrator and sole integrator. Use up to three workers concurrently after the inventory/data contract is agreed. Give each agent its exact file allowlist and acceptance criteria. Use isolated worktrees if needed; never have two agents editing a shared file. Follow the actual model-routing instructions, and never guess model IDs.

| Agent | Exclusive work | Acceptance before handoff |
| --- | --- | --- |
| A: NAS platform | New focused registry service module; registry service VM tests; proposed host integration patch | Pinned service evaluates/builds, persistence/mount ordering, TLS/auth/authorization and restart behavior tested |
| B: image supply | `scripts/registry/`, `registry/` lock/schema, corresponding behavior tests | Deterministic mapping, digest/index preservation, idempotency/failure handling, update contract and reports proven |
| C: consumers | Owned workload image fields, Helm values, regeneration-safe Flux patches, corresponding rendering/policy tests | Every direct and generated image inventoried; content-before-reference ordering; complete list of bootstrap/mirror exceptions and producer patches |
| Orchestrator | Shared host imports, dataset/DNS/secrets/k3s modules, flake inputs/tools, `justfile`, `.gitlab-ci.yml`, `AGENT_MAP.md`, runbook and combined gates | Integrates A/B/C serially, reconciles interfaces, validates complete delivery and authorization boundaries |

Agents needing a shared-file change send an exact patch to the orchestrator rather than editing it. Each handoff contains base revision, owned files, behavior changes, exact commands/results, unresolved issues, and the next dependent action. Review delegated changes independently. Use a different model family for independent final review when an authorized available peer supports it; if unavailable, record the limitation and perform an independent diff/test review rather than inventing dispatch capabilities.

### Phase 0: inventory and baseline

Produce a sanitized baseline report: fresh node/runtime identity, NAS mounts/capacity/service ports, DNS ownership, CI reachability, all source/rendered/live image references, producer locations, exact upstream digests, current and rollback releases, and backup destination. Inspect live workloads read-only if reachable. Reconcile differences between source and runtime; do not silently overwrite either. Record legacy consumers that still require old registry content.

Agree schema and namespaces before parallel implementation. Reuse source mappings across import, policy, update detection, and retention rather than building separate drifting inventories.

### Phase 1: implementation and offline review

Agents implement A/B/C. Orchestrator integrates platform configuration and jobs, adds registry-specific checks to the existing validation selection and full offline gate, and writes `docs/runbooks/local-registry.md`. Build the NAS and affected node configurations and exercise disposable service/OCI fixtures. Produce a target-specific preflight with dataset operations, DNS/TLS plan, credential delivery, proposed deploy diff, outage expectations, recovery commands, and exact image copy list.

Do not merge a Flux-active consumer cutover before the registry and content are ready. Separate platform/preparation and consumer cutover commits or branches so an authorized push cannot accidentally reconcile unavailable images.

### Phase 2: authorized platform deployment and import

Deploy NAS registry, DNS and secrets through the repository's actual CI/host ownership paths. Verify NAS service and dataset identity, trusted TLS, authorization boundaries, private client reachability, persistence and backup. Import all required current, bootstrap, and rollback images using the locked copy plan, then independently verify them from the node/runner network.

Configure node auth/trust without switching all workload images at once. Roll schedulable etcd servers one at a time, preserving quorum and checking disruption budgets, local-volume workloads, and scheduling headroom. No indiscriminate node drain, image-cache deletion, or three-node simultaneous restart.

### Phase 3: authorized consumer and producer cutover

Canary a small stateless workload, prove an uncached pull using a disposable isolated runtime or safely selected new digest, then migrate cohorts. Use fresh kubelet/containerd logs and registry access logs to prove the endpoint; a successful Pod using a preexisting cache is insufficient. Update producer pipelines and the upstream update workflow. Migrate bootstrap/system images with separate restart/recovery checks. Disable configured upstream fallback only after completeness is established.

Verify the legacy cluster remains healthy for workloads still living there; migrating its runtime is outside scope unless specifically added. Old registry cleanup automation must not delete retained recovery releases, and edits to its owner require the appropriate authorization.

### Phase 4: recovery and acceptance

Prove restore in isolation and fresh container pulls with upstream image-registry access unavailable in a disposable test environment. Do not simulate internet failure by disrupting the production router. Demonstrate that missing uncopied content fails clearly rather than silently falling back upstream. Keep source indexes and required referenced manifests/referrers intact through tested GC. Report residual exceptions explicitly instead of declaring unqualified completion.

### Phase 5: separate nix-snapshotter pilot

Keep registry migration on ordinary OCI first. Then prepare a separately reviewable pilot using the exact pinned k3s and nix-snapshotter combination in a disposable NixOS VM. The inspected k3s tag already imports nix-snapshotter; confirm current support before adding an external containerd or redundant daemon.

Use native Nix images for one homegrown application, published through normal registry digest references. Upload and verify full runtime closures in Attic before publishing/deploying the manifest. Configure host-level substituter credentials and trusted signing keys. Avoid runtime builds as a normal deployment path. Keep `nix:0` references out of the initial pilot due to the open checkpoint-lookup issue documented below.

Test cold authenticated substitution, exact-store-path reuse, ordinary OCI containers, file-valued store paths, reboot, Nix/containerd GC, remote cache retention, cache outage, and rollback. Nix GC roots on a node do not retain data in Attic; deployed and rollback closures require remote retention. The registry's layer scanner cannot be assumed to scan external Nix closures. Native Nix images need a compatible snapshotter and are not portable conventional images merely because their manifests are OCI-valid.

Enabling a snapshotter changes the node runtime, not only one Pod. After VM success, propose one-node activation through normal deployment, with measured local disk usage and a rollback to the previous runtime configuration. Keep the prior conventional application image available. This pilot may remain blocked without making the completed registry migration a failure.

## Required acceptance evidence

| Area | Observable proof |
| --- | --- |
| Image inventory | Source, rendered charts, system/bootstrap, live Pods, init containers and scheduled workloads reconciled; every unresolved entry listed |
| Identity | Same source/destination manifest or index digest; platform coverage and required referrers verified; no conversion surprises |
| Credentials | Anonymous denied; node can pull but cannot push/delete; project publisher cannot write another project or upstream namespace; importer cannot write applications |
| TLS/DNS | Trusted hostname validation from nodes and runner, private resolution, renewal/reload test, no insecure flags |
| Persistence | NAS restart retains registry data, credentials, metadata, certificates; missing mount fails service startup |
| Cutover | Fresh Pod/runtime pulls demonstrably served by zot; app behavior and all three node/Flux health checked |
| Updates | One new upstream version discovered from its source, imported, verified, and proposed as a local digest before rollout; one first-party build publishes locally |
| Bootstrap | New/disposable node or equivalent cold-start test can obtain sandbox, CNI, helper, and Flux images through the local endpoint |
| Failure | Missing digest, revoked credential, bad certificate, partial import, disk-pressure condition, and copy interruption fail visibly without advancing desired state |
| Recovery | Isolated restore serves retained digests; rollback images remain after tested GC; cold pulls work with upstream registry access blocked in the test environment |
| Scope | No Forgejo, no new S3 dependency, no upstream repository/account deletion, no unrelated migration or runtime change bundled in |

Run pinned checks from the repository root: `nix develop ./flake -c just check-changed`, `nix develop ./flake -c just check`, `nix develop ./flake -c just fmt-check`, and required flake/YAML/secret scans per AGENTS.md. Add meaningful behavior tests, not assertions that strings occur in source. The existing formatter enumerates tracked Nix files, so explicitly check new Nix files too. Git-backed flake builds omit untracked files: include new files in an isolated review checkout/index or explicitly stage only authorized task files, never auto-stage unrelated work. Do not allow this to produce a false passing build of the old source.

Document preexisting failures separately with baseline evidence; do not fix unrelated code just to green the task. Validate the final combined revision after integrating agents. Static checks, service VMs, live deployment, external publishing, and recovery tests are distinct statuses.

## Completion handoff

Deliver the implemented configuration/scripts/tests, populated lock, producer patches, update workflow, runbook, deployment/rollback instructions, and sanitized acceptance report under `docs/agent-tasks/local-registry.md`. Distinguish: implemented and tested locally; deployed and verified; external work waiting for authorization or repository access; and deferred nix-snapshotter work.

The primary goal is complete only when all in-scope new-homelab image consumption uses zot, required images and rollback releases exist locally, first-party publishing and upstream updates are operational, and restore is proven. If external authorization or unavailable producer repositories prevent that outcome, finish all independent implementation and state precisely what remains rather than calling the migration complete.

## Primary references

- [zot configuration](https://zotregistry.dev/v2.1.18/admin-guide/admin-configuration/)
- [zot authentication and authorization](https://zotregistry.dev/v2.1.18/articles/authn-authz/)
- [zot mirroring and digest preservation](https://zotregistry.dev/v2.1.18/articles/mirroring/)
- [zot retention](https://zotregistry.dev/v2.1.18/articles/retention/)
- [k3s registry configuration and fallback behavior](https://docs.k3s.io/installation/private-registry)
- [nix-snapshotter architecture](https://github.com/pdtpartners/nix-snapshotter/blob/main/docs/architecture.md)
- [nix-snapshotter nix:0 checkpoint issue](https://github.com/pdtpartners/nix-snapshotter/issues/179)
- [reported k3s 1.36 file-path regression, not proof that the lab's 1.35 runtime is affected](https://github.com/pdtpartners/nix-snapshotter/issues/183)

Check documentation against the actual pinned releases during implementation. These links are research references, not permission to blindly install the latest upstream version.
