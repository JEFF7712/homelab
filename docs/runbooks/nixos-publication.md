# NixOS artifact publication

The protected `nixos-publish` operation builds Forgejo-authoritative
`JEFF7712/nixos-config` source and publishes the laptop system and installer
ISO, source inputs, and derivation closures to Attic. Both target sets must
report successful publication before the operation receipt establishes
acceptance. Existing narinfo records alone are insufficient.

Homelab repository 1 uses a 180-minute pipeline budget. Its previous
60-minute cap canceled publication #1368 while the disposable builder was
still hydrating the laptop closure from the internal cache. This budget
applies to all pipelines in that infrastructure repository because
Woodpecker enforces the cap per repository. Application repository budgets,
runner resource limits, source admission, and deployment serialization stay
under their existing policies.

Apply `config/ci/woodpecker-repository.json` through the authorized
Woodpecker repository API and read back `timeout` after provisioning. Start
publication only from freshly validated current main. Retain source SHA,
the successful operation number, both publication markers, and its signed
operation receipt. A longer time budget does not establish offline build
or independent restoration acceptance.

## Laptop deployment branch

The laptop runs comin against `deploy/laptop` in `JEFF7712/nixos-config` with
local builds disabled, so that branch may only name a commit whose laptop
closure is already in Attic. `scripts/ci/nixos_deploy.py` owns it:

- `nixos-lock-update` (`weekly` cron) runs `nix flake update` on main, evaluates
  the laptop toplevel, and defers when `nix-cascade-guard` reports an uncached
  nixpkgs. It opens a single `flake-lock/weekly` PR and never replaces an open
  one. Then it does what `nixos-deploy` does.
- `nixos-deploy` (`nightly` cron) finishes the open update PR: it waits for
  `ci/woodpecker/application-validation`, publishes the PR head's closure,
  and merges. A failed or unmergeable candidate is closed and the run fails;
  the next weekly run proposes a fresh one. It then publishes the validated
  main tip and fast-forwards `deploy/laptop` to it. It never force-pushes, and a
  deploy branch that is not an ancestor of main fails the run.

Both push as `homelab-publisher` with `forgejo_publish_token`, which needs
write access to `JEFF7712/nixos-config`. Branch protection on `deploy/*` must
restrict pushes to that identity, because comin deploys whatever the branch
names. For an urgent fix, trigger `deploy_to=nixos-lock-update` from a
successful main validation instead of waiting for Sunday.
