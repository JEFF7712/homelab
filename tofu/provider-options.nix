{
  flakePath,
  schemaPath,
  providerName,
}:
let
  flake = builtins.getFlake flakePath;
  pkgs = import flake.inputs.nixpkgs { system = builtins.currentSystem; };
  # Tofunix 3.0.1 accepts references for scalar/collection attributes but
  # omits the reference alternative for nested provider attributes. Preserve
  # runtime YAML decoding without weakening literal nested-object checks.
  generator = builtins.toFile "tofunix-generator.nix" (
    builtins.replaceStrings
      [ "then convertNestedType value.nested_type" ]
      [ ''then "(types.either referenceType (''${convertNestedType value.nested_type}))"'' ]
      (builtins.readFile "${flake.inputs.tofunix}/lib/generator.nix")
  );
  definitions = import ./providers.nix;
  definition = definitions.${providerName};
  schema =
    import ./schema.nix
      (builtins.fromJSON (builtins.readFile schemaPath)).provider_schemas.${definition.address};
  select = names: values: pkgs.lib.filterAttrs (name: _: builtins.elem name names) values;
in
import generator {
  inherit pkgs;
  lib = pkgs.lib;
  fullSpec = {
    name = providerName;
    provider = definition.name;
    inherit (definition) source version;
    spec = schema // {
      resource_schemas = select definition.resources schema.resource_schemas;
      data_source_schemas = select definition.data schema.data_source_schemas;
    };
  };
}
