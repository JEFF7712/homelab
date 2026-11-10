# Garage 1.x for nas-01's object store (flake/modules/nas-s3.nix) and the
# niks3 cache (flake/modules/niks3-cache.nix).
#
# nixpkgs marks garage 1.3.1 insecure because the 1.x series is end of life.
# Moving to Garage 2 is a major upgrade of the store that holds OpenTofu state,
# with its own data migration, so it is a separate, planned change. Until then
# this accepts 1.3.1 explicitly and in one place. meta is not part of the
# derivation, so the package and its store path are unchanged.
pkgs:
pkgs.garage.overrideAttrs (old: {
  meta = old.meta // {
    knownVulnerabilities = [ ];
  };
})
