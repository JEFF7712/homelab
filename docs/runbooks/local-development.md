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

Forgejo and S3 application mounts use `nofail`, so missing application storage
does not send the NAS into emergency mode. Their services still require their
mounts and cannot start without them. Core storage and boot mounts retain their
required behavior. Direct `nixos-rebuild` commands bypass the fleet storage
check; use the fleet deployment entry point for NAS changes.
