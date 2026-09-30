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
