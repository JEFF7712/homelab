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

The job also retains the development-shell derivation's available build-input
graph, including derivation metadata and already valid outputs. This is
different from retaining only the runtime closure: bootstrap hooks and check
builders can require paths that are not referenced by runtime outputs.
Graph enumeration failure stops publication; this does not build additional
package outputs or guarantee availability of inputs that were never fetched.

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
OpenTofu provider installation and kubeconform's default core-schema lookup
are additional inputs that must be retained before claiming complete offline
CI.

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
