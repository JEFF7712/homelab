{ lib, inventory }:
let
  numericKeys = attrs: builtins.sort (a: b: lib.toInt a < lib.toInt b) (builtins.attrNames attrs);
  numbers = values: if values == [ ] then "None" else lib.concatMapStringsSep ", " toString values;
  safe = text: builtins.replaceStrings [ "|" "\n" ] [ "\\|" " " ] text;
  row = fields: "| ${lib.concatStringsSep " | " fields} |";
  table =
    headers: rows:
    lib.concatStringsSep "\n" (
      [
        (row headers)
        (row (map (_: "---") headers))
      ]
      ++ map row rows
    );
  vlansFor =
    cfg: key: field:
    map lib.toInt (
      builtins.filter (vlan: builtins.elem (lib.toInt key) cfg.vlans.${vlan}.${field}) (
        numericKeys cfg.vlans
      )
    );
  ports =
    name:
    let
      cfg = inventory.switches.${name};
    in
    table [ "Port" "Role" "Untagged VLANs" "Tagged VLANs" "PVID" ] (
      map (key: [
        key
        (safe cfg.ports.${key}.role)
        (numbers (vlansFor cfg key "untagged_ports"))
        (numbers (vlansFor cfg key "tagged_ports"))
        (toString cfg.ports.${key}.pvid)
      ]) (numericKeys cfg.ports)
    );
  upstairs = inventory.switches.upstairs;
  cables = lib.concatLists (
    lib.mapAttrsToList (
      name: cfg:
      map (
        key:
        let
          endpoint = cfg.ports.${key}.endpoint;
        in
        [
          "${name} port ${key}"
          (if endpoint == null then "No cable declared" else "${endpoint.node}/${endpoint.interface}")
          (if endpoint != null && endpoint.uncertain then "Unverified port order" else "Declared")
        ]
      ) (numericKeys cfg.ports)
    ) inventory.switches
  );
in
builtins.replaceStrings
  [ "@PORTS_UPSTAIRS@" "@PORTS_DOWNSTAIRS@" "@VLANS_UPSTAIRS@" "@PVIDS_UPSTAIRS@" "@CABLES@" ]
  [
    (ports "upstairs")
    (ports "downstairs")
    (table [ "VLAN" "Tagged ports" "Untagged ports" ] (
      map (key: [
        key
        (numbers upstairs.vlans.${key}.tagged_ports)
        (numbers upstairs.vlans.${key}.untagged_ports)
      ]) (numericKeys upstairs.vlans)
    ))
    (table [ "Port" "PVID" ] (
      map (key: [
        key
        (toString upstairs.ports.${key}.pvid)
      ]) (numericKeys upstairs.ports)
    ))
    (table [ "Switch port" "Cable endpoint" "Evidence" ] cables)
  ]
  (builtins.readFile ./port-map.md.in)
