{ pkgs, tofunix }:
let
  tofunixLib = import "${tofunix}/lib" { inherit pkgs; };
in
# Keep backend/provider declarations in their small runtime HCL files. Tofunix
# emits resource lists; canonicalize named resources to the JSON object form
# consumed by the existing Access inventory checker.
{
  provider,
  modules,
  extra ? { },
}:
let
  evaluated =
    (tofunixLib.mkModule {
      moduleConfig.imports = [ (./. + "/${provider}/generated/provider.nix") ] ++ modules;
    }).config.final;
  normalize =
    value:
    if builtins.isList value then
      map normalize value
    else if builtins.isAttrs value then
      let
        attrs = pkgs.lib.mapAttrs (_: normalize) value;
      in
      builtins.removeAttrs attrs [
        "preconditions"
        "postconditions"
      ]
      // pkgs.lib.optionalAttrs (attrs ? preconditions) { precondition = attrs.preconditions; }
      // pkgs.lib.optionalAttrs (attrs ? postconditions) { postcondition = attrs.postconditions; }
    else
      value;
  named = pkgs.lib.mapAttrs (_: instances: normalize (pkgs.lib.mergeAttrsList instances));
in
builtins.removeAttrs evaluated [ "terraform" ]
// pkgs.lib.optionalAttrs (evaluated ? resource) { resource = named evaluated.resource; }
// pkgs.lib.optionalAttrs (evaluated ? data) { data = named evaluated.data; }
// extra
