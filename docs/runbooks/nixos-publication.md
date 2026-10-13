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
