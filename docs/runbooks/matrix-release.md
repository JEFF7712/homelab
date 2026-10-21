# Matrix releases

## Ownership and activation

The service source is `services/led-matrix`. Its registry repository remains
`apps/spotify-matrix`; renaming the service did not rename its publisher.
Flux owns `gitops/led-matrix/deployment.yaml`.

The `matrix-publish` protected CI operation builds an exact validated main SHA
on the native Docker builder, `homelab-04`, as the `service-release` account.
That account's key (`service_release_ssh_key`) has a forced command that runs
the worker deployed with homelab-04's NixOS generation, in the `docker` group
and without sudo, so a change to the worker side of `scripts/ci/service_release.py`
takes effect only after the held `ci-host` route deploys homelab-04. The same
applies to `bus-display-publish` and `lidarr-extended-publish`. It does not
modify the running matrix,
publish a Git branch, open a PR, or merge changes.

Before activating this operation, obtain authorization for publication and
shared infrastructure changes. Review the source diff and install the updated
immutable Woodpecker operation catalog using the existing CI platform procedure.
Merely merging the operation JSON does not activate the server-owned catalog.

Provision the protected Woodpecker file secret
`registry_spotify_matrix_publisher_auth_file` with an OCI auth document naming only
`registry.rupan.dev`, authenticated as `publisher-spotify-matrix`. Scope it to
the protected homelab deployment workflow using the same event and image-filter
settings as `registry_jarvis_publisher_auth_file`. Do not replace the global
Docker login, share another project's publisher, or give agents maintenance
credentials. Recover the existing credential through its authorized store if
available. If it cannot be recovered, rotate only this publisher using
[the registry rotation procedure](local-registry.md#rotating-a-publisher-credential).
Preserve all other identities, ACL entries, and file ownership.

The operation also uses the existing node reader password for pulling the
pinned Python base and `service_release_ssh_key` for its builder; it never
receives the fleet deployment key.
Reader and writer Docker configurations are separate temporary mode-0600
files outside the build context. They are deleted on completion or failure.
Failed build logs remain under `/persist/service-release-ci/` on `homelab-04`.

## Local checks

Enter `nix develop ./flake`. Prepare the service environment once per worktree:

```sh
env -u PYTHONPATH uv --directory services/led-matrix sync --frozen --no-dev --no-install-project
bash scripts/checks/led-matrix.sh
```

The first command may download locked dependencies. The check itself runs
offline, using the frozen service lock, and runs all service unit tests plus
the core streamer's Ruff and Pyright checks. It does not contact HA or the Pico.
The pinned shell supplies the Linux C++ runtime needed by NumPy's binary wheel;
the check sets that library path explicitly, including in clean CI containers.
`just check-changed` routes matrix source changes to this check. Matrix-only CI
changes prepare the environment and run this check, docs, and whitespace checks
without initializing unrelated OpenTofu providers or building the NixOS fleet.
Mixed changes retain their broader validation, including matrix checks.
Main's protected validation, periodic full checks, and infrastructure gates
continue to apply according to their normal scope.

## Publication and promotion

1. Publish the reviewed service fix through the normal branch and PR workflow,
   with the required authorization. Merge after protected validation succeeds.
2. Select `matrix-publish` as `deploy_to` when restarting successful current-main
   validation. The existing operation framework checks the main SHA, validation
   parent, operator identity, expiry, and completion receipts before dispatch.
3. The operation rejects an incorrect publisher identity and checks authentication
   before building. Authentication success does not prove repository write access;
   the first authorized push verifies that boundary.
4. The builder receives only the committed service Git archive. It pulls the
   digest-pinned local Python base with the reader, downloads the hashed runtime
   wheels, and builds with networking disabled. Wheel preparation currently
   requires access to the Python package index, as in the existing service build.
   This is not a cold offline supply-chain proof.
5. Before pushing, the builder runs runtime tests inside the resulting image
   with networking disabled, a read-only filesystem, dropped capabilities, and
   the image's non-root user. These tests exercise simulated inputs and local
   loopback endpoints; they do not connect another sender to the physical Pico.
6. The scoped writer publishes a pipeline-specific tag. Registry reads verify
   the tag's manifest, its immutable digest lookup, its configuration checksum,
   the tested local image ID, source revision label, platform, user, and entrypoint.
7. The operation prints the promotion patch base64 encoded between
   `BEGIN/END PROMOTION PATCH` markers (Woodpecker masks lines of multi-line
   secrets, which a raw JSON patch can contain) and keeps it with the receipt under
   `artifacts/service-release/spotify-matrix/`. The patch updates the matrix
   Deployment and its inventory and lock records together. `just ship` applies
   it on a fresh branch and ships it as the promotion PR.
8. Verify Flux reconciliation, the Deployment's actual image digest, readiness,
   HA connectivity, and acknowledged Pico frames. Exercise the affected mode
   and confirm the visible result before calling the change accepted.

Publication failure produces no Flux patch. An image and receipt alone do not
mean the change is deployed. A successful push followed by a lost receipt is an
uncertain outcome: inspect the pipeline-specific tag, builder logs, and receipt
before retrying. Never delete published content to make a retry pass.

For an authentication diagnostic, run
`python -m scripts.ci.service_release doctor spotify-matrix --auth-file PATH` only in the
authorized environment holding the protected publisher file. This is a read-only
check and never falls back to `~/.docker/config.json`.

## Recovery

Before activation retain the existing immutable CI catalog and protected
publisher credential for rollback, following the registry runbook. Disable the
new operation or restore the prior catalog if activation fails. Credential
rotation rollback requires restoring that publisher's previous verifier and
matching secret, then rechecking authentication.

For application rollback, revert the focused Flux promotion commit, preserving
the prior image pin and registry records together. Verify the old image is
available before merging the revert and confirm the sender resumes healthy
acknowledged frames. Keep laptop senders stopped throughout.

## Other services

`scripts/ci/service_release.py` holds one `Release` entry per service built
from this repository: `spotify-matrix`, `bus-display`, and
`lidarr-extended`, each with its own scoped publisher, consumer manifests,
lock record, and runtime contract, published by `<name>-publish` (the matrix
keeps `matrix-publish`). bus-display builds offline from its retained
wheelhouse, which the coordinator fetches and verifies before archiving.
lidarr-extended builds on a vendor base that installs packages at build time,
so its build has network access and no user or entrypoint contract. Add a
service by adding a `Release`, its catalog operation, and a `ship` route, then
provision its publisher with `just registry-provision-publisher apps/<name>`.
