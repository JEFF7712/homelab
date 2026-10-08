# Sovereign Application Lifecycle RFC

Status: draft for implementation. Date: 2026-10-08.

## Objective and scope

A clean machine, independently retained recovery assets, and access to infrastructure owned by Rupan must be sufficient to reproduce a working application without hidden external platform dependencies.

The first implementation moves Darkbit's static site through Forgejo, local Woodpecker CI, Zot, a protected homelab promotion PR, and Flux. This RFC defines the interfaces and acceptance gates. It does not enroll a repository, activate a CI policy, push changes, or deploy a migration.

Three properties are assessed separately:

| Property | Required evidence |
| --- | --- |
| Runtime independence | Site HTML, styles, and assets load through an internal ingress with external access denied |
| Build independence | An empty disposable builder reconstructs the release using only enumerated local sources, artifacts, dependencies, and tools |
| Recovery independence | Replacement infrastructure reconstructs the application from surviving assets while the original control and supply services are unavailable |

Public access through Cloudflare and purchasing through Etsy remain declared external integrations in the pilot. They do not count as independent capabilities. An offline site must still render; an outbound shop link does not promise offline purchasing. Public-ingress replacement is a separate scope.

## Current enrollment audit

Source baseline: homelab `840636cd9fe92da13ad25bc4108fa78adbdd11ae` and Darkbit `8592d96a350b3ae1c758c940c3c780d9816231c2`. Refresh these identities before implementation.

The live Woodpecker server and all three agent tiers were active on homelab-04. Server environment enabled the configuration extension at `http://127.0.0.1:8010/config` with exclusive configuration and netrc enabled. Repository-level configuration in `config/ci/woodpecker-repository.json` leaves extension overrides empty and trusted network, volumes, and security permissions false.

The deployed policy source and local `scripts/ci/policy.py` had identical SHA-256 `7c4696a72d8b28062a19cc1deefcec0a6c053a3f6ebe81a06da68bbf6184e67b`. Its authorization requires Woodpecker repository ID 1, owner JEFF7712, and name homelab. Five local evaluations passed: different repository ID, different owner, application repository, changed repository name, and forbidden homelab pipeline variables were rejected as expected. These exercised the policy function using synthetic requests, not signed HTTP requests or live webhooks.

Enrollment has several gates: Forgejo repository creation/access, Woodpecker user admission and repository activation, webhook delivery, server policy authorization, secret assignment, and branch-status protection. `WOODPECKER_OPEN=false` controls user admission; changing repository YAML cannot bypass the exclusive policy. Repo activation and successful Git cloning alone do not establish usable CI.

Application execution must be a separate policy branch selected from an explicit server-owned catalog. Retain the current homelab branch, state authority, operation catalog, signed-extension checks, deployment operator checks, and credential boundary. Do not replace the homelab identity condition with a blanket owner allowlist.

## Pilot selection: Darkbit

Source: `/home/rupan/businesses/darkbit/darkbit-site`, currently GitHub `JEFF7712/darkbit-site`. Deployment: `gitops/websites/darkbit/`. Artifact: `registry.rupan.dev/apps/darkbit`. Existing publisher identity: `publisher-darkbit`.

Darkbit is a static HTML/CSS/assets site with no database, background worker, API secret, or persistent volume in the inspected deployment. Its Dockerfile copies local files into nginx and requires no production npm install. The package manifest's live-server dependency is development-only and is outside the image build closure; it becomes part of the closure if adopted as a validation tool.

Current dependencies to replace are GitHub checkout/actions/scheduling/secrets/cache and the floating `nginx:alpine` base. The existing deployment already has HTTP readiness, resource limits, a non-root user, read-only root filesystem, and writable emptyDir mounts. Image and deployment UID/path compatibility must be verified together. Add an appropriate liveness or startup probe only after defining its behavior. Rollout and rollback must account for the single replica and actual resource capacity.

Apolline is the alternate pilot but currently fetches Google Fonts. Darkbit's inspected HTML/CSS has external shop/company navigation and inline SVG namespaces, but no external rendering asset URL was found in those files. A browser network test remains required before declaring runtime independence.

## Five lifecycle interfaces

### 1. Source contract

