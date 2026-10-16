# Agent Task: spotify-matrix-ha

Status: `active`

Base commit: `2d198919cd918705f390c2a7e6625107a69c5d62`

Checkpoint HEAD: `2d198919cd918705f390c2a7e6625107a69c5d62`

Owner: `codex`

Session: `spotify-matrix-ha-20261016`

Exported at: `2026-10-16T18:22:39.415438+00:00`

Current HEAD at export: `2d198919cd918705f390c2a7e6625107a69c5d62`

## Objective

Prepare an HA event-driven Spotify matrix renderer and reviewable homelab deployment without extra Spotify polling

## Acceptance criteria

- [x] HA cached metadata and events drive acknowledged Pico frames without Spotify Web API calls (.agent-state/spotify-matrix/final-container-live.json; services/spotify-matrix/DEPLOYMENT.md)
- [x] Offline recovery tests, container build, and manifests validate (.agent-state/evidence/checks/verify-065zwfs5.json; .agent-state/evidence/checks/verify-0skhzdve.json; services/spotify-matrix/build-metadata.json)

## Owned source

- `services/spotify-matrix`
- `gitops/spotify-matrix`
- `gitops/clusters/homelab-01/spotify-matrix.yaml`
- `gitops/clusters/homelab-01/kustomization.yaml`
- `registry/images.inventory.json`
- `registry/images.lock.json`
- `tofu/opnsense/homelab.auto.tfvars`
- `docs/agent-tasks/spotify-matrix-ha.md`

## Remaining work

- Obtain approval for publication, dedicated HA credential provisioning, DHCP reservation and homelab rollout
- Publish qualified image and focused GitOps changes after approval
- Verify live cluster routing, Cilium, recovery, and operation with laptop off

## Verification

Current source fingerprint at export: `9bd61c1b2af973f57e28f7346bdeb746894287a8b4f073a072e94d00526d2b86`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- `bash scripts/checks/gitops.sh`: exit 0, freshness `stale`, superseded `false`, verified at `2026-10-16T18:18:15.175325+00:00`, source fingerprint `0572a61e808ad5c2af26e920e10c9c4d85cede342e4993732e84f94df38613fd`, evidence `/home/rupan/homelab/.worktrees/spotify-matrix-ha/.agent-state/evidence/checks/verify-0skhzdve.log`
- `bash -c 'cd services/spotify-matrix && env -u PYTHONPATH uv run --with ruff --with pyright sh -c "ruff check ha_source.py ha_matrix_streamer.py test_ha_source.py && ruff format --check ha_source.py ha_matrix_streamer.py test_ha_source.py && pyright ha_source.py ha_matrix_streamer.py && python -m unittest discover -v"'`: exit 0, freshness `stale`, superseded `true`, verified at `2026-10-16T18:18:54.307785+00:00`, source fingerprint `0572a61e808ad5c2af26e920e10c9c4d85cede342e4993732e84f94df38613fd`, evidence `/home/rupan/homelab/.worktrees/spotify-matrix-ha/.agent-state/evidence/checks/verify-aws_l8bn.log`
- `bash -c 'cd services/spotify-matrix && env -u PYTHONPATH uv run --with ruff --with pyright sh -c "ruff check ha_source.py ha_matrix_streamer.py test_ha_source.py && ruff format --check ha_source.py ha_matrix_streamer.py test_ha_source.py && pyright ha_source.py ha_matrix_streamer.py && python -m unittest discover -v"'`: exit 0, freshness `stale`, superseded `false`, verified at `2026-10-16T18:19:54.752690+00:00`, source fingerprint `29c270dad93b1ec11c2531cfbe6fdd6988987582ca121d16110c6c4195b3156d`, evidence `/home/rupan/homelab/.worktrees/spotify-matrix-ha/.agent-state/evidence/checks/verify-065zwfs5.log`
- `bash -c 'set -euo pipefail; bash scripts/checks/tofu.sh; CHECK_FROM_ALL=1 SKIP_TESTS=1 bash scripts/checks/home-assistant.sh; python -m scripts.checks.docs; python scripts/checks/whitespace.py; nix flake check "path:.?dir=flake" --no-write-lock-file'`: exit 0, freshness `stale`, superseded `false`, verified at `2026-10-16T18:22:10.711809+00:00`, source fingerprint `29c270dad93b1ec11c2531cfbe6fdd6988987582ca121d16110c6c4195b3156d`, evidence `/home/rupan/homelab/.worktrees/spotify-matrix-ha/.agent-state/evidence/checks/verify-umrdzhnu.log`

## Next action

Obtain approval for image publication, protected HA credential provisioning, DHCP reservation, and Flux rollout

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
