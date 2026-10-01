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
