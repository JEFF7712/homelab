# Agent Task: darkbit-local-release

Status: `complete`

Base commit: `f20d993b6bcd5959c6e79242d78fdb2c965a9bf9`

Checkpoint HEAD: `22521fa039a9023cdbf611655c6efe2cda19ed7c`

Owner: `codex`

Session: `darkbit-release-20261008`

Exported at: `2026-10-09T00:52:00.208081+00:00`

Current HEAD at export: `22521fa039a9023cdbf611655c6efe2cda19ed7c`

## Objective

Move Darkbit build and publication to server-owned local CI with scoped credentials and protected Flux promotion

## Acceptance criteria

- [x] Static OCI assembly deterministic and offline from verified pinned local base; invalid or missing inputs fail closed (.agent-state/evidence/darkbit-local-release/clean-assembly.json, runtime.json, supply-failure.json: fresh pinned Python container, no network, deterministic output, missing dependency rejected)
- [x] PR/fork validation has no publication authority; main push locally builds and publishes only apps/darkbit (publisher-scope.json: own read200, cross-app/upstream/delete403; pr-validation-boundary.json; published-release.json: main pipeline25 successful and independent digest matched)
- [x] Protected homelab promotion consumes verified release digest and Flux serves matching image (Protected homelabPR112 merged after validation688/689; live-delivery.json proves Flux revision1f553c7, exact pod imageID and matching assets via pod, Service endpoints and public HTTPS)
- [x] GitHub publisher retired only after verified local delivery (github-retirement.json proves disabled_manually, no repository publishing secrets; DarkbitPR4 merged and canonical workflow deleted after verified live delivery)

## Owned source

- `scripts/ci/static_release.py`
- `scripts/ci/applications.py`
- `config/ci/applications.json`
- `flake/modules/woodpecker.nix`
- `tests/test_static_release.py`
- `tests/test_application_ci.py`
- `docs/runbooks/application-ci.md`
- `scripts/registry/core.py`
- `scripts/registry/provision_darkbit.py`
- `tests/test_registry_darkbit_publisher.py`
- `registry/images.inventory.json`
- `registry/images.lock.json`
- `config/ci/operations.json`
- `secrets/darkbit-release.sops.env`
- `.sops.yaml`

## Remaining work

- None

## Verification

Current source fingerprint at export: `987a97664ffe0cb1196eda96cac505a8965b4339de1ef71c27e34f8acb1ddc39`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- `just check`: exit 69, freshness `stale`, superseded `true`, verified at `2026-10-09T00:04:52.004965+00:00`, source fingerprint `4f8a86dcda63713b83f1e3fdc1c3d80ef07b928bbc93089ba3c950584a7f184e`, evidence `/home/rupan/homelab/.worktrees/darkbit-local-release/.agent-state/evidence/checks/verify-cr49g3iy.log`
- `just check`: exit 0, freshness `stale`, superseded `false`, verified at `2026-10-09T00:11:26.031250+00:00`, source fingerprint `d52fc0b3116c79307c9488a26b4c54f1197246b6830380a8ad60c0acad7e20f6`, evidence `/home/rupan/homelab/.worktrees/darkbit-local-release/.agent-state/evidence/checks/verify-4ucw9_zs.log`

## Next action

Local delivery phase complete. Next phase: retain and pin clone/toolchain inputs and validate clean complete offline CI, then independent recovery

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
