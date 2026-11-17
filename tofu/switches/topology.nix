{ lib, inventory }:
let
  ports = lib.concatLists (
    lib.mapAttrsToList (
      name: cfg:
      lib.mapAttrsToList (key: port: {
        source = "switch-${name}";
        interface = "port${key}";
        inherit (port) endpoint;
      }) cfg.ports
    ) inventory.switches
  );
  known = builtins.filter (port: port.endpoint != null && !port.endpoint.uncertain) ports;
  external = lib.foldl' lib.recursiveUpdate { } (
    map (port: {
      ${port.endpoint.node} = {
        deviceType = "device";
        interfaces.${port.endpoint.interface} = {
          virtual = false;
        };
      };
    }) (builtins.filter (port: !(lib.hasPrefix "switch-" port.endpoint.node)) known)
  );
  switches = lib.mapAttrs' (
    name: cfg:
    lib.nameValuePair "switch-${name}" {
      name = "${name} ${cfg.model}";
      deviceType = "switch";
      hardware.info = "Management ${cfg.host}; declared port map";
      interfaces = lib.mapAttrs' (
        key: port:
        lib.nameValuePair "port${key}" {
          virtual = false;
          # A managed VLAN switch must not inherit the library's unmanaged-switch
          # default, which merges every port into the same network.
          sharesNetworkWith = lib.mkForce [ ];
          physicalConnections = lib.optional (port.endpoint != null && !port.endpoint.uncertain) {
            inherit (port.endpoint) node interface;
          };
        }
      ) cfg.ports;
    }
  ) inventory.switches;
in
lib.recursiveUpdate external switches
