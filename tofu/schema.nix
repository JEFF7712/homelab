# Preserve useful provider docs without copying credential-shaped examples into
# generated source. This touches descriptions only, never schema/config values.
value:
let
  sanitizeDescription =
    text:
    let
      parts = builtins.match ''(.*"cf-access-client-secret": ")[^"]+(".*)'' text;
    in
    if parts == null then text else builtins.elemAt parts 0 + "<example>" + builtins.elemAt parts 1;
  clean =
    value:
    if builtins.isList value then
      map clean value
    else if builtins.isAttrs value then
      builtins.mapAttrs (
        name: child:
        if name == "description" && builtins.isString child then sanitizeDescription child else clean child
      ) value
    else
      value;
in
clean value
