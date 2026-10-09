# Local development

Pinned environment, registry commands, and offline validation for working in
this repository.

## Environment

Use the repository's pinned development environment and inspect the current
context before changing files:

```sh
nix develop ./flake
just agent-context
just check-changed
```

## Git and Sovereign Infrastructure

- **Primary Git Remote (`origin`):** `ssh://forgejo@git.internal:2222/JEFF7712/homelab.git`
  - Self-hosted Forgejo instance running on `nas-01` (`10.0.30.20:3000`).
  - Developers push exclusively to `origin`.
  - The normal-push recovery mirror verifies GitLab heads and tags every five minutes.
- **CI System:** Woodpecker CI (`http://ci.internal:8000`) on `homelab-04`.
- **GitOps:** Flux CD on `homelab-01` reconciles from Forgejo with sub-second webhook notifications.
- **State Backend:** TLS HTTP service on NAS (`https://s3.internal:3902`) with durable locks and version history. Garage holds the frozen migration input.
- Deployment and explicit GitLab fallback: [Forgejo disaster recovery](forgejo-disaster-recovery.md).


## Registry commands

Useful registry commands are exposed through `just`:

```sh
just registry-inventory
just registry-resolve
just registry-plan
just registry-check
```

`registry-copy` and `registry-verify` are remote operations and require the
deployment authorization and protected credentials described in
[`local-registry.md`](local-registry.md).
Use `just check` for the complete offline validation gate before handoff.

## Nix input retention

`scripts/ci/cache.sh` publishes host, check and development-shell closures to
Attic. Before publication it also runs `nix flake archive` and includes the
root source plus every recursive pinned input in the publication roots.
Malformed or incomplete archive metadata fails before publication. Without
`ATTIC_TOKEN`, the job builds the closures but skips publication.

The job also retains every declared build root's available input graph,
including derivation metadata and already valid outputs. This covers the
seven host systems, declared checks and development shell. This is
different from retaining only the runtime closure: bootstrap hooks and check
builders can require paths that are not referenced by runtime outputs.
Graph enumeration failure stops publication; this does not build additional
package outputs or guarantee availability of inputs that were never fetched.

Pipeline 731 passed with 620 Nix paths downloaded from Attic and none from
the public Nix cache. All six previously missing development-shell helpers
were observed in those local downloads. A fresh, network-disabled container
also restored the latest source and these helpers from retained artifacts
with signature verification enabled. Its receipt is under
`/home/rupan/sovereign-recovery/homelab-ci/2026-10-08/cold-build-inputs-20261009-v2/receipt.json`.

Fleet publication pipeline 729 subsequently passed, but nine of its 19
publicly fetched build helpers still returned 404 after publication. These
were outside the development-shell graph, motivating collection from every
declared build root. A successful application validation does not prove a
complete offline fleet rebuild.

On 2026-10-08, a source audit found the pinned `nixpkgs` source available in
Attic, but six other recursive input sources returned 404. Binary closure
publication alone did not retain those source archives.

Pipeline 718 subsequently published all seven external recursive sources.
A cold, network-disabled test restored source commit `9130256` from its Git
bundle, explicitly imported those retained sources, and evaluated the
homelab-04 system derivation without credentials or disabling signature
checks. This proves source bootstrap, rather than a complete offline CI run.
Its receipt is under
`/home/rupan/sovereign-recovery/homelab-ci/2026-10-08/cold-sources-20261008-v2/receipt.json`.

Generation 45 and replacement pipeline 724 verified the local Nix runtime.
That run downloaded 614 Nix paths locally and six from the public cache.
All six public paths were present in the available development-shell build
graph, which measured 5.05 GiB of uncompressed NAR data on the workstation.
The graph includes many already cached paths; the incremental cache size
depends on existing retained content.

Successful publication and local source availability are prerequisites for a
clean offline CI test. They do not prove it: a disposable builder must resolve
the pinned inputs and all check dependencies without public network access.
Independent recovery also needs accessible source bundles, cache artifacts,
verification keys, toolchains and host state outside the infrastructure being
restored.
The pinned development shell wraps OpenTofu with an exclusive filesystem
provider mirror. `nix/ci-providers.json` pins archives for each supported shell
platform to the versions and ZIP checksums in the stack lock files. The mirror
and archives are Nix store dependencies, retained by cache publication.
`just provision-check-deps` initializes with a read-only lock file. Missing
providers or versions fail rather than falling back to the public registry.
Update the lock file and mirror entries together when upgrading providers.
This changes provider installation only; authenticated plans and applies still
contact their target services and use the existing state configuration.

Core schemas used by the rendered GitOps manifests are committed under
`schemas/kubernetes-core/v1.35.7-standalone-strict`, matching the observed
cluster version. `retention.json` records the upstream commit and checksums.
GitOps validation uses only these files and the existing local CRD schemas.
Missing schemas fail, including in bootstrap manifests. CRD definitions
remain explicitly skipped. Add the corresponding schema from the pinned
upstream commit when introducing another core kind, and update the pin when
upgrading Kubernetes.

`python -m scripts.ci.offline INPUTS_JSON NEW_PROOF_DIRECTORY` executes the
full validation and formatting gates in a fresh rootless Podman store with
networking disabled. It imports retained Git and Nix OCI tools, checks out a
checksummed source bundle, imports signed Nix inputs from a read-only file
cache, and executes the unit tests rather than accepting a cached test report.
The input JSON names `source`, `bundle`, `bundle_sha256`, `ref`, `cache`,
`store_paths`, `trusted_public_keys`, and `tools` (Git and Nix OCI directory
paths). Paths are relative to the input JSON directory. The cache must contain
the development shell, recursive flake sources, and all validation build
dependencies. The proof directory must not exist. Logs survive failures;
`receipt.json` is written only after a passing nonempty test report and all
commands succeed. The runner assumes a working Podman runtime. This validates
offline CI, not a full platform restore or live infrastructure plans.

## NAS storage deployments

Fleet deployment checks every ZFS dataset in the evaluated NAS filesystem
configuration on the live NAS before running `nixos-rebuild`. This check also
runs for dry activation and when the general preflight is skipped. Provision
new datasets explicitly before deployment: adding a Disko declaration does not
create a dataset during `nixos-rebuild`. Never run Disko against the existing
tank to add an application dataset.

Every declared tank data mount uses `nofail`, so missing application storage
does not send the NAS into emergency mode. Attic, Zot, Forgejo and S3 services
require their respective mounts. NFS waits for its export mounts to finish
attempting to mount and exports only mounted paths, using the `mountpoint`
export option. A missing export does not disable the remaining exports.
After recovering an export mount, run `sudo exportfs -ra` on the NAS.

The photos/documents backup requires both source mounts and the destination
mount. Before any `rsync --delete`, it also verifies the source dataset names
and that the destination is an XFS mount. A missing or incorrect mount fails
the job before backup contents can be modified.

Core root datasets and both boot partitions retain their required behavior.
Direct `nixos-rebuild` commands bypass the fleet storage check; use the fleet
deployment entry point for NAS changes.