- Every application has one canonical Forgejo repository and recorded default branch, owners, deployment paths, and artifact namespaces. GitHub may remain an optional outbound mirror whose failure cannot block local release or delivery.
- Protected application branches require successful application validation and prohibit direct or forced updates. PR/fork validation receives no publisher, signing, promotion, or deployment credentials.
- A release identity binds application ID, Forgejo repository identity, exact source commit, policy version, build-input manifest, artifact digest, and CI evidence. Repository IDs must be reconciled during recovery; they are not assumed stable after database reconstruction.
- Homelab remains the deployment authority. Application releases propose protected homelab PRs rather than directly mutating cluster state or homelab main. Promotion rights are confined to each application's allowed paths and artifacts.
- Migration inventories branches, tags, LFS, submodules, release assets, issues, PRs, wiki, and local uncommitted work separately. A Git mirror is not evidence that platform metadata or uncommitted work was transferred.

### 2. Build contract

- The catalog identifies checks, build context, Dockerfile, target platform, pinned CI/toolchain images, allowed dependency sources, artifact destination, and permission tier. Initially support the platform actually tested, x86_64-linux, without claiming multi-architecture availability.
- Builds operate in disposable environments without host sockets, privileged mode, production mounts, Kubernetes credentials, or undeclared network access. Rootless build capability and its host requirements must be proven before selecting the executor implementation.
- The tested immutable base digest and all transitive build inputs are locally retained. Tool installation scripts, actions, plugins, build frontends, emulators and architecture-specific binaries belong in the dependency manifest if used.
- Separate untrusted build execution from credentialed publication/signing. A publisher consumes a verified artifact and release identity; application Dockerfiles and scripts never receive signing keys or registry write credentials.
- Publisher rights are limited to `apps/darkbit` for the pilot. Build cache uses an explicitly owned local namespace, not GitHub Actions cache. A cache optimization is not authoritative dependency retention.
- Produce SBOM and provenance records bound to the artifact digest. Define trusted signing material and a verification policy before calling signatures enforced. Scan failures or stale scanner data are inconclusive, not clean findings.
- Distinguish offline rebuildability from bit-for-bit reproducibility. Record both source/input identities and resulting image digest; claim identical outputs only if independently measured, including timestamps and image metadata.
- Promotion consumes verified immutable digests. New CI numbering cannot collide with existing `0.0.<GitHub run number>` tags or cause older releases to become newest under the promotion algorithm.

### 3. Runtime contract

- Every deployment declares resource requests/limits, application health semantics, rollout strategy, writable paths, ingress, network access, service-account permissions, secret references, and structured logs.
- Runtime uses verified artifact digests and has no requirement to contact the source forge or CI scheduler. Existing workloads must tolerate temporary control-plane service outages; cold rescheduling additionally requires an available copy of the image.
- Darkbit requires internal HTTP/TLS reachability and local assets for independence tests. Validate actual page content and asset requests, not only Pod readiness. External ingress and certificate issuance are separately recorded dependencies.
- Rollback retains the previous deployment configuration and artifact plus any relevant compatibility requirements. Shared service instances are not adopted implicitly from another application's namespace.

### 4. State contract

- Applications declare owned databases, roles, object buckets, volumes, migration procedures, consistency boundaries, retention requirements, and measured recovery objectives. Stateless applications explicitly declare no persistent application state.
- Database migrations run as observable, separately authorized release steps with exclusive execution and recorded outcome. Image rollback does not imply database rollback; compatibility and recovery policy are explicit.
- Darkbit has no application data backup requirement. Its source/assets, release manifest, OCI blobs, signatures, provenance, deployment configuration, and necessary recovery credentials still require independent retention.
- Proposed pilot objectives: zero loss of accepted source/releases represented in the independently verified recovery checkpoint, and application availability within 60 minutes after prerequisite platform services become available. These are test targets, not observed performance or an absolute zero-loss guarantee between backup checkpoints. Record total platform recovery time separately.

### 5. Recovery contract

- Define the failed infrastructure and surviving assets before each drill. A primary-NAS-loss drill must not read the original NAS, Forgejo, Zot, Attic, Garage, or metadata hosted only there.
- Surviving assets include offline-readable bootstrap configuration, base OS/installer and required Nix closures, verified source bundles and Forgejo backups, OCI blobs/referrers, durable package/toolchain supply, persistent-state backups, backup tooling, recovery manifests, and independently held decryption/trust keys.
- The recovery manifest records object identity/checksum, storage location, encryption key reference, dependencies, retention, verification date and restore procedure. It contains key references, never plaintext secret values.
- Bootstrap networking and trust first, mount independent recovery media, recover sufficient local artifact/source supply, then restore Git/CI/secret distribution and state services, and finally resume Flux/application deployment. Supply restoration must not require pulling a tool or image from the service being restored.
- Record timestamps, integrity checks, recovered source/manifest identities, credential access and rotation decisions, functional application behavior, and measured data loss. Successful backup jobs alone do not satisfy this contract.

