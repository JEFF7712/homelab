{
  lib,
  options,
  config,
  ...
}:
let
  inherit (lib)
    mkOption
    mkOptionType
    types
    isType
    literalExpression
    hasPrefix
    hasSuffix
    ;
  unsetType = mkOptionType {
    name = "unset";
    description = "unset";
    descriptionClass = "noun";
    check = isType "unset";
  };
  unset = {
    _type = "unset";
  };
  unsetOr =
    typ:
    (types.either unsetType typ)
    // {
      inherit (typ) description getSubOptions;
    };
  mkUnsetOption =
    args:
    mkOption args
    // {
      type = unsetOr args.type;
      default = args.default or unset;
      defaultText = literalExpression "unset";
    };
  referenceType = mkOptionType {
    name = "reference";
    description = "reference";
    descriptionClass = "noun";
    check =
      val:
      (builtins.isString val || val ? __toString)
      && hasPrefix "\${" (builtins.toString val)
      && hasSuffix "}" (builtins.toString val);
  };
in
rec {
  _file = "generated tplinkeasysmart provider";
  config.terraform.required_providers.tplinkeasysmart = {
    source = "registry.terraform.io/davidlukac/tplinkeasysmart";
    version = "0.32.7";
  };
  config.generated.provider."tplinkeasysmart" = mkUnsetOption {
    type = types.attrsOf (
      types.submodule (
        { name, ... }: {
          _file = "generated tplinkeasysmart provider";
          options = {
            _name = mkOption {
              internal = true;
              type = types.str;
              default = name;
            };
            _version = mkOption {
              internal = true;
              type = types.str;
              default = "0.32.7";
            };
            alias = mkUnsetOption {
              type = types.str;
              default = if name != "default" then name else unset;
              description = ''
                Alias for this terraform provider. Automatically set to "${name}".
              '';
            };
            id = mkOption {
              readOnly = true;
              type = types.str;
              default = "\${tplinkeasysmart.${name}}";
              defaultText = literalExpression "\"\${tplinkeasysmart.${name}}\"";
              description = ''
                ID of provider, read only. Only returns a terraform reference string.
              '';
            };
            "allow_unverified_firmware" = mkUnsetOption {
              type = (types.either referenceType (types.bool));
              description = "Write to switches whose firmware build this provider version was not verified against. Defaults to false: reads and imports always work, but every write is refused on an unverified build, because a newer firmware may have changed the protocol. Verified builds: 20230218, 20250710. Falls back to TPLINK_EASYSMART_ALLOW_UNVERIFIED_FIRMWARE (`true` or `false`). Can be overridden per switch in the `switches` map. ";
            };
            "host" = mkUnsetOption {
              type = (types.either referenceType (types.str));
              description = "IPv4 address or hostname of the switch. Falls back to TPLINK_EASYSMART_HOST. Optional when `mac` is set. ";
            };
            "mac" = mkUnsetOption {
              type = (types.either referenceType (types.str));
              description = "MAC address of the switch (`aa:bb:cc:dd:ee:ff`, colons or hyphens, any case; `system_info` reports it as `mac_address`). It pins the identity: a device with another MAC at `host` is never written to. Without `host`, or when `host` does not answer, the switch is found by a layer-2 discovery on the first request, so its address may change (for example under DHCP). Discovery needs the same layer-2 segment; use `host` as well when Terraform runs elsewhere. Falls back to TPLINK_EASYSMART_MAC. ";
            };
            "password" = mkUnsetOption {
              type = (types.either referenceType (types.str));
              description = "Admin password. Falls back to TPLINK_EASYSMART_PASSWORD. ";
            };
            "switches" = mkUnsetOption {
              type = (
                types.either referenceType (
                  types.attrsOf (
                    types.submodule {
                      options = {
                        "allow_unverified_firmware" = mkUnsetOption {
                          type = (types.either referenceType (types.bool));
                          description = "Overrides the provider-level `allow_unverified_firmware` for this switch. ";
                        };
                        "host" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "IPv4 address or hostname of the switch. Optional when `mac` is set; at least one of the two is required. ";
                        };
                        "mac" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "MAC address of the switch (`aa:bb:cc:dd:ee:ff`, colons or hyphens, any case; `system_info` reports it as `mac_address`). It pins the identity: a device with another MAC at `host` is never written to. Without `host`, or when `host` does not answer, the switch is found by a layer-2 discovery on the first request, so its address may change (for example under DHCP). Discovery needs the same layer-2 segment; use `host` as well when Terraform runs elsewhere. ";
                        };
                        "password" = mkOption {
                          type = (types.either referenceType (types.str));
                          description = "Admin password. ";
                        };
                        "timeout_seconds" = mkUnsetOption {
                          type = (types.either referenceType (types.number));
                          description = "Seconds to wait for a reply before retrying a request. Defaults to the provider-level `timeout_seconds`, then 3. ";
                        };
                        "username" = mkOption {
                          type = (types.either referenceType (types.str));
                          description = "Admin user name. ";
                        };

                      };
                    }
                  )
                )
              );
              description = ''Switches managed by this provider, by name. Select one on a resource or data source with `switch = "<name>"`. May be combined with `host`, which then names the default switch used when `switch` is omitted. '';
            };
            "timeout_seconds" = mkUnsetOption {
              type = (types.either referenceType (types.number));
              description = "Seconds to wait for a reply before retrying a request. Defaults to 3. ";
            };
            "username" = mkUnsetOption {
              type = (types.either referenceType (types.str));
              description = "Admin user name. Falls back to TPLINK_EASYSMART_USERNAME. ";
            };

          };
        }
      )
    );
    description = "Manages TP-Link Easy Smart switches (TL-SG108E v6) over their native UDP management protocol. The provider must run on the same layer-2 network as the switches. Configure one switch with `host`, `username` and `password` (use a provider alias per switch), or any number with the `switches` map and select one per resource with `switch`. Do not run two clients against the same switch at once.";
  };

  # required for looking up the suboptions
  config.generated = {
    resource = {
      "tplinkeasysmart_port" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated tplinkeasysmart provider";
              options = {
                _name = mkOption {
                  internal = true;
                  type = types.str;
                  default = name;
                };
                count = mkUnsetOption {
                  type = types.either referenceType types.int;
                  description = ''
                    Used to create multiple instances of this resource/data.
                  '';
                };
                for_each = mkUnsetOption {
                  type = types.oneOf [
                    referenceType
                    (types.attrsOf types.str)
                    (types.listOf types.str)
                  ];
                  description = ''
                    Used to create multiple instances of this resource/data from a map or set.
                  '';
                };
                depends_on = mkUnsetOption {
                  type = types.listOf (types.coercedTo types.attrs builtins.toString types.str);
                  description = ''
                    Explicit dependency references. Use this to declare hidden dependencies that OpenTofu cannot automatically infer.
                  '';
                };
                provider = mkUnsetOption {
                  type = types.str;
                  example = "aws.west";
                  description = ''
                    Provider reference for selecting a non-default provider configuration.
                  '';
                };

                lifecycle = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      create_before_destroy = mkUnsetOption {
                        type = types.bool;
                        description = ''
                          When `true`, the new replacement object is created first before destroying the old one.
                        '';
                      };
                      prevent_destroy = mkUnsetOption {
                        type = types.either referenceType types.bool;
                        description = ''
                          When `true`, OpenTofu will reject any plan that would destroy this resource.
                        '';
                      };
                      ignore_changes = mkUnsetOption {
                        type = types.either (types.enum [ "all" ]) (types.listOf types.str);
                        description = ''
                          List of attribute names to ignore during updates, or "all" to ignore all attributes.
                        '';
                      };
                      replace_triggered_by = mkUnsetOption {
                        type = types.listOf types.str;
                        description = ''
                          List of resource or attribute references that trigger replacement of this resource when changed.
                        '';
                      };
                      preconditions = mkUnsetOption {
                        type = types.listOf (
                          types.submodule {
                            options = {
                              condition = mkOption {
                                type = types.either referenceType types.str;
                                description = ''
                                  A condition expression that must evaluate to true for the check to pass.
                                '';
                              };
                              error_message = mkOption {
                                type = types.str;
                                description = ''
                                  Error message displayed when the condition evaluates to false.
                                '';
                              };
                            };
                          }
                        );
                        description = ''
                          Preconditions that must be met before applying changes.
                        '';
                      };
                      postconditions = mkUnsetOption {
                        type = types.listOf (
                          types.submodule {
                            options = {
                              condition = mkOption {
                                type = types.either referenceType types.str;
                                description = ''
                                  A condition expression that must evaluate to true for the check to pass.
                                '';
                              };
                              error_message = mkOption {
                                type = types.str;
                                description = ''
                                  Error message displayed when the condition evaluates to false.
                                '';
                              };
                            };
                          }
                        );
                        description = ''
                          Postconditions that must be met after applying changes.
                        '';
                      };
                      destroy = mkUnsetOption {
                        type = types.bool;
                        description = ''
                          When `false`, OpenTofu will "forget" the resource instead of destroying it.
                        '';
                      };
                      enabled = mkUnsetOption {
                        type = types.either referenceType types.bool;
                        description = ''
                          Controls whether a resource is created and managed by OpenTofu.
                          When `false`, the resource is excluded as if it did not exist.
                        '';
                      };
                    };
                  };
                  description = ''
                    Lifecycle customizations for this resource.
                  '';
                };

                "actual_speed" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Currently negotiated speed, or link-down. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "allow_disruptive" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Allow changing a port that has a link up or is a LAG member. Defaults to false. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Administrative state. Defaults to true. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "flow_control" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Flow control. Defaults to false. It can be true only with speed `auto` or `1000Mbps-full`: at a fixed 10 or 100 Mbps speed the switch accepts the setting but keeps flow control off, so that combination is refused while planning. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "port" = mkOption {
                  type = (types.either referenceType (types.number));
                  description = "Port number (1-based). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "speed" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "One of: auto, 10Mbps-half, 10Mbps-full, 100Mbps-half, 100Mbps-full, 1000Mbps-full. Defaults to auto. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "switch" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Name of the switch (a key of the provider's `switches` map) this object lives on. Omit it to use the provider's own switch (`host`), or the only configured switch. Changing it replaces the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configuration of one physical port (Switching > Port Setting): admin state, speed and flow control. Import with the port number. **Safety:** by default the provider refuses to change a port that has a link up or is in a LAG, because that can cut the uplink or the management path; set `allow_disruptive = true` to override. On a LAG member the switch refuses half duplex but does not keep the members' speed, flow control or enabled state alike, so change all members together. Destroying only removes it from state. ";
      };
      "tplinkeasysmart_system" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated tplinkeasysmart provider";
              options = {
                _name = mkOption {
                  internal = true;
                  type = types.str;
                  default = name;
                };
                count = mkUnsetOption {
                  type = types.either referenceType types.int;
                  description = ''
                    Used to create multiple instances of this resource/data.
                  '';
                };
                for_each = mkUnsetOption {
                  type = types.oneOf [
                    referenceType
                    (types.attrsOf types.str)
                    (types.listOf types.str)
                  ];
                  description = ''
                    Used to create multiple instances of this resource/data from a map or set.
                  '';
                };
                depends_on = mkUnsetOption {
                  type = types.listOf (types.coercedTo types.attrs builtins.toString types.str);
                  description = ''
                    Explicit dependency references. Use this to declare hidden dependencies that OpenTofu cannot automatically infer.
                  '';
                };
                provider = mkUnsetOption {
                  type = types.str;
                  example = "aws.west";
                  description = ''
                    Provider reference for selecting a non-default provider configuration.
                  '';
                };

                lifecycle = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      create_before_destroy = mkUnsetOption {
                        type = types.bool;
                        description = ''
                          When `true`, the new replacement object is created first before destroying the old one.
                        '';
                      };
                      prevent_destroy = mkUnsetOption {
                        type = types.either referenceType types.bool;
                        description = ''
                          When `true`, OpenTofu will reject any plan that would destroy this resource.
                        '';
                      };
                      ignore_changes = mkUnsetOption {
                        type = types.either (types.enum [ "all" ]) (types.listOf types.str);
                        description = ''
                          List of attribute names to ignore during updates, or "all" to ignore all attributes.
                        '';
                      };
                      replace_triggered_by = mkUnsetOption {
                        type = types.listOf types.str;
                        description = ''
                          List of resource or attribute references that trigger replacement of this resource when changed.
                        '';
                      };
                      preconditions = mkUnsetOption {
                        type = types.listOf (
                          types.submodule {
                            options = {
                              condition = mkOption {
                                type = types.either referenceType types.str;
                                description = ''
                                  A condition expression that must evaluate to true for the check to pass.
                                '';
                              };
                              error_message = mkOption {
                                type = types.str;
                                description = ''
                                  Error message displayed when the condition evaluates to false.
                                '';
                              };
                            };
                          }
                        );
                        description = ''
                          Preconditions that must be met before applying changes.
                        '';
                      };
                      postconditions = mkUnsetOption {
                        type = types.listOf (
                          types.submodule {
                            options = {
                              condition = mkOption {
                                type = types.either referenceType types.str;
                                description = ''
                                  A condition expression that must evaluate to true for the check to pass.
                                '';
                              };
                              error_message = mkOption {
                                type = types.str;
                                description = ''
                                  Error message displayed when the condition evaluates to false.
                                '';
                              };
                            };
                          }
                        );
                        description = ''
                          Postconditions that must be met after applying changes.
                        '';
                      };
                      destroy = mkUnsetOption {
                        type = types.bool;
                        description = ''
                          When `false`, OpenTofu will "forget" the resource instead of destroying it.
                        '';
                      };
                      enabled = mkUnsetOption {
                        type = types.either referenceType types.bool;
                        description = ''
                          Controls whether a resource is created and managed by OpenTofu.
                          When `false`, the resource is excluded as if it did not exist.
                        '';
                      };
                    };
                  };
                  description = ''
                    Lifecycle customizations for this resource.
                  '';
                };

                "hostname" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Device description: 1-32 letters, digits and hyphens, not starting or ending with a hyphen (dots and underscores are rejected by the switch). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Always `system`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "switch" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Name of the switch (a key of the provider's `switches` map) this object lives on. Omit it to use the provider's own switch (`host`), or the only configured switch. Changing it replaces the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "System settings (System > System Info > Device Description). Singleton; import with id `system`. Only the device description is managed. This resource never changes the management IP, DHCP or gateway (the request re-sends them as read from the switch); `tplinkeasysmart_management_ip` manages them. A write is skipped when the description already matches. Destroying only removes it from state. ";
      };
      "tplinkeasysmart_vlan_8021q_group" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated tplinkeasysmart provider";
              options = {
                _name = mkOption {
                  internal = true;
                  type = types.str;
                  default = name;
                };
                count = mkUnsetOption {
                  type = types.either referenceType types.int;
                  description = ''
                    Used to create multiple instances of this resource/data.
                  '';
                };
                for_each = mkUnsetOption {
                  type = types.oneOf [
                    referenceType
                    (types.attrsOf types.str)
                    (types.listOf types.str)
                  ];
                  description = ''
                    Used to create multiple instances of this resource/data from a map or set.
                  '';
                };
                depends_on = mkUnsetOption {
                  type = types.listOf (types.coercedTo types.attrs builtins.toString types.str);
                  description = ''
                    Explicit dependency references. Use this to declare hidden dependencies that OpenTofu cannot automatically infer.
                  '';
                };
                provider = mkUnsetOption {
                  type = types.str;
                  example = "aws.west";
                  description = ''
                    Provider reference for selecting a non-default provider configuration.
                  '';
                };

                lifecycle = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      create_before_destroy = mkUnsetOption {
                        type = types.bool;
                        description = ''
                          When `true`, the new replacement object is created first before destroying the old one.
                        '';
                      };
                      prevent_destroy = mkUnsetOption {
                        type = types.either referenceType types.bool;
                        description = ''
                          When `true`, OpenTofu will reject any plan that would destroy this resource.
                        '';
                      };
                      ignore_changes = mkUnsetOption {
                        type = types.either (types.enum [ "all" ]) (types.listOf types.str);
                        description = ''
                          List of attribute names to ignore during updates, or "all" to ignore all attributes.
                        '';
                      };
                      replace_triggered_by = mkUnsetOption {
                        type = types.listOf types.str;
                        description = ''
                          List of resource or attribute references that trigger replacement of this resource when changed.
                        '';
                      };
                      preconditions = mkUnsetOption {
                        type = types.listOf (
                          types.submodule {
                            options = {
                              condition = mkOption {
                                type = types.either referenceType types.str;
                                description = ''
                                  A condition expression that must evaluate to true for the check to pass.
                                '';
                              };
                              error_message = mkOption {
                                type = types.str;
                                description = ''
                                  Error message displayed when the condition evaluates to false.
                                '';
                              };
                            };
                          }
                        );
                        description = ''
                          Preconditions that must be met before applying changes.
                        '';
                      };
                      postconditions = mkUnsetOption {
                        type = types.listOf (
                          types.submodule {
                            options = {
                              condition = mkOption {
                                type = types.either referenceType types.str;
                                description = ''
                                  A condition expression that must evaluate to true for the check to pass.
                                '';
                              };
                              error_message = mkOption {
                                type = types.str;
                                description = ''
                                  Error message displayed when the condition evaluates to false.
                                '';
                              };
                            };
                          }
                        );
                        description = ''
                          Postconditions that must be met after applying changes.
                        '';
                      };
                      destroy = mkUnsetOption {
                        type = types.bool;
                        description = ''
                          When `false`, OpenTofu will "forget" the resource instead of destroying it.
                        '';
                      };
                      enabled = mkUnsetOption {
                        type = types.either referenceType types.bool;
                        description = ''
                          Controls whether a resource is created and managed by OpenTofu.
                          When `false`, the resource is excluded as if it did not exist.
                        '';
                      };
                    };
                  };
                  description = ''
                    Lifecycle customizations for this resource.
                  '';
                };

                "allow_disruptive" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Allow touching ports that have a link up or belong to a LAG, and leaving a port in no VLAN. Defaults to false. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "VLAN name, up to 10 letters, digits, hyphens or underscores. Defaults to empty. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "switch" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Name of the switch (a key of the provider's `switches` map) this object lives on. Omit it to use the provider's own switch (`host`), or the only configured switch. Changing it replaces the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tagged_ports" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.number))));
                  description = "Ports that carry this VLAN tagged. Defaults to none. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "untagged_ports" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.number))));
                  description = "Ports that carry this VLAN untagged. Defaults to none. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "vlan" = mkOption {
                  type = (types.either referenceType (types.number));
                  description = "VLAN id, 1-4094. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "An 802.1Q VLAN (VLAN > 802.1Q VLAN): its name and its tagged and untagged member ports. Import with the VLAN id. The 802.1Q mode must already be active (see `tplinkeasysmart_vlan_mode`); the provider refuses to manage VLANs in another mode. Unlike port-based groups, a port can be a member of several 802.1Q VLANs. **Safety:** the provider refuses to create, change or destroy a VLAN when any port involved (old or new members) has a link up or belongs to a LAG, and when the change would leave a port in no VLAN at all, because that can cut connectivity or management access; set `allow_disruptive = true` to override. Keep VLAN 1 (the default) under `lifecycle { prevent_destroy = true }`. ";
      };
      "tplinkeasysmart_vlan_8021q_pvid" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated tplinkeasysmart provider";
              options = {
                _name = mkOption {
                  internal = true;
                  type = types.str;
                  default = name;
                };
                count = mkUnsetOption {
                  type = types.either referenceType types.int;
                  description = ''
                    Used to create multiple instances of this resource/data.
                  '';
                };
                for_each = mkUnsetOption {
                  type = types.oneOf [
                    referenceType
                    (types.attrsOf types.str)
                    (types.listOf types.str)
                  ];
                  description = ''
                    Used to create multiple instances of this resource/data from a map or set.
                  '';
                };
                depends_on = mkUnsetOption {
                  type = types.listOf (types.coercedTo types.attrs builtins.toString types.str);
                  description = ''
                    Explicit dependency references. Use this to declare hidden dependencies that OpenTofu cannot automatically infer.
                  '';
                };
                provider = mkUnsetOption {
                  type = types.str;
                  example = "aws.west";
                  description = ''
                    Provider reference for selecting a non-default provider configuration.
                  '';
                };

                lifecycle = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      create_before_destroy = mkUnsetOption {
                        type = types.bool;
                        description = ''
                          When `true`, the new replacement object is created first before destroying the old one.
                        '';
                      };
                      prevent_destroy = mkUnsetOption {
                        type = types.either referenceType types.bool;
                        description = ''
                          When `true`, OpenTofu will reject any plan that would destroy this resource.
                        '';
                      };
                      ignore_changes = mkUnsetOption {
                        type = types.either (types.enum [ "all" ]) (types.listOf types.str);
                        description = ''
                          List of attribute names to ignore during updates, or "all" to ignore all attributes.
                        '';
                      };
                      replace_triggered_by = mkUnsetOption {
                        type = types.listOf types.str;
                        description = ''
                          List of resource or attribute references that trigger replacement of this resource when changed.
                        '';
                      };
                      preconditions = mkUnsetOption {
                        type = types.listOf (
                          types.submodule {
                            options = {
                              condition = mkOption {
                                type = types.either referenceType types.str;
                                description = ''
                                  A condition expression that must evaluate to true for the check to pass.
                                '';
                              };
                              error_message = mkOption {
                                type = types.str;
                                description = ''
                                  Error message displayed when the condition evaluates to false.
                                '';
                              };
                            };
                          }
                        );
                        description = ''
                          Preconditions that must be met before applying changes.
                        '';
                      };
                      postconditions = mkUnsetOption {
                        type = types.listOf (
                          types.submodule {
                            options = {
                              condition = mkOption {
                                type = types.either referenceType types.str;
                                description = ''
                                  A condition expression that must evaluate to true for the check to pass.
                                '';
                              };
                              error_message = mkOption {
                                type = types.str;
                                description = ''
                                  Error message displayed when the condition evaluates to false.
                                '';
                              };
                            };
                          }
                        );
                        description = ''
                          Postconditions that must be met after applying changes.
                        '';
                      };
                      destroy = mkUnsetOption {
                        type = types.bool;
                        description = ''
                          When `false`, OpenTofu will "forget" the resource instead of destroying it.
                        '';
                      };
                      enabled = mkUnsetOption {
                        type = types.either referenceType types.bool;
                        description = ''
                          Controls whether a resource is created and managed by OpenTofu.
                          When `false`, the resource is excluded as if it did not exist.
                        '';
                      };
                    };
                  };
                  description = ''
                    Lifecycle customizations for this resource.
                  '';
                };

                "allow_disruptive" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Allow changing a port that has a link up or is a LAG member. Defaults to false. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "port" = mkOption {
                  type = (types.either referenceType (types.number));
                  description = "Port number (1-based). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "pvid" = mkOption {
                  type = (types.either referenceType (types.number));
                  description = "The port's default VLAN id, 1-4094. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "switch" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Name of the switch (a key of the provider's `switches` map) this object lives on. Omit it to use the provider's own switch (`host`), or the only configured switch. Changing it replaces the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "The PVID of one port (VLAN > 802.1Q PVID Setting): the VLAN that untagged traffic entering the port is assigned to. Import with the port number. The 802.1Q mode must be active and the VLAN must exist (see `tplinkeasysmart_vlan_mode` and `tplinkeasysmart_vlan_8021q_group`). **Safety:** the provider refuses to change a port that has a link up or is in a LAG, because a wrong PVID drops the port's untagged traffic; set `allow_disruptive = true` to override. Destroying sets the PVID back to 1 (when 802.1Q is active and VLAN 1 exists), under the same guard. ";
      };
      "tplinkeasysmart_vlan_mode" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated tplinkeasysmart provider";
              options = {
                _name = mkOption {
                  internal = true;
                  type = types.str;
                  default = name;
                };
                count = mkUnsetOption {
                  type = types.either referenceType types.int;
                  description = ''
                    Used to create multiple instances of this resource/data.
                  '';
                };
                for_each = mkUnsetOption {
                  type = types.oneOf [
                    referenceType
                    (types.attrsOf types.str)
                    (types.listOf types.str)
                  ];
                  description = ''
                    Used to create multiple instances of this resource/data from a map or set.
                  '';
                };
                depends_on = mkUnsetOption {
                  type = types.listOf (types.coercedTo types.attrs builtins.toString types.str);
                  description = ''
                    Explicit dependency references. Use this to declare hidden dependencies that OpenTofu cannot automatically infer.
                  '';
                };
                provider = mkUnsetOption {
                  type = types.str;
                  example = "aws.west";
                  description = ''
                    Provider reference for selecting a non-default provider configuration.
                  '';
                };

                lifecycle = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      create_before_destroy = mkUnsetOption {
                        type = types.bool;
                        description = ''
                          When `true`, the new replacement object is created first before destroying the old one.
                        '';
                      };
                      prevent_destroy = mkUnsetOption {
                        type = types.either referenceType types.bool;
                        description = ''
                          When `true`, OpenTofu will reject any plan that would destroy this resource.
                        '';
                      };
                      ignore_changes = mkUnsetOption {
                        type = types.either (types.enum [ "all" ]) (types.listOf types.str);
                        description = ''
                          List of attribute names to ignore during updates, or "all" to ignore all attributes.
                        '';
                      };
                      replace_triggered_by = mkUnsetOption {
                        type = types.listOf types.str;
                        description = ''
                          List of resource or attribute references that trigger replacement of this resource when changed.
                        '';
                      };
                      preconditions = mkUnsetOption {
                        type = types.listOf (
                          types.submodule {
                            options = {
                              condition = mkOption {
                                type = types.either referenceType types.str;
                                description = ''
                                  A condition expression that must evaluate to true for the check to pass.
                                '';
                              };
                              error_message = mkOption {
                                type = types.str;
                                description = ''
                                  Error message displayed when the condition evaluates to false.
                                '';
                              };
                            };
                          }
                        );
                        description = ''
                          Preconditions that must be met before applying changes.
                        '';
                      };
                      postconditions = mkUnsetOption {
                        type = types.listOf (
                          types.submodule {
                            options = {
                              condition = mkOption {
                                type = types.either referenceType types.str;
                                description = ''
                                  A condition expression that must evaluate to true for the check to pass.
                                '';
                              };
                              error_message = mkOption {
                                type = types.str;
                                description = ''
                                  Error message displayed when the condition evaluates to false.
                                '';
                              };
                            };
                          }
                        );
                        description = ''
                          Postconditions that must be met after applying changes.
                        '';
                      };
                      destroy = mkUnsetOption {
                        type = types.bool;
                        description = ''
                          When `false`, OpenTofu will "forget" the resource instead of destroying it.
                        '';
                      };
                      enabled = mkUnsetOption {
                        type = types.either referenceType types.bool;
                        description = ''
                          Controls whether a resource is created and managed by OpenTofu.
                          When `false`, the resource is excluded as if it did not exist.
                        '';
                      };
                    };
                  };
                  description = ''
                    Lifecycle customizations for this resource.
                  '';
                };

                "allow_disruptive" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Allow changing the VLAN mode. Defaults to false. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Always `vlan_mode`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mode" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "One of `mtu`, `port-based` or `802.1q`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mtu_uplink_port" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = ''The uplink port of the MTU VLAN: every other port can only talk to it. Required with `mode = "mtu"`, not allowed with another mode. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "switch" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Name of the switch (a key of the provider's `switches` map) this object lives on. Omit it to use the provider's own switch (`host`), or the only configured switch. Changing it replaces the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "The VLAN mode of the switch (VLAN > MTU VLAN, Port Based VLAN, 802.1Q VLAN): exactly one of the three is active. Singleton; import with id `vlan_mode`. **Safety:** switching the mode discards the VLAN settings of the other modes and can cut every connection, including the management path, so the provider refuses to change the mode unless `allow_disruptive = true`. Applying the mode the switch already has writes nothing. Destroying only removes it from state. ";
      };

    };
    data = {
      "tplinkeasysmart_system_info" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated tplinkeasysmart provider";
              options = {
                _name = mkOption {
                  internal = true;
                  type = types.str;
                  default = name;
                };
                count = mkUnsetOption {
                  type = types.either referenceType types.int;
                  description = ''
                    Used to create multiple instances of this resource/data.
                  '';
                };
                for_each = mkUnsetOption {
                  type = types.oneOf [
                    referenceType
                    (types.attrsOf types.str)
                    (types.listOf types.str)
                  ];
                  description = ''
                    Used to create multiple instances of this resource/data from a map or set.
                  '';
                };
                depends_on = mkUnsetOption {
                  type = types.listOf (types.coercedTo types.attrs builtins.toString types.str);
                  description = ''
                    Explicit dependency references. Use this to declare hidden dependencies that OpenTofu cannot automatically infer.
                  '';
                };
                provider = mkUnsetOption {
                  type = types.str;
                  example = "aws.west";
                  description = ''
                    Provider reference for selecting a non-default provider configuration.
                  '';
                };

                "auto_save" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether configuration changes are saved to flash automatically. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dhcp" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether the management IP comes from DHCP. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "factory_default" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "The switch reports it has never been configured (factory credentials still apply). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "firmware_version" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Firmware version string. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "gateway" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Default gateway. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "hardware_version" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Hardware version string. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "hostname" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Device description. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The switch MAC address. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ip_address" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Management IP address. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mac_address" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "MAC address. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "model" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Model, e.g. TL-SG108E. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "netmask" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Management subnet mask. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "port_count" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Number of physical ports. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "switch" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Name of the switch (a key of the provider's `switches` map) this object lives on. Omit it to use the provider's own switch (`host`), or the only configured switch. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Identity and network settings of the switch, read through unauthenticated discovery. Also serves as a connectivity check. ";
      };

    };
  };
  # hacky unfortunately
  options = {
    inherit (config.generated) resource data provider;
  };
}
