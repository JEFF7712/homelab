# GitHub release synchronization

Forgejo is authoritative for nix-agent source and published releases. The Git
push mirror copies branches and tags. The separate `github-release-mirror`
timer on homelab-04 copies published version release notes and attached assets
to GitHub every five minutes, with up to 30 seconds of scheduling jitter.

## Ownership

- `flake/modules/release-mirror.nix`: service and timer.
- `flake/hosts/homelab-04/default.nix`: explicit `JEFF7712/nix-agent` enrollment,
  starting at v0.13.0. Earlier GitHub releases are left as they are.
- `flake/flake.nix` and `flake/flake.lock`: the `forgejo-release-mirror` v0.1.0
  input, pinned to its release commit and sharing the fleet's nixpkgs input.
  Reconciliation and asset verification live in the
  [standalone package](https://github.com/JEFF7712/forgejo-release-mirror).
- `repo-monitor.env` in `secrets/hosts/homelab-04.sops.yaml`: credentials
  shared with the repo monitor, installed at `/run/secrets/repo-monitor.env`.
  `FORGEJO_READ_TOKEN` reads the private source repository; `GITHUB_TOKEN` must
  have repository contents write permission for the destination. Never print
  these values or copy them into the Nix store.

Only published version tags are eligible. Draft releases and temporary test
tags are ignored. Each GitHub tag must already exist and resolve to the same
commit as Forgejo, including annotated tags. A delayed Git mirror causes a
failed run that retries on the next timer execution.

New GitHub releases remain drafts until every source attachment has been
downloaded, its size checked, uploaded, and its SHA-256 digest verified.
Repeated executions skip matching assets and unchanged metadata. A failed
upload leaves a draft that the next execution resumes. Notes and prerelease
flags follow Forgejo. Differing assets in an already published GitHub release
cause an explicit failure instead of replacing public artifacts. Releases,
tags, and extra destination assets are never deleted by synchronization.

## Verification and operations

Run the service integration tests in the pinned development environment:

```sh
nix develop ./flake -c python -m unittest tests.test_release_mirror -v
```

These evaluate homelab-04's actual unit and timer, verify its release pin and
arguments, and build and exercise the installed executable without credentials.
The package build runs the upstream regression suite; the homelab does not
keep a second copy of the reconciliation implementation or its unit tests.

The existing unit name, environment file, repository, minimum version, and
timer schedule remain authoritative in `flake/modules/release-mirror.nix`
and the host configuration. The service passes `--forgejo-origin`,
`--source-repository`, and `--destination-repository` explicitly.

For a live read-only preview, run the unit's packaged command with `--dry-run`
and the same runtime environment file through an authorized CI/operator path.
Never copy token values into command arguments or logs. A preview reads tag
and release metadata but does not download or verify asset bytes or test write
permission. Routine agents observe the service through `agent-observer`; they
do not start privileged units directly.

Deploy changes through the scoped fleet workflow in
[NixOS host deployment](nixos-host-deployment.md). After deployment, inspect
`systemctl status github-release-mirror.timer` and
`journalctl -u github-release-mirror.service`. A successful run logs one JSON
summary with each tag and the number of uploaded assets. An unchanged second
run should upload zero assets and update no metadata. Check GitHub release
notes and asset hashes independently before declaring publication complete.

To pause synchronization, disable its timer. To retire it declaratively, set
`homelab.releaseMirror.enable = false` and deploy the reviewed host change.
Existing GitHub releases remain available.