## Release prerequisite: dependency availability

Every release has a retained build-input manifest and evidence that its full closure is resolvable from documented local endpoints or independent recovery assets. This is separate from image presence in Zot and separate from mutable cache health. Retention protects the active release, rollback releases, and the selected recovery checkpoint, including shared dependency blobs and referrers. Garbage collection may not erase them based only on recent access time.

An application may instead declare artifact-only recovery, but that is an explicit weaker operating model and does not pass the pilot's source-rebuild acceptance. Darkbit's initial image build requires its source/assets, pinned nginx image including target-platform layers, and pinned build/validation tooling. It does not require unrelated npm or Python package mirrors merely because those languages exist elsewhere in the platform.

## Acceptance tests

| Gate | Setup | Pass evidence |
| --- | --- | --- |
| Policy isolation | Synthetic and signed extension requests plus enrolled test repository | Foreign repositories, fork publishing, variable overrides, arbitrary infrastructure operations, and publisher/signing credential access are rejected; legitimate application validation is admitted |
| Local delivery | Pilot Forgejo PR through validation, build, publish and homelab promotion | Branch protection, webhook receipt, CI source SHA, Zot digest, promotion verification and applied Flux revision agree; browser renders expected page/assets |
| Clean offline build | Fresh disposable builder with empty writable caches; only enumerated internal endpoints reachable | Clone and checks/build complete from retained inputs; DNS, egress and download evidence exposes undeclared endpoints; build result verified; deliberately missing required input fails closed |
| Offline runtime | Disposable app instance and browser with external access blocked | Page, stylesheet and product assets load locally; no required external rendering requests; no Forgejo/CI contact needed to serve requests |
| Independent recovery | Original primary services inaccessible; only declared surviving assets available | Replacement host bootstraps supply/control services, restores source/artifacts/credentials, rebuilds and deploys Darkbit, verifies behavior, and records recovery time and loss |
| Supply failure | Remove an input only from the isolated test supply copy | Release cannot claim build independence; error names the missing immutable input; production supply remains unchanged |

No gate is passed by this RFC. Policy rejection evaluations and deployed-source comparison are audit evidence only. Production network denial, service stoppage, enrollment, credential changes, repository publication and deployment are outside this local drafting task.

## Implementation order and architecture decisions

1. Introduce the explicit application catalog and a tested application policy branch while retaining homelab behavior. Prove a build executor and publication handoff that meet the permission contract.
2. Migrate Darkbit source and CI through protected changes. Retain current GitHub operation until local delivery acceptance; then make external mirroring optional and retire its runner/credentials after cutover verification.
3. Retain the complete pilot input closure and pass a clean offline build and runtime test. Bring dependency availability into release promotion rather than relying on cache population by prior jobs.
4. Assemble independently accessible recovery assets and execute the isolated restore drill. Complete this before treating the pilot as a reusable platform pattern.
5. Extract tested templates and minimal provisioning automation. Stateful templates and broader language/toolchain supply get their own measured closures and recovery tests.

Keep the existing Woodpecker server provisionally: it already has server-owned execution policy and separate tiers. This avoids adding a second scheduler, but the shared server and homelab-04 remain common failure domains. A separate application instance or builder host becomes justified if permissions, contention, or recovery tests show that the shared implementation cannot satisfy the contract. Agent labels and containers alone are not evidence of physical isolation.

The application control plane and artifact supply plane have distinct contracts even where they share hardware. Forgejo, Woodpecker, and Flux determine accepted changes; Zot, Attic, package/toolchain supply and independent artifact copies provide bytes. Logical separation does not create redundant storage or eliminate the NAS failure domain.

Deferred decisions with measurable prerequisites: physical builder placement follows load/isolation measurements; signing enforcement follows a local trust design; public ingress independence follows a network/identity design; stateful templates follow database recovery tests. No new major identity, tracing, documentation or portal service is required for this pilot.

## References and evidence

- [Landscape audit](2026-10-08-project-sovereignty-landscape.md)
- [CI execution and authority](../runbooks/ci-pipelines.md)
- [Registry retention and producers](../runbooks/local-registry.md)
- [Existing platform recovery procedure](../runbooks/forgejo-disaster-recovery.md)
- Policy source: `scripts/ci/policy.py`; service wiring: `flake/modules/woodpecker.nix`; repository settings: `config/ci/woodpecker-repository.json`.
- Local audit evidence: `.agent-state/evidence/sovereign-lifecycle-rfc/policy-rejections.json` and `deployed-policy-comparison.json`.
