{ flakePath, schemaPath }:
let
  flake = builtins.getFlake flakePath;
  pkgs = import flake.inputs.nixpkgs { system = builtins.currentSystem; };
  tofunix = import "${flake.inputs.tofunix}/lib" { inherit pkgs; };
  schema = builtins.fromJSON (builtins.readFile schemaPath);
  providers = builtins.fromJSON (builtins.readFile ./ci-providers.json);
  versions = pkgs.lib.unique (
    map (entry: entry.version) (
      builtins.filter (entry: entry.address == "registry.opentofu.org/browningluke/opnsense") providers
    )
  );
in
assert builtins.length versions == 1;
tofunix.generateOptionsForProvider {
  name = "browningluke-opnsense";
  provider = "opnsense";
  source = "browningluke/opnsense";
  version = builtins.head versions;
  spec = schema.provider_schemas."registry.opentofu.org/browningluke/opnsense";
}
