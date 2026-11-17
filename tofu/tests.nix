{ pkgs, tofunix }:
let
  inherit (pkgs) lib;
  render = import ./lib.nix { inherit pkgs tofunix; };
  rejects = value: !(builtins.tryEval (builtins.deepSeq value true)).success;
  networks = (import ../inventory { inherit lib; }).networks;
  inventory =
    module:
    import ./switches {
      inherit lib networks;
      extraModules = [ module ];
    };
  provider =
    resource:
    render {
      provider = "switches";
      modules = [ { inherit resource; } ];
    };
  cloudflare =
    resource:
    render {
      provider = "cloudflare";
      modules = [ { inherit resource; } ];
    };
  tests = {
    providerDocumentationExamples =
      let
        schema = import ./schema.nix {
          description = ''Example: {"cf-access-client-secret": "<upstream>"}'';
          nested.description = "Ordinary provider documentation";
          attributes.secret.type = "string";
        };
      in
      schema.description == ''Example: {"cf-access-client-secret": "<example>"}''
      && schema.nested.description == "Ordinary provider documentation"
      && schema.attributes.secret.type == "string";
    missingCableSwitch = rejects (inventory {
      switches.upstairs.ports."4".endpoint.node = lib.mkForce "switch-missing";
    });
    nonreciprocalCable = rejects (inventory {
      switches.downstairs.ports."2".endpoint.interface = lib.mkForce "port5";
    });
    duplicateCableTarget = rejects (inventory {
      switches.upstairs.ports."1".endpoint = lib.mkForce {
        node = "nas-01";
        interface = "switch-uplink";
        uncertain = false;
      };
    });
    metadataDoesNotChangeRuntime =
      let
        before = inventory { };
        after = inventory { switches.upstairs.ports."8".role = lib.mkForce "Updated management label"; };
        doc = import ./switches/documentation.nix {
          inherit lib;
          inventory = after;
        };
      in
      before.runtime == after.runtime
      && lib.hasInfix "Updated management label" doc
      && !(after.runtime.switches.upstairs.ports."8" ? role);
    declaredTopology =
      let
        nodes = import ./switches/topology.nix {
          inherit lib;
          inventory = inventory { };
        };
      in
      nodes.switch-upstairs.interfaces.port4.physicalConnections == [
        {
          node = "switch-downstairs";
          interface = "port2";
        }
      ]
      && nodes.switch-upstairs.interfaces.port2.physicalConnections == [ ]
      && nodes ? nas-01;
    validInventory = !rejects (inventory { });
    wrongPVIDType = rejects (inventory {
      switches.upstairs.ports."8".pvid = lib.mkForce "10";
    });
    absentPVID = rejects (inventory {
      switches.upstairs.ports."8".pvid = lib.mkForce 98;
    });
    PVIDOutsidePortMembership = rejects (inventory {
      switches.upstairs.ports."8".pvid = lib.mkForce 20;
    });
    invalidPort = rejects (inventory {
      switches.downstairs.vlans."20".tagged_ports = lib.mkForce [ 6 ];
    });
    overlappingMembership = rejects (inventory {
      switches.upstairs.vlans."20".tagged_ports = lib.mkForce [
        3
        7
      ];
    });
    duplicateMembership = rejects (inventory {
      switches.upstairs.vlans."20".tagged_ports = lib.mkForce [
        3
        3
      ];
    });
    unknownProviderArgument = rejects (provider {
      tplinkeasysmart_port.probe = {
        port = 1;
        typo = true;
      };
    });
    wrongProviderType = rejects (provider {
      tplinkeasysmart_port.probe.port = false;
    });
    wrongNestedCloudflareType = rejects (cloudflare {
      cloudflare_ruleset.probe = {
        kind = "zone";
        name = "probe";
        phase = "http_request_firewall_custom";
        rules = [
          {
            action = "block";
            expression = "true";
            enabled = 12;
          }
        ];
      };
    });
    deferredNestedCloudflareValue =
      !rejects (cloudflare {
        cloudflare_zero_trust_tunnel_cloudflared_config.probe = {
          account_id = "account";
          tunnel_id = "tunnel";
          config.ingress = "\${local.ingress}";
        };
      });
    sharedVLANs =
      let
        changed = import ./switches {
          inherit lib;
          networks = networks // {
            clients = networks.clients // {
              tag = 22;
            };
          };
        };
      in
      changed.switches.upstairs.ports."7".pvid == 22
      && changed.switches.downstairs.ports."3".pvid == 22
      && changed.switches.upstairs.vlans ? "22"
      && !(changed.switches.upstairs.vlans ? "20");
    identityPrecondition =
      let
        cfg = (import ./. { inherit pkgs tofunix; }).outputs."switches/main.tf.json";
      in
      builtins.length cfg.resource.tplinkeasysmart_vlan_mode.switch.lifecycle.precondition == 1
      && !(cfg.resource.tplinkeasysmart_vlan_mode.switch.lifecycle ? preconditions);
  };
in
assert lib.assertMsg (builtins.all (value: value) (
  builtins.attrValues tests
)) "Tofunix regressions failed: ${builtins.toJSON tests}";
pkgs.writeText "tofu-type-checks.json" (builtins.toJSON tests)
