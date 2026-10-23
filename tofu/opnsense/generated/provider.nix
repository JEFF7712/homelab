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
  _file = "generated opnsense provider";
  config.terraform.required_providers.opnsense = {
    source = "browningluke/opnsense";
    version = "0.26.0";
  };
  config.generated.provider."opnsense" = mkUnsetOption {
    type = types.attrsOf (
      types.submodule (
        { name, ... }: {
          _file = "generated opnsense provider";
          options = {
            _name = mkOption {
              internal = true;
              type = types.str;
              default = name;
            };
            _version = mkOption {
              internal = true;
              type = types.str;
              default = "0.26.0";
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
              default = "\${opnsense.${name}}";
              defaultText = literalExpression "\"\${opnsense.${name}}\"";
              description = ''
                ID of provider, read only. Only returns a terraform reference string.
              '';
            };
            "allow_insecure" = mkUnsetOption {
              type = (types.either referenceType (types.bool));
              description = "Allow insecure TLS connections. Alternatively, can be configured using the `OPNSENSE_ALLOW_INSECURE` environment variable. Defaults to `false`. ";
            };
            "api_key" = mkUnsetOption {
              type = (types.either referenceType (types.str));
              description = "The API key for a user. Alternatively, can be configured using the `OPNSENSE_API_KEY` environment variable. ";
            };
            "api_secret" = mkUnsetOption {
              type = (types.either referenceType (types.str));
              description = "The API secret for a user. Alternatively, can be configured using the `OPNSENSE_API_SECRET` environment variable. ";
            };
            "max_backoff" = mkUnsetOption {
              type = (types.either referenceType (types.number));
              description = "Maximum backoff period in seconds after failed API calls. Alternatively, can be configured using the `OPNSENSE_MAX_BACKOFF` environment variable. ";
            };
            "min_backoff" = mkUnsetOption {
              type = (types.either referenceType (types.number));
              description = "Minimum backoff period in seconds after failed API calls. Alternatively, can be configured using the `OPNSENSE_MIN_BACKOFF` environment variable. ";
            };
            "retries" = mkUnsetOption {
              type = (types.either referenceType (types.number));
              description = "Maximum number of retries to perform when an API request fails. Alternatively, can be configured using the `OPNSENSE_RETRIES` environment variable. ";
            };
            "uri" = mkUnsetOption {
              type = (types.either referenceType (types.str));
              description = "The URI to an OPNsense host. Alternatively, can be configured using the `OPNSENSE_URI` environment variable. ";
            };

          };
        }
      )
    );
    description = "";
  };

  # required for looking up the suboptions
  config.generated = {
    resource = {
      "opnsense_cron_job" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "command" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Command to execute. Must be a command key known to OPNsense (e.g. `firmware poll`, `unbound restart`). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "days" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Day(s) of month to run (cron expression, e.g. `*` or `1`). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Description for this cron job. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this cron job. Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "hours" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Hour(s) to run (cron expression, e.g. `0` or `*/6`). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the cron job. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "minutes" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Minute(s) to run (cron expression, e.g. `0` or `*/15`). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "months" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Month(s) to run (cron expression, e.g. `*` or `1,6`). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "parameters" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional parameters to pass to the command. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "weekdays" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Day(s) of week to run (cron expression, e.g. `*` or `1-5`). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "who" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "User to run the command as. Defaults to `root`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Cron jobs allow scheduling commands to run at specified times. ";
      };
      "opnsense_dnsmasq_host" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "alias_records" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Alias records of the host. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "client_id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Client ID of the host. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "cname_records" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "CNAME records of the host. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "comment" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional comment. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "domain" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Domain of the host. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "hardware_addresses" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Hardware addresses of the host. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "hostname" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Name of the host, without the domain part. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the host. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ip_addresses" = mkOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "IP addresses of the host. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "is_ignored" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether DHCP packet is ignored for this host. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "is_local_domain" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether this is a local domain. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tag" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''UUID of the dnsmasq tag to associate with this host. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure hosts override for dnsmasq. ";
      };
      "opnsense_firewall_alias" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "categories" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set of category IDs to apply. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "content" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = ''The content of the alias. Enter ISO 3166-1 country codes when `type = "geoip"` (e.g. `["CA", "FR"]`). Enter `__<int>_network`, or alias when `type = "networkgroup"` (e.g. `["__wan_network", "otheralias"]`). Enter OpenVPN group when `type = "authgroup"` (e.g. `["admins"]`). Set to `[]` when `type = "external"`. Defaults to `[]`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this firewall alias. Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "interface" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Choose on which interface this alias applies. Only applies (and must be set) when `type = "dynipv6host"`. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ip_protocol" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = ''Select the Internet Protocol version this alias applies to. Available values: `IPv4`, `IPv6`. Only applies when `type = "asn"`, `type = "geoip"`, or `type = "external"`. Defaults to `["IPv4"]`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "The name must start with a letter or single underscore, be less than 32 characters and only consist of alphanumeric characters or underscores. Aliases can be nested using this name. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "path_expression" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''A `jq` expression to extract IP addresses from the downloaded JSON. Only applies (and must be set) when `type = "urljson"`. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "stats" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether to maintain a set of counters for each table entry. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "type" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "The type of alias. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "update_freq" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = ''The frequency that the list will be refreshed, in days (e.g. for 30 hours, enter `1.25`). Only applies (and must be set) when `type = "urltable"`. Defaults to `-1`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Aliases are named lists of networks, hosts or ports that can be used as one entity by selecting the alias name in the various supported sections of the firewall. These aliases are particularly useful to condense firewall rules and minimize changes. ";
      };
      "opnsense_firewall_category" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "auto" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "If set, this category will be removed when unused. This is included for completeness, but will result in constant recreations if not attached to any rules, and thus it is advised to leave it as default. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "color" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Pick a color to use. Must be a hex color in format `rrggbb` (e.g. `ff0000`). Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Enter a name for this category. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "To ease maintenance of larger rulesets, OPNsense includes categories for the firewall. Each rule can contain one or more categories. ";
      };
      "opnsense_firewall_filter" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "categories" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "For grouping purposes, provide the IDs of multiple groups here to organize items. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this firewall filter rule. Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "filter" = mkOption {
                  type = types.submodule {
                    options = {
                      "action" = mkOption {
                        type = (types.either referenceType (types.str));
                        description = "Choose what to do with packets that match the criteria specified below. Hint: the difference between block and reject is that with reject, a packet (TCP RST or ICMP port unreachable for UDP) is returned to the sender, whereas with block the packet is dropped silently. In either case, the original packet is discarded. ";
                      };
                      "allow_options" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Whether to allow packets with IP options to pass. Otherwise they are blocked. Defaults to `false`. ";
                      };
                      "destination" = mkUnsetOption {
                        type = types.submodule {
                          options = {
                            "invert" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = "Whether to invert the sense of the match. Defaults to `false`. ";
                            };
                            "net" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = "";
                            };
                            "port" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = ''Destination port number or well known name (imap, imaps, http, https, ...), for ranges use a dash. Defaults to `""`. '';
                            };

                          };
                        };
                        description = "";
                      };
                      "direction" = mkOption {
                        type = (types.either referenceType (types.str));
                        description = "Direction of the traffic. The default policy is to filter inbound traffic, which sets the policy to the interface originally receiving the traffic. ";
                      };
                      "icmp_type" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "";
                      };
                      "ip_protocol" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "";
                      };
                      "log" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Whether to log packets that are handled by this rule. Defaults to `false`. ";
                      };
                      "protocol" = mkOption {
                        type = (types.either referenceType (types.str));
                        description = "";
                      };
                      "quick" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "If a packet matches a rule specifying quick, then that rule is considered the last matching rule and the specified action is taken. When a rule does not have quick enabled, the last matching rule wins. Defaults to `true`. ";
                      };
                      "schedule" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "";
                      };
                      "source" = mkUnsetOption {
                        type = types.submodule {
                          options = {
                            "invert" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = "Whether to invert the sense of the match. Defaults to `false`. ";
                            };
                            "net" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = "";
                            };
                            "port" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = ''Source port number or well known name (imap, imaps, http, https, ...), for ranges use a dash. Defaults to `""`. '';
                            };

                          };
                        };
                        description = "";
                      };
                      "tcp_flags" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "The TCP flags that must be set this rule to match. Defaults to `[]`. ";
                      };
                      "tcp_flags_out_of" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "The TCP flags that must be cleared for this rule to match. Defaults to `[]`. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "interface" = mkOption {
                  type = types.submodule {
                    options = {
                      "interface" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "The interfaces to apply the filter rule on. Leave empty (`[]`) for a floating rule that applies to all interfaces. ";
                      };
                      "invert" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Whether to use all but selected interfaces. Defaults to `false`. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "internal_tagging" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "match_local" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = ''Used to specify that packets must already be tagged with the given tag in order to match the rule. Defaults to `""`. '';
                      };
                      "set_local" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = ''Packets matching this rule will be tagged with the specified string. The tag acts as an internal marker that can be used to identify these packets later on. This can be used, for example, to provide trust between interfaces and to determine if packets have been processed by translation rules. Tags are "sticky", meaning that the packet will be tagged even if the rule is not the last matching rule. Further matching rules can replace the tag with a new one but will not remove a previously applied tag. A packet is only ever assigned one tag at a time. Defaults to `""`. '';
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "no_xmlrpc_sync" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether to exclude this item from the HA synchronization process. An already existing item with the same UUID on the synchronization target will not be altered or deleted as long as this is active. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "priority" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "low_delay_set" = mkUnsetOption {
                        type = (types.either referenceType (types.number));
                        description = "Used in combination with set priority, packets which have a TOS of lowdelay and TCP ACKs with no data payload will be assigned this priority when offered. Defaults to `-1`. ";
                      };
                      "match" = mkUnsetOption {
                        type = (types.either referenceType (types.number));
                        description = "Only match packets which have the given queueing priority assigned. Defaults to `-1`. ";
                      };
                      "match_tos" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "";
                      };
                      "set" = mkUnsetOption {
                        type = (types.either referenceType (types.number));
                        description = "Packets matching this rule will be assigned a specific queueing priority. If the packet is transmitted on a vlan(4) interface, the queueing priority will be written as the priority code point in the 802.1Q VLAN header. Defaults to `-1`. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "sequence" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Specify the order of this filter rule. Defaults to `1`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "source_routing" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "disable_reply_to" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Whether to explicitly disable reply-to for this rule. Defaults to `false`. ";
                      };
                      "gateway" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = ''Leave as 'default' to use the system routing table. Or choose a gateway to utilize policy based routing. Defaults to `""`. '';
                      };
                      "reply_to" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = ''Determines how packets route back in the opposite direction (replies), when set to default, packets on WAN type interfaces reply to their connected gateway on the interface (unless globally disabled). A specific gateway may be chosen as well here. This setting is only relevant in the context of a state, for stateless rules there is no defined opposite direction. Defaults to `""`. '';
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "stateful_firewall" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "adaptive_timeouts" = mkUnsetOption {
                        type = types.submodule {
                          options = {
                            "end" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "When reaching this number of state entries, all timeout values become zero, effectively purging all state entries immediately. This value is used to define the scale factor, it should not actually be reached (set a lower state limit). Defaults to `-1`. ";
                            };
                            "start" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "When the number of state entries exceeds this value, adaptive scaling begins. All timeout values are scaled linearly with factor `(adaptive.end - number of states) / (adaptive.end - adaptive.start)`. Defaults to `-1`. ";
                            };

                          };
                        };
                        description = "";
                      };
                      "max" = mkUnsetOption {
                        type = types.submodule {
                          options = {
                            "new_connections" = mkUnsetOption {
                              type = types.submodule {
                                options = {
                                  "count" = mkUnsetOption {
                                    type = (types.either referenceType (types.number));
                                    description = "Maximum new connections per host, measured over time. Defaults to `-1`. ";
                                  };
                                  "seconds" = mkUnsetOption {
                                    type = (types.either referenceType (types.number));
                                    description = "Time interval (seconds) to measure the number of connections. Defaults to `-1`. ";
                                  };

                                };
                              };
                              description = "";
                            };
                            "source_connections" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "Limit the maximum number of simultaneous TCP connections which have completed the 3-way handshake that a single host can make. Defaults to `-1`. ";
                            };
                            "source_nodes" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "Limits the maximum number of source addresses which can simultaneously have state table entries. Defaults to `-1`. ";
                            };
                            "source_states" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "Limits the maximum number of simultaneous state entries that a single source address can create with this rule. Defaults to `-1`. ";
                            };
                            "states" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "Limits the number of concurrent states the rule may create. When this limit is reached, further packets that would create state are dropped until existing states time out. Defaults to `-1`. ";
                            };

                          };
                        };
                        description = "";
                      };
                      "no_pfsync" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Whether to prevent states created by this rule to be synced with pfsync. Defaults to `false`. ";
                      };
                      "overload_table" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = ''Overload table used when max new connections per time interval has been reached. The default virusprot table comes with a default block rule in floating rules, alternatively specify your own table here. Defaults to `""`. '';
                      };
                      "policy" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = ''How states created by this rule are treated, default (as defined in advanced), floating in which case states are valid on all interfaces or interface bound. Interface bound states are more secure, floating more flexible. Defaults to `""`. '';
                      };
                      "timeout" = mkUnsetOption {
                        type = (types.either referenceType (types.number));
                        description = "State Timeout in seconds (TCP only). Defaults to `-1`. ";
                      };
                      "type" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = ''State tracking mechanism to use, default is full stateful tracking, sloppy ignores sequence numbers, use none for stateless rules. Defaults to `"keep"`. '';
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "traffic_shaping" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "reverse_shaper" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = ''Shape packets using the selected pipe or queue in the reverse rule direction. Defaults to `""`. '';
                      };
                      "shaper" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = ''Shape packets using the selected pipe or queue in the rule direction. Defaults to `""`. '';
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Firewall filter rules can be used to restrict or allow traffic from and/or to specific networks as well as influence how traffic should be forwarded ";
      };
      "opnsense_firewall_nat" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). Must be between 1 and 255 characters. Must be a character in set `[a-zA-Z0-9 .]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "destination" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "invert" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Use this option to invert the sense of the match. Defaults to `false`. ";
                      };
                      "net" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "Specify the IP address, CIDR or alias for the destination of the packet for this mapping. For `<INT> net`, enter `<int>` (e.g. `lan`). For `<INT> address`, enter `<int>ip` (e.g. `lanip`). Defaults to `any`. ";
                      };
                      "port" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = ''Destination port number or well known name (imap, imaps, http, https, ...), for ranges use a dash. Defaults to `""`. '';
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "disable_nat" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enabling this option will disable NAT for traffic matching this rule and stop processing Outbound NAT rules. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this firewall NAT rule. Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "interface" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Choose on which interface(s) packets must come in to match this rule. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ip_protocol" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Select the Internet Protocol version this rule applies to. Available values: `inet`, `inet6`. Defaults to `inet`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "log" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Log packets that are handled by this rule. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "protocol" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Choose which IP protocol this rule should match. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "sequence" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Specify the order of this NAT rule. Defaults to `1`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "source" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "invert" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Use this option to invert the sense of the match. Defaults to `false`. ";
                      };
                      "net" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "Specify the IP address, CIDR or alias for the source of the packet for this mapping. For `<INT> net`, enter `<int>` (e.g. `lan`). For `<INT> address`, enter `<int>ip` (e.g. `lanip`). Defaults to `any`. ";
                      };
                      "port" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = ''Specify the source port for this rule. This is usually random and almost never equal to the destination port range (and should usually be `""`). Defaults to `""`. '';
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "target" = mkOption {
                  type = types.submodule {
                    options = {
                      "ip" = mkOption {
                        type = (types.either referenceType (types.str));
                        description = "Specify the IP address or alias for the packets to be mapped to. For `<INT> address`, enter `<int>ip` (e.g. `lanip`). ";
                      };
                      "port" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = ''Destination port number or well known name (imap, imaps, http, https, ...), for ranges use a dash. Defaults to `""`. '';
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Network Address Translation (abbreviated to NAT) is a way to separate external and internal networks (WANs and LANs), and to share an external IP between clients on the internal network. ";
      };
      "opnsense_firewall_nat_one_to_one" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "categories" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set of category IDs to apply. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). Must be between 0 and 255 characters. Must be a character in set `[a-zA-Z0-9 .]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "destination" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "invert" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Use this option to invert the sense of the match. Defaults to `false`. ";
                      };
                      "net" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "The 1:1 mapping will only be used for connections to or from the specified destination. Hint: this is usually 'any'. Defaults to `any`. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this firewall NAT rule. Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "external_net" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Enter the external subnet's starting address for the 1:1 mapping or network. This is the address or network the traffic will translate to/from. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "interface" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Choose which interface this rule applies to. Defaults to `wan`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "log" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Log packets that are handled by this rule. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "nat_reflection" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "NAT reflection mode. One of `default`, `enable`, or `disable`. `default` means OPNsense uses the global firewall NAT reflection setting. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "sequence" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Specify the order of this NAT rule. Defaults to `1`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "source" = mkOption {
                  type = types.submodule {
                    options = {
                      "invert" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Use this option to invert the sense of the match. Defaults to `false`. ";
                      };
                      "net" = mkOption {
                        type = (types.either referenceType (types.str));
                        description = "Enter the internal IP address or CIDR for the 1:1 mapping. Aliases are only allowed in nat, not in binat type! ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "type" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Select `binat` (default) or `nat` here, when nets are equally sized `binat` is usually the best option. Using `nat` we can also map unequal sized networks. A `binat` rule specifies a bidirectional mapping between an external and internal network and can be used from both ends, `nat` only applies in one direction. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "1:1 NAT maps a public IP or subnet to an internal private IP or subnet. All traffic to the public address is forwarded to the internal host or network. Unlike port forwarding, it exposes the full internal system, useful for servers behind a firewall. BINAT rules enable bidirectional translation for consistent incoming and outgoing connections. ";
      };
      "opnsense_firewall_nat_port_forward" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). Must be between 1 and 255 characters. Must be a character in set `[a-zA-Z0-9 .]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "destination" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "invert" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Use this option to invert the sense of the match. Defaults to `false`. ";
                      };
                      "net" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "Specify the IP address, CIDR or alias for the destination of the packet. For `<INT> net`, enter `<int>` (e.g. `lan`). For `<INT> address`, enter `<int>ip` (e.g. `lanip`). Defaults to `any`. ";
                      };
                      "port" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = ''Destination port number or well known name (imap, imaps, http, https, ...), for ranges use a dash. Defaults to `""`. '';
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this port forwarding rule. Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "interface" = mkOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Choose on which interface packets must come in to match this rule. Must specify at least 1. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ip_protocol" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Select the Internet Protocol version this rule applies to. Available values: `inet`, `inet6`. Defaults to `inet`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "log" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Log packets that are handled by this rule. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "nat_reflection" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "NAT reflection mode. One of `default`, `enable`, or `disable`. `default` means OPNsense uses the global firewall NAT reflection setting. Defaults to `default`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "protocol" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Choose which IP protocol this rule should match. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "sequence" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Specify the order of this port forwarding rule. Defaults to `1`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "source" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "invert" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Use this option to invert the sense of the match. Defaults to `false`. ";
                      };
                      "net" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "Specify the IP address, CIDR or alias for the source of the packet. For `<INT> net`, enter `<int>` (e.g. `lan`). For `<INT> address`, enter `<int>ip` (e.g. `lanip`). Defaults to `any`. ";
                      };
                      "port" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = ''Specify the source port for this rule. This is usually random and almost never equal to the destination port range (and should usually be `""`). Defaults to `""`. '';
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "target" = mkOption {
                  type = types.submodule {
                    options = {
                      "ip" = mkOption {
                        type = (types.either referenceType (types.str));
                        description = "Specify the internal IP address or alias to forward packets to. ";
                      };
                      "port" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = ''Destination port number or well known name (imap, imaps, http, https, ...), for ranges use a dash. Defaults to `""`. '';
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Destination NAT (port forwarding) redirects traffic arriving on an external interface to an internal host. Use this to expose internal services (e.g. web servers, SSH) to the outside network. ";
      };
      "opnsense_interfaces_vip" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "gateway" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "For some interface types a gateway is required to configure an IP Alias (ppp/pppoe/tun), leave this field empty for all other interface types. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the VIP. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "interface" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Choose which interface this VIP applies to. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mode" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Mode of the VIP. One of `ipalias` or `proxyarp`. `proxyarp` cannot be bound to by anything running on the firewall, such as IPsec, OpenVPN, etc. In most cases an `ipalias` should be used. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "network" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Provide an address and subnet to use. (e.g 192.168.0.1/24) ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Virtual IPs allow an OPNsense firewall to assign multiple IP addresses to the same network interface. ";
      };
      "opnsense_interfaces_vlan" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "device" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Custom VLAN name. Custom names are possible, but only if the start of the name matches the required prefix and contains numeric characters or dots, e.g. `vlan0.1.2` or `qinq0.3.4`. Set to `""` to generate a device name. Defaults to `""` '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the VLAN. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "parent" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "VLAN capable interface to attach the VLAN to, e.g. `vtnet0`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "priority" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "802.1Q VLAN PCP (priority code point). Defaults to `0`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tag" = mkOption {
                  type = (types.either referenceType (types.number));
                  description = "802.1Q VLAN tag. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "VLANs (Virtual LANs) can be used to segment a single physical network into multiple virtual networks. ";
      };
      "opnsense_ipsec_auth_local" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "auth_id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Authentication ID for the AuthLocal Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "authentication" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Authentication method for the AuthLocal Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "certificates" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "List of certificates for the AuthLocal Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description for the AuthLocal Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "eap_id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "EAP ID for the AuthLocal Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Enable or disable the AuthLocal Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ipsec_connection" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "The parent connection UUID. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "public_keys" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "List of public keys for the AuthLocal Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "round" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Authentication round for the AuthLocal Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "IPsec AuthLocal Resources are used for phase 1 authentication of IPsec VPN connections. ";
      };
      "opnsense_ipsec_auth_remote" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "auth_id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Authentication ID for the AuthRemote Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "authentication" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Authentication method for the AuthRemote Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "certificates" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "List of certificates for the AuthRemote Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description for the AuthRemote Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "eap_id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "EAP ID for the AuthRemote Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Enable or disable the AuthRemote Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ipsec_connection" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "The parent connection UUID. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "public_keys" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "List of public keys for the AuthRemote Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "round" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Authentication round for the AuthRemote Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "IPsec AuthRemote Resources are used for phase 1 authentication of IPsec VPN connections. ";
      };
      "opnsense_ipsec_child" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "close_action" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Close action for the Child Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description for the PSK. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dpd_action" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "DPD action for the Child Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Enable or disable the Child Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "install_policies" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Install policies for the Child Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ipsec_connection" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "The parent connection UUID. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "local_networks" = mkOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "List of local networks for the Child Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mode" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Mode for the Child Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "proposals" = mkOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "List of proposals for the Child Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "rekey_time" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Rekey time for the Child Resource in seconds. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "remote_networks" = mkOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "List of remote networks for the Child Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "request_id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Request ID for the Child Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "sha256_96" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Enable or disable SHA256_96. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "start_action" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Start action for the Child Resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "IPsec Child Resources are used for phase 2 of IPsec VPN connections. ";
      };
      "opnsense_ipsec_connection" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "aggressive" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Enable or disable aggressive mode. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Description for the IPsec connection. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dpd_delay" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Dead Peer Detection (DPD) delay. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dpd_timeout" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Dead Peer Detection (DPD) timeout. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Enable or disable the IPsec connection. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ike_lifetime" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "IKE lifetime duration. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ip_pools" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "List of IP pools for the connection. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "keying_tries" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Number of keying tries. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "local_addresses" = mkOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "List of local addresses for the connection. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "local_port" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Local port for the connection. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mobike" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Enable or disable MOBIKE support. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "proposals" = mkOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "List of encryption proposals for the connection. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "reauthentication_time" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Time interval for reauthentication. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "rekey_time" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Time interval for rekeying. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "remote_addresses" = mkOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "List of remote addresses for the connection. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "remote_port" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Remote port for the connection. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "send_certificate" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Whether to send a certificate. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "send_certificate_request" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Whether to send a certificate request. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "udp_encapsulation" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Enable or disable UDP encapsulation. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "unique" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Whether the connection should use unique IDs. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "version" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "IKE version to use (e.g., '1', '2'). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "IPsec Connections are used for establishing secure communication channels. ";
      };
      "opnsense_ipsec_psk" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description for the PSK. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "identity_local" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Local identity for the PSK. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "identity_remote" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Remote identity for the PSK. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "pre_shared_key" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "The pre-shared key used for authentication. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "type" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Type of the pre-shared key. Valid values are 'PSK' (traditional pre-shared key) or 'EAP' (for EAP-MSCHAPv2 authentication). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "IPsec Pre-Shared Keys (PSKs) are used for authenticating IPsec VPN connections. ";
      };
      "opnsense_ipsec_vti" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description for the VTI. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Enable or disable the VTI. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "local_ip" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Local IP address for the VTI. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "remote_ip" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Remote IP address for the VTI. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "request_id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Request ID for the VTI. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tunnel_local_ip" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Local tunnel IP address for the VTI. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tunnel_local_ip2" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Second local tunnel IP address for the VTI. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tunnel_remote_ip" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Remote tunnel IP address for the VTI. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tunnel_remote_ip2" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Second remote tunnel IP address for the VTI. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "IPsec Virtual Tunnel Interfaces (VTIs) are used by routed IPsec VPN connections. ";
      };
      "opnsense_kea_dhcpv4_peer" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the peer. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = ''Peer name, there should be one entry matching this machine's "This server name". '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "role" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Peer's role. Defaults to `"primary"`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "url" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "URL of the server instance, which should use a different port than the control agent (e.g. `http://192.0.2.1:8001/`). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure HA Peers for Kea DHCPv4. ";
      };
      "opnsense_kea_dhcpv4_reservation" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "hostname" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Hostname to offer to the client. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the reservation. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ip_address" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "IP address to offer to the client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mac_address" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "MAC/Ether address of the client in question. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "subnet_id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Subnet ID the reservation belongs to. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure DHCPv4 reservations for Kea. ";
      };
      "opnsense_kea_dhcpv4_subnet" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "auto_collect" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Automatically update option data from the GUI for relevant attributes. When set, values for `routers`, `dns_servers` and `ntp_servers` will be ignored. Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dns_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "DNS servers to offer to the clients. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "domain_name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Domain name to offer to the client, set to this firewall's domain name when left empty. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "domain_search" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set of Domain Names to be used by the client to locate not-fully-qualified domain names. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the subnet. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "match_client_id" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "By default, KEA uses client-identifiers instead of MAC addresses to locate clients, disabling this option changes back to matching on MAC address which is used by most dhcp implementations. Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "next_server" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Next server IP address. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ntp_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set of IP addresses indicating NTP (RFC 5905) servers available to the client. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "pools" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = ''Set of pools in range or subnet format (e.g. `"192.168.0.100 - 192.168.0.200"` , `"192.0.2.64/26"`). Defaults to `[]`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "routers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Default gateways to offer to the clients. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "static_routes" = mkUnsetOption {
                  type = types.listOf (
                    types.submodule {
                      options = {
                        "destination_ip" = mkOption {
                          type = (types.either referenceType (types.str));
                          description = "Destination IP address for static route. ";
                        };
                        "router_ip" = mkOption {
                          type = (types.either referenceType (types.str));
                          description = "Gateway IP for static route. ";
                        };

                      };
                    }
                  );
                  description = "Static routes that the client should install in its routing cache. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "subnet" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = ''Subnet to use (e.g. `"192.0.2.64/26"`), should be large enough to hold the specified pools and reservations. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tftp_bootfile" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Boot filename to request. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tftp_server" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''TFTP server address or fqdn. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "time_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set of RFC 868 time servers available to the client. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure DHCPv4 subnets for Kea. ";
      };
      "opnsense_kea_dhcpv6_pd_pool" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "delegated_len" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Length of the delegated prefix. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the PD pool. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "prefix" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = ''IPv6 prefix for the PD pool (e.g. `"2001:db8::/48"`). '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "prefix_len" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Prefix length of the PD pool prefix. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "subnet_id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Subnet ID the PD pool belongs to. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure DHCPv6 prefix delegation pools for Kea. ";
      };
      "opnsense_kea_dhcpv6_peer" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the peer. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = ''Peer name, there should be one entry matching this machine's "This server name". '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "role" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Peer's role. Defaults to `"primary"`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "url" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "URL of the server instance, which should use a different port than the control agent (e.g. `http://192.0.2.1:8001/`). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure HA Peers for Kea DHCPv6. ";
      };
      "opnsense_kea_dhcpv6_reservation" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "domain_search" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Domain search list. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "duid" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "DHCP Unique Identifier (DUID) of the client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "hostname" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Hostname to offer to the client. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the reservation. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ip_address" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "IPv6 address to offer to the client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "subnet_id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Subnet ID the reservation belongs to. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure DHCPv6 reservations for Kea. ";
      };
      "opnsense_kea_dhcpv6_subnet" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "allocator" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Address allocator to use. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dns_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "DNS servers to offer. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "domain_search" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Domain search list. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the subnet. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "interface" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Interface to listen on. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "pd_allocator" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Prefix delegation allocator. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "pools" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = ''Set of address pools (e.g. `"2001:db8::100 - 2001:db8::200"`). Defaults to `[]`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "subnet" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = ''IPv6 Subnet (e.g. `"2001:db8::/64"`). '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure DHCPv6 subnets for Kea. ";
      };
      "opnsense_kea_peer" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the peer. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = ''Peer name, there should be one entry matching this machine's "This server name". '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "role" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Peer's role. Defaults to `"primary"`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "url" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "URL of the server instance, which should use a different port than the control agent (e.g. `http://192.0.2.1:8001/`). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure HA Peers for Kea DHCPv4. ";
      };
      "opnsense_kea_reservation" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "hostname" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Hostname to offer to the client. Defaults to `""`.. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the reservation. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ip_address" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "IP address to offer to the client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mac_address" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "MAC/Ether address of the client in question. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "subnet_id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Subnet ID the reservation belongs to. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure DHCPv4 reservations for Kea. ";
      };
      "opnsense_kea_subnet" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "auto_collect" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Automatically update option data from the GUI for relevant attributes. When set, values for `routers`, `dns_servers` and `ntp_servers` will be ignored. Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dns_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "DNS servers to offer to the clients. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "domain_name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Domain name to offer to the client, set to this firewall's domain name when left empty. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "domain_search" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set of Domain Names to be used by the client to locate not-fully-qualified domain names. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the subnet. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "match_client_id" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "By default, KEA uses client-identifiers instead of MAC addresses to locate clients, disabling this option changes back to matching on MAC address which is used by most dhcp implementations. Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "next_server" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Next server IP address. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ntp_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set of IP addresses indicating NTP (RFC 5905) servers available to the client. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "pools" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = ''Set of pools in range or subnet format (e.g. `"192.168.0.100 - 192.168.0.200"` , `"192.0.2.64/26"`). Defaults to `[]`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "routers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Default gateways to offer to the clients. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "static_routes" = mkUnsetOption {
                  type = types.listOf (
                    types.submodule {
                      options = {
                        "destination_ip" = mkOption {
                          type = (types.either referenceType (types.str));
                          description = "Destination IP address for static route. ";
                        };
                        "router_ip" = mkOption {
                          type = (types.either referenceType (types.str));
                          description = "Gateway IP for static route. ";
                        };

                      };
                    }
                  );
                  description = "Static routes that the client should install in its routing cache. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "subnet" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = ''Subnet to use (e.g. `"192.0.2.64/26"`), should be large enough to hold the specified pools and reservations. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tftp_bootfile" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Boot filename to request. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tftp_server" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''TFTP server address or fqdn. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "time_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set of RFC 868 time servers available to the client. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure DHCPv4 subnets for Kea. ";
      };
      "opnsense_openvpn_client_overwrite" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "block" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "When `true`, blocks this client from connecting. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "common_name" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Client common name to match. Wildcards are not allowed; use one resource per common name. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Description of this override. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dns_domain" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Push DNS domains to the client. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dns_domain_search" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Push DNS search domains to the client. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dns_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Push DNS servers to the client. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this override. Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the override. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "local_networks" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Local IPv4/IPv6 networks accessible from this client (CIDR). Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ntp_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Push NTP servers to the client. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "push_reset" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "When `true`, sends `push-reset` so the client ignores any server-pushed options not explicitly redefined here. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "redirect_gateway" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "OpenVPN `redirect-gateway` flags to push. Any of `local`, `autolocal`, `def1`, `bypass-dhcp`, `bypass-dns`, `block-local`, `ipv6`, `!ipv4`. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "register_dns" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Push `register-dns` so Windows clients refresh DNS settings. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "remote_networks" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Remote networks reachable behind this client (CIDR), pushed as iroute. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "route_gateway" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Override the default route gateway for this client. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "UUIDs of the OpenVPN server instances this override applies to. When empty, the override applies to all servers. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tunnel_network" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''IPv4 tunnel network to assign this client (CIDR notation). Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tunnel_network_v6" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''IPv6 tunnel network to assign this client (CIDR notation). Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "wins_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Push WINS servers to the client. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Client-specific overrides applied by an OpenVPN server when a client with a matching common name connects (e.g. fixed tunnel address, pushed routes, DNS). ";
      };
      "opnsense_openvpn_instance" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "auth_digest" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Authentication digest. Set to `""` to use the OpenVPN default. Valid values include `SHA1`, `SHA256`, `SHA512`, etc. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "auth_gen_token" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Generate auth tokens valid for this many seconds. Defaults to `-1` (unset). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "auth_gen_token_renewal" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Renew the auth token every N seconds. Defaults to `-1` (unset). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "auth_gen_token_secret" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Secret used to sign auth tokens. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "auth_mode" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = ''Authentication backends (e.g. `["Local Database"]`). Defaults to `[]`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "bridge_gateway" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Bridge gateway IP when bridging the OpenVPN tap interface. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "bridge_pool" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Bridge DHCP pool range when bridging the OpenVPN tap interface (`start-end`). Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "carp_depend_on" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Only run this instance when the named CARP VIP is master. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "cert_depth" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Maximum certificate chain depth. One of `""` (do not check), `1`, `2`, `3`, `4`, `5`. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "certificate" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''ID (refid) of the server/client certificate to use. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "certificate_authority" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''ID (refid) of the certificate authority. Leave blank to derive from the server/client certificate. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "compress_migrate" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable compression migration (legacy clients). Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "crl" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''ID (refid) of the certificate revocation list. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "data_ciphers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = ''Accepted data-channel ciphers, in priority order (e.g. `["AES-256-GCM", "CHACHA20-POLY1305"]`). Defaults to `[]`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "data_ciphers_fallback" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Fallback cipher for legacy clients that don't support cipher negotiation. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Description of this instance. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dev_type" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Tunnel device type. One of `tun`, `tap`, or `ovpn` (DCO). Defaults to `tun`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dns_domain" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "DNS domains pushed to clients. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dns_domain_search" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "DNS search domains pushed to clients. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dns_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "DNS servers pushed to clients. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this instance. Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "fragment" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Enable internal datagram fragmentation. Defaults to `-1` (disabled). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "http_proxy" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Connect through an HTTP proxy (`host:port`). Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the instance. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ifconfig_pool_persist" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Persist client-IP assignments across restarts. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "keepalive_interval" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Keepalive ping interval in seconds. Defaults to `-1` (unset). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "keepalive_timeout" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Keepalive timeout in seconds (peer is declared dead). Defaults to `-1` (unset). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "local" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Local IP to bind to. Defaults to `""` (all interfaces). '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "local_group" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Required local group membership for authenticating clients. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "max_clients" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Maximum number of concurrent clients. Defaults to `-1` (unset). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mss_fix" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable `mssfix` to clamp TCP MSS to the tunnel MTU. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "no_pool" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "When `true`, disables the dynamic IP pool. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ntp_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "NTP servers pushed to clients. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "password" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Client mode: password for the remote server. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "port" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Listening port (server) or remote port (client). Defaults to `-1` (no fixed port). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "port_share" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Share the OpenVPN port with another service using `host:port`. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "protocol" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Network protocol. One of `udp`, `udp4`, `udp6`, `tcp`, `tcp4`, `tcp6`. Defaults to `udp`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "provision_exclusive" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Only allow the most recently authenticated session for a given common name. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "push_excluded_routes" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Routes excluded from being pushed to clients (CIDR). Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "push_inactive" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Disconnect clients after this many seconds of inactivity. Defaults to `-1` (disabled). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "push_route" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Routes to push to clients (CIDR). Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "redirect_gateway" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "OpenVPN `redirect-gateway` flags. Any of `local`, `autolocal`, `def1`, `bypass-dhcp`, `bypass-dns`, `block-local`, `ipv6`, `!ipv4`. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "register_dns" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Run `register-dns` on Windows clients. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "remote" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Remote host(s) the client should connect to. Use `host` or `host:port`. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "remote_cert_tls" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Require the remote peer to have a certificate with the correct extended key usage. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "reneg_sec" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Renegotiate the data channel key after this many seconds. `0` disables renegotiation. Defaults to `-1` (unset). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "role" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Whether this instance acts as a `server` or a `client`. Defaults to `server`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "route" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Local routes to install (CIDR). Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "route_metric" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Metric for installed routes. Defaults to `-1` (unset). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "server" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''IPv4 tunnel network in CIDR notation (server mode). Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "server_ipv6" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''IPv6 tunnel network in CIDR notation (server mode). Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "strict_user_cn" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Whether to enforce that the username matches the certificate common name. One of `0` (no), `1` (yes), or `2` (yes, case-insensitive). Defaults to `0`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tls_key" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''UUID of an `opnsense_openvpn_static_key` to use as TLS key. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "topology" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Tunnel topology. One of `subnet`, `net30`, or `p2p`. Defaults to `subnet`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tun_mtu" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "MTU for the tunnel interface. Defaults to `-1` (unset). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "use_ocsp" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Use OCSP to verify the peer certificate. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "username" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Client mode: username for the remote server. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "username_as_common_name" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Use the authenticated username as the common name. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "various_flags" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Miscellaneous instance flags. Any of `block-ipv6`, `client-to-client`, `duplicate-cn`, `float`, `passtos`, `persist-remote-ip`, `remote-random`, `route-noexec`, `route-nopull`, `explicit-exit-notify`, `fast-io`. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "various_push_flags" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Miscellaneous push flags. Any of `block-ipv6`, `block-outside-dns`, `register-dns`, `explicit-exit-notify`. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "verify_client_cert" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Client certificate verification mode. One of `require` or `none`. Defaults to `require`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "verify_x509_name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Verify the peer certificate against this X509 name. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "vpn_id" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Numeric VPN ID. Must be unique across all OpenVPN instances. The OPNsense web UI auto-assigns one; when managing instances via Terraform you should set this explicitly to keep ordering deterministic. Defaults to `-1` (auto). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "OpenVPN instances (servers or clients) configured under `VPN > OpenVPN > Instances`. Each instance corresponds to one OpenVPN daemon process on the OPNsense host. ";
      };
      "opnsense_openvpn_static_key" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Description for this static key. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the static key. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "key" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "The static key payload. Use the OpenVPN-formatted key (e.g. output of `openvpn --genkey secret`). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mode" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The static-key mode. One of `auth`, `crypt`, or `crypt-v2`. Defaults to `crypt`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "OpenVPN static keys are pre-shared TLS or static keys used by OpenVPN instances for `tls-auth`, `tls-crypt`, `tls-crypt-v2` or peer-to-peer secret authentication. ";
      };
      "opnsense_quagga_bgp_aspath" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "action" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Set permit for match or deny to negate the rule. Defaults to `"permit"`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "as" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "The AS pattern you want to match, regexp allowed (e.g. `.$` or `_1$`). It's not validated so please be careful! ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''An optional description for this AS path. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this AS path. Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the AS path. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "number" = mkOption {
                  type = (types.either referenceType (types.number));
                  description = "The ACL rule number (0-4294967294); keep in mind that there are no sequence numbers with AS-Path lists. When you want to add a new line between you have to completely remove the ACL! ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure AS Path lists for BGP. ";
      };
      "opnsense_quagga_bgp_communitylist" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "action" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Set permit for match or deny to negate the rule. Defaults to `"permit"`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "community" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "The community you want to match. You can also regex and it is not validated so please be careful. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''An optional description for this prefix list. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this community list. Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the community list. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "number" = mkOption {
                  type = (types.either referenceType (types.number));
                  description = "Set the number of your Community-List. 1-99 are standard lists while 100-500 are expanded lists. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "seq_number" = mkOption {
                  type = (types.either referenceType (types.number));
                  description = "The ACL sequence number (10-99). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure community lists for BGP. ";
      };
      "opnsense_quagga_bgp_neighbor" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "as_override" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Override AS number of the originating router with the local AS number. This command is only allowed for eBGP peers. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "attribute_unchanged" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Specify attribute to be left unchanged when sending advertisements to a peer. Read more at FRR documentation. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "bfd" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable BFD support for this neighbor. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "connect_timer" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "The time in seconds how fast a neighbor tries to reconnect. Defaults to `-1`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "default_route" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable to send Defaultroute. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''An optional description for this neighbor. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "disable_connected_check" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable to allow peerings between directly connected eBGP peers using loopback addresses. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this neighbor. Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "hold_down" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "The time in seconds when a neighbor is considered dead. This is usually 3 times the keepalive timer. Defaults to `180`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the neighbor. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "keep_alive" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Enable Keepalive timer to check if the neighbor is still up. Defaults to `60`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "link_local_interface" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Interface to use for IPv6 link-local neighbours. Must be a valid OPNsense interface in lowercase (e.g. `wan`). Please refer to the FRR documentation for more information. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "local_ip" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''The local IP connecting to the neighbor. This is only required for BGP authentication. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "md5_password" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''The password for BGP authentication. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "multi_hop" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable multi-hop. Specifying ebgp-multihop allows sessions with eBGP neighbors to establish when they are multiple hops away. When the neighbor is not directly connected and this knob is not enabled, the session will not establish. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "multi_protocol" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Mark this neighbor as multiprotocol capable per RFC 2283. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "next_hop_self" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable the next-hop-self command. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "next_hop_self_all" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = ''Add the parameter "all" after next-hop-self command. Defaults to `false`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "peer_ip" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "The IP of your neighbor. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "prefix_list_in" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''The prefix list ID for inbound direction. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "prefix_list_out" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''The prefix list ID for outbound direction. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "remote_as" = mkOption {
                  type = (types.either referenceType (types.number));
                  description = "The neighbor AS. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "route_map_in" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''The route map ID for inbound direction. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "route_map_out" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''The route map ID for outbound direction. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "rr_client" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable route reflector client. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "update_source" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Physical name of the IPv4 interface facing the peer. Must be a valid OPNsense interface in lowercase (e.g. `wan`). Please refer to the FRR documentation for more information. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "weight" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Specify a default weight value for the neighbor’s routes. Defaults to `-1`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure neighbors for BGP. ";
      };
      "opnsense_quagga_bgp_prefixlist" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "action" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Set permit for match or deny to negate the rule. Defaults to `"permit"`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''An optional description for this prefix list. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this prefix list. Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the prefix list. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ip_version" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Set the IP version to use. Defaults to `"IPv4"`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "The name of this prefix list. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "network" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = ''The network pattern you want to match. You can also add "ge" or "le" additions after the network statement. It's not validated so please be careful! '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "number" = mkOption {
                  type = (types.either referenceType (types.number));
                  description = "The ACL sequence number (1-4294967294). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure prefix lists for BGP. ";
      };
      "opnsense_quagga_bgp_routemap" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "action" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Set permit for match or deny to negate the rule. Defaults to `"permit"`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "aspaths" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set the AS Path list IDs to use. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "community_lists" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set the community list IDs to use. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''An optional description for this route map. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this route map. Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the route map. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "The name of this route map. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "prefix_lists" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set the prefix list IDs to use. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "route_map_id" = mkOption {
                  type = (types.either referenceType (types.number));
                  description = "The Route-map ID between 1 and 65535. Be aware that the sorting will be done under the hood, so when you add an entry between it gets to the right position. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "set" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Free text field for your set, please be careful! You can set e.g. `local-preference 300` or `community 1:1` (http://www.nongnu.org/quagga/docs/docs-multi/Route-Map-Set-Command.html#Route-Map-Set-Command). Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure route maps for BGP. ";
      };
      "opnsense_route" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this route.  Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "gateway" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Which gateway this route applies, e.g. `WAN`. Must be an existing gateway. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the route. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "network" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Destination network for this static route. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Routes can be used to teach your firewall which path it should take when forwarding packets to a specific network. ";
      };
      "opnsense_trust_ca" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "action" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Creation action: `internal` (generate a new CA), `existing` (import an existing CA), or `ocsp` (create an OCSP signing certificate). Defaults to `internal`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "caref" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Reference ID (`ref_id`) of the parent CA. Used for intermediate CAs or OCSP signers. Leave empty for root CAs. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "city" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "City or locality name. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "common_name" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Common name (CN) for the CA certificate. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "country" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "ISO 3166-1 alpha-2 country code (e.g. `US`, `CA`, `DE`). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "crt" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Base64-encoded PEM certificate body. Required when `action` is `existing`. Computed (generated by OPNsense) when `action` is `internal`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "crt_payload" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Decoded PEM certificate body (read-only). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description for this CA. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "digest" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Digest algorithm: `sha224`, `sha256` (default), `sha384`, `sha512`. Defaults to `sha256`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "email" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Email address for the CA subject. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the Certificate Authority. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "key_type" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Key type and size: `512`, `1024`, `2048` (default), `3072`, `4096`, `8192`, `prime256v1`, `secp384r1`, `secp521r1`. Defaults to `2048`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "lifetime" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "CA validity period in days. Defaults to `3650` (10 years). Note: OPNsense does not return the configured lifetime on subsequent reads; the value stored in Terraform state is preserved. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Distinguished name string of the CA (read-only). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ocsp_uri" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "OCSP responder URI embedded in issued certificates. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "organization" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Organisation name. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "organizational_unit" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Organisational unit name. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "prv" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Base64-encoded PEM private key. Computed when `action` is `internal`; optionally provided when `action` is `existing`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "prv_payload" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Decoded PEM private key (read-only, sensitive). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ref_id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Short hex reference ID used by other OPNsense subsystems (e.g. OpenVPN, IPsec) to reference this CA. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "serial" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Next serial number for certificates issued by this CA. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "state" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "State or province name. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "valid_from" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Unix timestamp of certificate start date (read-only). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "valid_to" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Unix timestamp of certificate expiry date (read-only). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Manages a Certificate Authority in the OPNsense Trust store. ";
      };
      "opnsense_trust_cert" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "action" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Certificate action: `internal` (generate signed by CA), `external` (CSR only), `import` (import existing), `sign_csr`, `import_csr`, `reissue`, `manual`. Defaults to `internal`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "altnames_dns" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''DNS Subject Alternative Names, newline or CRLF separated (e.g. `www.example.com\napi.example.com`). '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "altnames_email" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Email Subject Alternative Names, newline separated. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "altnames_ip" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "IP Subject Alternative Names, newline separated. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "altnames_uri" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "URI Subject Alternative Names, newline separated. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "caref" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Reference ID (`ref_id`) of the signing CA. Required for `internal` and `sign_csr` actions. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "cert_type" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Certificate type: `server_cert`, `usr_cert` (default), `combined_server_client`. Defaults to `usr_cert`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "city" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "common_name" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Common name (CN) for the certificate. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "country" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "ISO 3166-1 alpha-2 country code (e.g. `US`, `CA`, `DE`). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "crt" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Base64-encoded PEM certificate body. Required when `action` is `import`. Computed when `action` is `internal`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "crt_payload" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Decoded PEM certificate body (read-only). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "csr" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Base64-encoded PEM Certificate Signing Request. Required when `action` is `sign_csr` or `import_csr`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "csr_payload" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Decoded PEM CSR (read-only). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description for this certificate. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "digest" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Digest algorithm: `sha224`, `sha256` (default), `sha384`, `sha512`. Defaults to `sha256`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "email" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the certificate. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "in_use" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Whether this certificate is currently referenced by another OPNsense subsystem (`0`/`1`, read-only). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "is_user" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Whether this certificate is linked to a user account (`0`/`1`, read-only). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "key_type" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Key type and size: `512`, `1024`, `2048` (default), `3072`, `4096`, `8192`, `prime256v1`, `secp384r1`, `secp521r1`. Defaults to `2048`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "lifetime" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Certificate validity period in days. Defaults to `397` (maximum accepted by modern browsers for server certificates). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Distinguished name string of the certificate (read-only). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ocsp_uri" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "OCSP responder URI. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "organization" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "organizational_unit" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "private_key_location" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Where to store the private key: `firewall` (default, store on the firewall) or `external` (CSR only, no key stored). Defaults to `firewall`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "prv" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Base64-encoded PEM private key. Required when `action` is `import`. Computed when `action` is `internal`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "prv_payload" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Decoded PEM private key (read-only, sensitive). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ref_id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Short hex reference ID used by other OPNsense subsystems to reference this certificate. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "rfc3280_purpose" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Extended Key Usage OID string (e.g. `id-kp-serverAuth`, `id-kp-clientAuth`). Computed by OPNsense based on `cert_type` when not explicitly set. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "state" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "valid_from" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Unix timestamp of certificate start date (read-only). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "valid_to" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Unix timestamp of certificate expiry date (read-only). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Manages an end-entity certificate in the OPNsense Trust store. ";
      };
      "opnsense_trust_settings" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "cipher_string" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set of TLS cipher names to allow (OpenSSL cipher-suite identifiers, e.g. `TLS_AES_256_GCM_SHA384`). When empty, OPNsense applies its built-in safe default set. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enable_config_constraints" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "When enabled, OpenSSL policy constraints are enforced. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enable_legacy_sect" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "When enabled, the OpenSSL legacy provider section is active (enables older algorithms such as MD4, DES). Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "fetch_crls" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "When enabled, a cron job periodically fetches CRLs from distribution points embedded in certificates. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Always set to `trust_settings`. Use this value when importing: `terraform import opnsense_trust_settings.settings trust_settings` ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "install_crls" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "When enabled, fetched CRLs are automatically installed into the system trust store. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "store_intermediate_certs" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "When enabled, intermediate CA certificates are stored in the system trust store. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = ''
          Manages Trust global settings (TLS cipher policy, CRL handling, OpenSSL legacy mode). This is a singleton resource that manages existing upstream configuration.

          **Important:** This resource must be imported before it can be managed:
          ```bash
          terraform import opnsense_trust_settings.settings trust_settings
          ```

          After importing, you can manage the configuration with `terraform apply`. Running `terraform destroy` will remove the resource from state but will NOT modify the upstream configuration. '';
      };
      "opnsense_unbound_acl" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "action" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Action to take for queries from the listed networks. One of: `allow`, `deny`, `refuse`, `allow_snoop`, `deny_non_local`, `refuse_non_local`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "When enabled, this ACL entry is active. Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the ACL entry. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Descriptive name for this ACL entry. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "networks" = mkOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "One or more CIDR blocks to match (e.g. `10.0.0.0/24`). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Access Control List entries control which source networks are permitted to query the Unbound resolver. ";
      };
      "opnsense_unbound_domain_override" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "domain" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Domain to override (NOTE: this does not have to be a valid TLD!), e.g. `test` or `mycompany.localdomain` or `1.168.192.in-addr.arpa`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this domain override. Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the host override. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "server" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "IP address of the authoritative DNS server for this domain, e.g. `192.168.100.100`. To use a nondefault port for communication, append an `@` with the port number. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Domain overrides can be used to forward queries for specific domains (and subsequent subdomains) to local or remote DNS servers. ";
      };
      "opnsense_unbound_forward" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "domain" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = ''If a domain is entered here, queries for this specific domain will be forwarded to the specified server. Set to `""` to forward all queries to the specified server. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this query forward.  Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the forward. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "server_ip" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "IP address of DNS server to forward all requests. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "server_port" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Port of DNS server, for usual DNS use `53`, if you use DoT set it to `853`. Defaults to `53`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "type" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Type of forward. Available values: `query`, `dot`. Defaults to `query`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "verify_cn" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''The Common Name of the DNS server (e.g. `dns.example.com`). This field is required to verify its TLS certificate. DNS-over-TLS is susceptible to man-in-the-middle attacks unless certificates can be verified. Set to `""` to accept self-signed yet also potentially fraudulent certificates. Must be set when `type` is `dot`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Query Forwarding section allows for entering arbitrary nameservers to forward queries to. Can forward queries normally, or over TLS. ";
      };
      "opnsense_unbound_host_alias" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "domain" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Domain of the host, e.g. example.com. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this alias for the selected host. Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "hostname" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Name of the host, without the domain part. Use `*` to create a wildcard entry. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the host alias. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "override" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "The associated host override to apply this alias on. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Host aliases can be used to create alternative names for a Host ";
      };
      "opnsense_unbound_host_override" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "domain" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Domain of the host, e.g. example.com ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable the override for this host. Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "hostname" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Name of the host, without the domain part. Use `*` to create a wildcard entry. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the host override. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mx_host" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Host name of MX host, e.g. mail.example.com. Must be set when `type` is `MX`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mx_priority" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Priority of MX record, e.g. 10. Must be set when `type` is `MX`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "server" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "IP address of the host, e.g. 192.168.100.100 or fd00:abcd::1. Must be set when `type` is `A` or `AAAA`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "type" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Type of resource record. Available values: `A`, `AAAA`, `MX`. Defaults to `A`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Host overrides can be used to change DNS results from client queries or to add custom DNS records. ";
      };
      "opnsense_unbound_settings" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "acls" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "default_action" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = ''By default, Unbound will allow queries from all networks. Use this setting to change this behaviour. Since the most specific net block is used, the ACLs as defined in the grid below have preference and will therefore override the behaviour of this setting for the specified networks. Defaults to `"allow"`. '';
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "advanced" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "aggressive_nsec" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Whether to enable RFC8198-based aggressive use of the DNSSEC-Validated cache. Helps to reduce the query rate towards targets but may lead to false negative responses if there are errors in the zone config. Defaults to `true`. ";
                      };
                      "cache" = mkUnsetOption {
                        type = types.submodule {
                          options = {
                            "enable_prefetch" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = "Message cache elements are prefetched before they expire to help keep the cache up to date. When enabled, this option can cause an increase of around 10% more DNS traffic and load on the server, but frequently requested items will not expire from the cache. Defaults to `false`. ";
                            };
                            "host_ttl" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "Time to live in seconds for entries in the host cache. The host cache contains round-trip timing, lameness and EDNS support information. Defaults to `-1`. ";
                            };
                            "keep_probing_hosts" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = "Whether to keep probing hosts that are down in the infrastructure host cache. Hosts that are down are probed about every 120 seconds with an exponential backoff. If hosts do not respond within this time period, they are marked as down for the duration of the host cache TTL. Defaults to `false`. ";
                            };
                            "max_negative_ttl" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "Configure a maximum Negative Time to live in seconds for RRsets and messages in the cache. When the internal TTL expires the negative response cache item is expired. This can be configured to force the resolver to query for data more often in case you wont get a valid answer. Defaults to `-1`. ";
                            };
                            "max_ttl" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "Configure a maximum Time to live in seconds for RRsets and messages in the cache. When the internal TTL expires the cache item is expired. This can be configured to force the resolver to query for data more often and not trust (very large) TTL values. Defaults to `-1`. ";
                            };
                            "min_ttl" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "Configure a minimum Time to live in seconds for RRsets and messages in the cache. If the minimum value kicks in, the data is cached for longer than the domain owner intended, and thus fewer queries are made to look up the data. The 0 value ensures the data in the cache is as the domain owner intended. High values can lead to trouble as the data in the cache might not match up with the actual data anymore. Defaults to `-1`. ";
                            };
                            "msg_cache_size" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = ''Size of the message cache. The message cache stores DNS rcodes and validation statuses. Valid input is plain bytes, optionally appended with 'k', 'm', or 'g' for kilobytes, megabytes or gigabytes respectively. Defaults to `""`. '';
                            };
                            "num_hosts" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "Number of hosts for which information is cached. Defaults to `-1`. ";
                            };
                            "rrset_cache_size" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = ''Size of the RRset cache. Contains the actual RR data. Valid input is plain bytes, optionally appended with 'k', 'm', or 'g' for kilobytes, megabytes or gigabytes respectively. Defaults to `""`. '';
                            };
                            "unwanted_reply_threshold" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "When enabled, a total number of unwanted replies is kept track of in every thread. When it reaches the threshold, a defensive action is taken and a warning is printed to the log file. This defensive action is to clear the RRSet and message caches, hopefully flushing away any poison. Defaults to `-1`. ";
                            };

                          };
                        };
                        description = "Cache configuration. ";
                      };
                      "discard_timeout" = mkUnsetOption {
                        type = (types.either referenceType (types.number));
                        description = ''The wait time in msec where recursion requests are dropped. This is to stop a large number of replies from accumulating. If 'Serve Expired Responses' is enabled this field should be set greater than 'Client Expired Response Timeout', otherwise, these late responses will not update the cache. The value 0 disables it. Default 1900. This setting may increase the "request queue exceeded" counter. Defaults to `-1`. '';
                      };
                      "dnssec_stripped" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "DNSSEC data is required for trust-anchored zones. If such data is absent, the zone becomes bogus. If this is disabled and no DNSSEC data is received, then the zone is made insecure. Defaults to `false`. ";
                      };
                      "enable_prefetch_key" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "When enabled, DNSKEYs are fetched earlier in the validation process when a Delegation signer is encountered. This helps lower the latency of requests but does utilize a little more CPU. Defaults to `false`. ";
                      };
                      "hide_identity" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "When enabled, id.server and hostname.bind queries are refused. Defaults to `false`. ";
                      };
                      "hide_version" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "When enabled, version.server and version.bind queries are refused. Defaults to `false`. ";
                      };
                      "incoming_num_tcp" = mkUnsetOption {
                        type = (types.either referenceType (types.number));
                        description = "The number of incoming TCP buffers to allocate per thread. If 0 is selected then no TCP queries, from clients, are accepted. Defaults to `-1`. ";
                      };
                      "insecure_domains" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "List of domains to mark as insecure. DNSSEC chain of trust is ignored towards the domain name. Defaults to `[]`. ";
                      };
                      "jostle_timeout" = mkUnsetOption {
                        type = (types.either referenceType (types.number));
                        description = "This timeout is used for when the server is very busy. Set to a value that usually results in one round-trip to the authority servers. If too many queries arrive, then 50% of the queries are allowed to run to completion, and the other 50% are replaced with the new incoming query if they have already spent more than their allowed time. This protects against denial of service by slow queries or high query rates. Defaults to `-1`. ";
                      };
                      "logging" = mkUnsetOption {
                        type = types.submodule {
                          options = {
                            "extended_statistics" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = "When enabled, extended statistics are printed. Defaults to `false`. ";
                            };
                            "log_local_actions" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = "When enabled, log lines to inform about local zone actions. These lines are like the local-zone type inform prints out, but they are also printed for the other types of local zones. Defaults to `false`. ";
                            };
                            "log_queries" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = "When enabled, prints one line per query to the log, with the log timestamp and IP address, name, type and class. Note that it takes time to print these lines, which makes the server (significantly) slower. Odd (non-printable) characters in names are printed as '?'. Defaults to `false`. ";
                            };
                            "log_replies" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = "When enabled, prints one line per reply to the log, with the log timestamp and IP address, name, type, class, return code, time to resolve, whether the reply is from the cache and the response size. Note that it takes time to print these lines, which makes the server (significantly) slower. Odd (non-printable) characters in names are printed as '?'. Defaults to `false`. ";
                            };
                            "log_servfail" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = "When enabled, log lines that say why queries return SERVFAIL to clients. This is separate from the verbosity debug logs, much smaller, and printed at the error level, not the info level of debug info from verbosity. Defaults to `false`. ";
                            };
                            "tag_query_reply" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = "When enabled, prints the word 'query: ' and 'reply: ' with logged queries and replies. This makes filtering logs easier. Defaults to `false`. ";
                            };
                            "validation_level" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "Have the validator print validation failures to the log. Regardless of the verbosity setting. Default is 0, off. At 1, for every user query that fails a line is printed to the logs. This way you can monitor what happens with validation. Use a diagnosis tool, such as dig or drill, to find out why validation is failing for these queries. At 2, not only the query that failed is printed but also the reason why Unbound thought it was wrong and which server sent the faulty data. Defaults to `0`. ";
                            };
                            "verbosity_level" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "Select the log verbosity. Level 0 means no verbosity, only errors. Level 1 gives operational information. Level 2 gives detailed operational information. Level 3 gives query level information, output per query. Level 4 gives algorithm level information. Level 5 logs client identification for cache misses. Defaults to `1`. ";
                            };

                          };
                        };
                        description = "Logging configuration. ";
                      };
                      "num_queries_per_thread" = mkUnsetOption {
                        type = (types.either referenceType (types.number));
                        description = ''The number of queries that every thread will service simultaneously. If more queries arrive that need to be serviced, and no queries can be jostled out (see "jostle_timeout"), then these queries are dropped. This forces the client to resend after a timeout, allowing the server time to work on the existing queries. Defaults to `-1`. '';
                      };
                      "outgoing_num_tcp" = mkUnsetOption {
                        type = (types.either referenceType (types.number));
                        description = "The number of outgoing TCP buffers to allocate per thread. If 0 is selected then no TCP queries, to authoritative servers, are done. Defaults to `-1`. ";
                      };
                      "outgoing_range" = mkUnsetOption {
                        type = (types.either referenceType (types.number));
                        description = "The number of ports to open. This number of file descriptors can be opened per thread. Larger numbers need extra resources from the operating system. For performance a very large value is best. For reference, usually double the amount of queries per thread is used. Defaults to `-1`. ";
                      };
                      "private_addresses" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "These are addresses on your private network, and are not allowed to be returned for public internet names. Any occurrence of such addresses are removed from DNS answers. Additionally, the DNSSEC validator may mark the answers bogus. This protects against so-called DNS Rebinding (Only applicable when DNS rebind check is enabled in `System->Settings->Administration`). Defaults to a list of RFC1918 and other private IP ranges. ";
                      };
                      "private_domains" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "List of domains to mark as private. These domains and all its subdomains are allowed to contain private addresses. Defaults to `[]`. ";
                      };
                      "qname_min_strict" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Whether to send minimum amount of information to upstream servers to enhance privacy. Do not fall-back to sending full QNAME to potentially broken nameservers. A lot of domains will not be resolvable when this option in enabled. Only use if you know what you are doing. Defaults to `false`. ";
                      };
                      "serve_expired" = mkUnsetOption {
                        type = types.submodule {
                          options = {
                            "client_response_timeout" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = ''Time in milliseconds before replying to the client with expired data. This essentially enables the serve- stable behavior as specified in RFC 8767 that first tries to resolve before immediately responding with expired data. A recommended value per RFC 8767 is 1800. Setting this to 0 will disable this behavior. Defaults to `""`. '';
                            };
                            "enabled" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = ''Serve expired responses from the cache with a TTL of 0 without waiting for the actual resolution to finish. The TTL can be modified with "record_reply_ttl" value. Defaults to `false`. '';
                            };
                            "record_reply_ttl" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = ''TTL value to use when replying with expired data. If "Client Expired Response Timeout" is also used then it is recommended to use 30 as the value as per RFC 8767. Defaults to `""`. '';
                            };
                            "reset_ttl" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = ''Whether to set the TTL of expired records to the "TTL for Expired Responses" value after a failed attempt to retrieve the record from an upstream server. This makes sure that the expired records will be served as long as there are queries for it. Defaults to `false`. '';
                            };
                            "ttl" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = ''Limits the serving of expired responses to the configured amount of seconds after expiration. A value of 0 disables the limit. A suggested value per RFC 8767 is between 86400 (1 day) and 259200 (3 days). Defaults to `""`. '';
                            };

                          };
                        };
                        description = "Serve expired cache configuration. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dnsbl" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "blocklist_domains" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "List of domains to blocklist. Only exact matches are supported. Defaults to `[]`. ";
                      };
                      "blocklists" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "List of domains (URLs) from where blocklist will be downloaded. Defaults to `[]`. ";
                      };
                      "destination_address" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = ''Destination ip address for entries in the blocklist (leave as `""` to use default: `0.0.0.0`). Not used when "return_nxdomain" is set. Defaults to `""`. '';
                      };
                      "enabled" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Enable the usage of DNS blocklists. Defaults to `false`. ";
                      };
                      "force_safe_search" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Force the usage of SafeSearch on Google, DuckDuckGo, Bing, Qwant, PixaBay and YouTube. Defaults to `false`. ";
                      };
                      "return_nxdomain" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Whether to use the DNS response code NXDOMAIN instead of a destination address. Defaults to `false`. ";
                      };
                      "type" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "Select which kind of DNSBL you want to use. Defaults to `[]`. ";
                      };
                      "whitelist_domains" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "List of domains to whitelist. You can use regular expressions. Defaults to `[]`. ";
                      };
                      "wildcard_domains" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "List of wildcard domains to blocklist. All subdomains of the given domain will be blocked. Blocking first-level domains is not supported. Defaults to `[]`. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "forwarding" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "enabled" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "The configured system nameservers will be used to forward queries to. This will override any entry in the grid below, except for entries with a specific domain. DNS over TLS will never be used for any query bound for a system nameserver. Defaults to `false`. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "general" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "dhcp_domain_override" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = ''The default domain name to use for DHCP lease registration. If empty, the system domain is used. Defaults to `""`. '';
                      };
                      "dns64_prefix" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = ''Defaults to `""`. '';
                      };
                      "enable_dns64" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "When set, Unbound will synthesize AAAA records from A records if no actual AAAA records are present. Defaults to `false`. ";
                      };
                      "enable_dnssec" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Enable DNSSEC validation. Defaults to `false`. ";
                      };
                      "enable_reload_cache_flush" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "When set, the DNS cache will be flushed during each daemon reload. This is the default behavior for Unbound, but may be undesired when multiple dynamic interfaces require frequent reloading. Defaults to `false`. ";
                      };
                      "enable_txt_support" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "When set, then any descriptions associated with Host entries and DHCP Static mappings will create a corresponding TXT record. Defaults to `false`. ";
                      };
                      "enable_wpad" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "When set, CNAME records for the WPAD host of all configured domains will be automatically added as well as overrides for TXT records for domains. This allows automatic proxy configuration in your network but you should not enable it if you are not using WPAD or if you want to configure it by yourself. Defaults to `false`. ";
                      };
                      "enabled" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Enable Unbound DNS resolver. Defaults to `false`. ";
                      };
                      "listen_interfaces" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "Interface IP addresses used for responding to queries from clients. If an interface has both IPv4 and IPv6 IPs, both are used. Queries to other interface IPs not selected below are discarded. The default behavior is to respond to queries on every available IPv4 and IPv6 address. Defaults to `[]`. ";
                      };
                      "local_zone_type" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = ''The local zone type used for the system domain. Type descriptions are available under "local-zone:" in the `unbound.conf(5)` manual page. Defaults to `""`. '';
                      };
                      "no_a_records" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "When set, Unbound will remove all A records from the answer section of all responses. Defaults to `false`. ";
                      };
                      "outgoing_interfaces" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "Utilize different network interfaces that Unbound will use to send queries to authoritative servers and receive their replies. By default all interfaces are used. Note that setting explicit outgoing interfaces only works when they are statically configured. Defaults to `[]`. ";
                      };
                      "port" = mkUnsetOption {
                        type = (types.either referenceType (types.number));
                        description = "The TCP/UDP port used for responding to DNS queries. Defaults to `53`. ";
                      };
                      "register_dhcp_leases" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "When set, then machines that specify their hostname when requesting a DHCP lease will be registered in Unbound, so that their name can be resolved. Defaults to `false`. ";
                      };
                      "register_dhcp_static_mappings" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "When set, then DHCP static mappings will be registered in Unbound, so that their name can be resolved. You should also set the domain in `System: Settings: General` to the proper value. Defaults to `false`. ";
                      };
                      "register_ipv6_link_local" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "When set, then IPv6 link-local addresses will be registered in Unbound, allowing return of unreachable address when more than one listen interface is configured. Defaults to `true`. ";
                      };
                      "register_systems_records" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "When set, then A/AAAA records for the configured listen interfaces will be generated. Disable this to control which interface IP addresses are mapped to the system host/domain name as well as to restrict the amount of information exposed in replies to queries for the system host/domain name. Defaults to `true`. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Always set to `unbound_settings`. Use this value when importing: `terraform import opnsense_unbound_settings.settings unbound_settings` ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = ''
          Manages Unbound DNS resolver settings. This is a singleton resource that manages existing upstream configuration.

          **Important:** This resource must be imported before it can be managed:
          ```bash
          terraform import opnsense_unbound_settings.settings unbound_settings
          ```

          After importing, you can manage the configuration with `terraform apply`. Running `terraform destroy` will remove the resource from state but will NOT modify the upstream configuration. '';
      };
      "opnsense_wireguard_client" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this client config. Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "keep_alive" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "The persistent keepalive interval in seconds. Defaults to `-1`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Name of the client config. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "psk" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Shared secret (PSK) for this peer. You can generate a key using `wg genpsk` on a client with WireGuard installed. Must be a 256-bit base64 string. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "public_key" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Public key of this client config. Must be a 256-bit base64 string. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "server_address" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''The public IP address the endpoint listens to. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "server_port" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "The port the endpoint listens to. Defaults to `-1`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tunnel_address" = mkOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = ''List of addresses allowed to pass trough the tunnel adapter. Please use CIDR notation like `"10.0.0.1/24"`. Defaults to `[]`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Client resources can be used to setup Wireguard clients. ";
      };
      "opnsense_wireguard_server" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "disable_routes" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Disables installation of routes. Usually you only enable this to do own routing decisions via a local gateway and gateway rules. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dns" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "The interface specific DNS servers. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this server. Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "gateway" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''The gateway IP here when using Disable Routes feature. You also have to add this as a gateway in OPNsense. Must be set when `disable_routes` is `true`. Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the server. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "instance" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The instance number to give the wg interface a unique name (wgX). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mtu" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "The interface MTU for this interface. Set to `-1` to use the MTU from main interface. Defaults to `-1`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Name of the server. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "peers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "List of peer IDs for this server. Defaults to `[]`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "port" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "The fixed port for this instance to listen on. The standard port range starts at 51820. Defaults to `-1`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "private_key" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Private key of this server. Must be a 256-bit base64 string. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "public_key" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Public key of this server. Must be a 256-bit base64 string. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tunnel_address" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = ''List of addresses to configure on the tunnel adapter. Please use CIDR notation like `"10.0.0.1/24"`. Defaults to `[]`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Server resources can be used to setup Wireguard servers. ";
      };
      "opnsense_wireguard_settings" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "When enabled, the WireGuard daemon is active. Defaults to `false`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Always set to `wireguard_settings`. Use this value when importing: `terraform import opnsense_wireguard_settings.settings wireguard_settings` ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = ''
          Manages WireGuard general settings. This is a singleton resource that manages existing upstream configuration.

          **Important:** This resource must be imported before it can be managed:
          ```bash
          terraform import opnsense_wireguard_settings.settings wireguard_settings
          ```

          After importing, you can manage the configuration with `terraform apply`. Running `terraform destroy` will remove the resource from state but will NOT modify the upstream configuration. '';
      };

    };
    data = {
      "opnsense_cron_job" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "command" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Command key. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "days" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Day(s) of month to run. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Description of the cron job. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether this cron job is enabled. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "hours" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Hour(s) to run. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the cron job. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "minutes" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Minute(s) to run. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "months" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Month(s) to run. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "parameters" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Parameters passed to the command. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "weekdays" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Day(s) of week to run. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "who" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "User the command runs as. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Cron jobs allow scheduling commands to run at specified times. ";
      };
      "opnsense_dnsmasq_host" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "alias_records" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Alias records of the host. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "client_id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Client identifier of the host. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "cname_records" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "CNAME records of the host. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "comment" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional comment. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "domain" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Domain of the host. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "hardware_addresses" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Hardware addresses of the host. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "hostname" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Name of the host, without the domain part. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the host. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ip_addresses" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "IP addresses of the host. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "is_ignored" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether DHCP packet is ignored for this host. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "is_local_domain" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether this is a local domain. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tag" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the dnsmasq tag associated with this host. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure hosts override for dnsmasq. ";
      };
      "opnsense_firewall_alias" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "categories" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set of category IDs to apply. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "content" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = ''The content of the alias. Enter ISO 3166-1 country codes when `type = "geoip"` (e.g. `["CA", "FR"]`). Enter `__<int>_network`, or alias when `type = "networkgroup"` (e.g. `["__wan_network", "otheralias"]`). Enter OpenVPN group when `type = "authgroup"` (e.g. `["admins"]`). Set to `[]` when `type = "external"`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this firewall alias. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "interface" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Choose on which interface this alias applies. Only applies (and must be set) when `type = "dynipv6host"`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ip_protocol" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = ''Select the Internet Protocol version this alias applies to. Available values: `IPv4`, `IPv6`. Only applies when `type = "asn"`, `type = "geoip"`, or `type = "external"`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The name must start with a letter or single underscore, be less than 32 characters and only consist of alphanumeric characters or underscores. Aliases can be nested using this name. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "path_expression" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''A `jq` expression to extract IP addresses from the downloaded JSON. Only applies (and must be set) when `type = "urljson"`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "stats" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether to maintain a set of counters for each table entry. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "type" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The type of alias. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "update_freq" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = ''The frequency that the list will be refreshed, in days (e.g. for 30 hours, enter `1.25`). Only applies (and must be set) when `type = "urltable"`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Aliases are named lists of networks, hosts or ports that can be used as one entity by selecting the alias name in the various supported sections of the firewall. These aliases are particularly useful to condense firewall rules and minimize changes. ";
      };
      "opnsense_firewall_category" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "auto" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "If set, this category will be removed when unused. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "color" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The color to use. Must be a hex color in format `rrggbb` (e.g. `ff0000`). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The name for this category. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "To ease maintenance of larger rulesets, OPNsense includes categories for the firewall. Each rule can contain one or more categories. ";
      };
      "opnsense_firewall_filter" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "categories" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "The IDs of multiple groups for organizing items. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description for reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether this firewall filter rule is enabled. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "filter" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "action" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "What to do with packets that match the criteria. Hint: the difference between block and reject is that with reject, a packet (TCP RST or ICMP port unreachable for UDP) is returned to the sender, whereas with block the packet is dropped silently. In either case, the original packet is discarded. ";
                      };
                      "allow_options" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Whether packets with IP options are allowed to pass. Otherwise they are blocked. ";
                      };
                      "destination" = mkUnsetOption {
                        type = types.submodule {
                          options = {
                            "invert" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = "Whether the sense of the match is inverted. ";
                            };
                            "net" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = "";
                            };
                            "port" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = "Destination port number or well known name (imap, imaps, http, https, ...), for ranges use a dash. ";
                            };

                          };
                        };
                        description = "";
                      };
                      "direction" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "The direction of the traffic. The default policy is to filter inbound traffic, which sets the policy to the interface originally receiving the traffic. ";
                      };
                      "icmp_type" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "";
                      };
                      "ip_protocol" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "";
                      };
                      "log" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Whether packets handled by this rule are logged. ";
                      };
                      "protocol" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "";
                      };
                      "quick" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Whether a packet matching this rule is the last matching rule. If quick is enabled, the specified action is taken immediately. ";
                      };
                      "schedule" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "";
                      };
                      "source" = mkUnsetOption {
                        type = types.submodule {
                          options = {
                            "invert" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = "Whether the sense of the match is inverted. ";
                            };
                            "net" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = "";
                            };
                            "port" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = "Source port number or well known name (imap, imaps, http, https, ...), for ranges use a dash. ";
                            };

                          };
                        };
                        description = "";
                      };
                      "tcp_flags" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "The TCP flags that must be set for this rule to match. ";
                      };
                      "tcp_flags_out_of" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "The TCP flags that must be cleared for this rule to match. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "interface" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "interface" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "The interfaces the filter rule is applied on. An empty set indicates a floating rule that applies to all interfaces. ";
                      };
                      "invert" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Whether all but selected interfaces are used. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "internal_tagging" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "match_local" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "Specifies that packets must already be tagged with the given tag in order to match the rule. ";
                      };
                      "set_local" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = ''Packets matching this rule are tagged with the specified string. The tag acts as an internal marker that can be used to identify these packets later on. This can be used, for example, to provide trust between interfaces and to determine if packets have been processed by translation rules. Tags are "sticky", meaning that the packet will be tagged even if the rule is not the last matching rule. Further matching rules can replace the tag with a new one but will not remove a previously applied tag. A packet is only ever assigned one tag at a time. '';
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "no_xmlrpc_sync" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether this item is excluded from the HA synchronization process. An already existing item with the same UUID on the synchronization target will not be altered or deleted as long as this is active. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "priority" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "low_delay_set" = mkUnsetOption {
                        type = (types.either referenceType (types.number));
                        description = "Used in combination with set priority, packets which have a TOS of lowdelay and TCP ACKs with no data payload will be assigned this priority when offered. ";
                      };
                      "match" = mkUnsetOption {
                        type = (types.either referenceType (types.number));
                        description = "Only matches packets which have the given queueing priority assigned. ";
                      };
                      "match_tos" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "";
                      };
                      "set" = mkUnsetOption {
                        type = (types.either referenceType (types.number));
                        description = "The specific queueing priority assigned to packets matching this rule. If the packet is transmitted on a vlan(4) interface, the queueing priority will be written as the priority code point in the 802.1Q VLAN header. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "sequence" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "The order of this filter rule. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "source_routing" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "disable_reply_to" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Whether reply-to is explicitly disabled for this rule. ";
                      };
                      "gateway" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "The gateway used for routing. 'default' uses the system routing table. A specific gateway can be chosen to utilize policy based routing. ";
                      };
                      "reply_to" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "How packets route back in the opposite direction (replies), when set to default, packets on WAN type interfaces reply to their connected gateway on the interface (unless globally disabled). A specific gateway may be chosen as well here. This setting is only relevant in the context of a state, for stateless rules there is no defined opposite direction. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "stateful_firewall" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "adaptive_timeouts" = mkUnsetOption {
                        type = types.submodule {
                          options = {
                            "end" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "When reaching this number of state entries, all timeout values become zero, effectively purging all state entries immediately. This value is used to define the scale factor, it should not actually be reached (set a lower state limit). ";
                            };
                            "start" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "When the number of state entries exceeds this value, adaptive scaling begins. All timeout values are scaled linearly with factor `(adaptive.end - number of states) / (adaptive.end - adaptive.start)`. ";
                            };

                          };
                        };
                        description = "";
                      };
                      "max" = mkUnsetOption {
                        type = types.submodule {
                          options = {
                            "new_connections" = mkUnsetOption {
                              type = types.submodule {
                                options = {
                                  "count" = mkUnsetOption {
                                    type = (types.either referenceType (types.number));
                                    description = "Maximum new connections per host, measured over time. ";
                                  };
                                  "seconds" = mkUnsetOption {
                                    type = (types.either referenceType (types.number));
                                    description = "Time interval (seconds) to measure the number of connections. ";
                                  };

                                };
                              };
                              description = "";
                            };
                            "source_connections" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "The maximum number of simultaneous TCP connections which have completed the 3-way handshake that a single host can make. ";
                            };
                            "source_nodes" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "The maximum number of source addresses which can simultaneously have state table entries. ";
                            };
                            "source_states" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "The maximum number of simultaneous state entries that a single source address can create with this rule. ";
                            };
                            "states" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "The limit on the number of concurrent states the rule may create. When this limit is reached, further packets that would create state are dropped until existing states time out. ";
                            };

                          };
                        };
                        description = "";
                      };
                      "no_pfsync" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Whether states created by this rule are prevented from being synced with pfsync. ";
                      };
                      "overload_table" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "The overload table used when max new connections per time interval has been reached. The default virusprot table comes with a default block rule in floating rules, alternatively specify your own table here. ";
                      };
                      "policy" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "How states created by this rule are treated, default (as defined in advanced), floating in which case states are valid on all interfaces or interface bound. Interface bound states are more secure, floating more flexible. ";
                      };
                      "timeout" = mkUnsetOption {
                        type = (types.either referenceType (types.number));
                        description = "State Timeout in seconds (TCP only). ";
                      };
                      "type" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "The state tracking mechanism used, default is full stateful tracking, sloppy ignores sequence numbers, use none for stateless rules. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "traffic_shaping" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "reverse_shaper" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "The selected pipe or queue used to shape packets in the reverse rule direction. ";
                      };
                      "shaper" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "The selected pipe or queue used to shape packets in the rule direction. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Firewall filter rules can be used to restrict or allow traffic from and/or to specific networks as well as influence how traffic should be forwarded ";
      };
      "opnsense_firewall_nat" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "destination" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "invert" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Use this option to invert the sense of the match. ";
                      };
                      "net" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "Specify the IP address, CIDR or alias for the destination of the packet for this mapping. ";
                      };
                      "port" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "Specify the port for the destination of the packet for this mapping. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "disable_nat" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enabling this option will disable NAT for traffic matching this rule and stop processing Outbound NAT rules. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this firewall NAT rule. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "interface" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The interface on which packets must come in to match this rule. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ip_protocol" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Select the Internet Protocol version this rule applies to. Available values: `inet`, `inet6`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "log" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Log packets that are handled by this rule. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "protocol" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Choose which IP protocol this rule should match. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "sequence" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Specify the order of this NAT rule. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "source" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "invert" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Use this option to invert the sense of the match. ";
                      };
                      "net" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "Specify the IP address, CIDR or alias for the source of the packet for this mapping. ";
                      };
                      "port" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = ''Specify the source port for this rule. This is usually random and almost never equal to the destination port range (and should usually be `""`). '';
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "target" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "ip" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "Specify the IP address or alias for the packets to be mapped to. ";
                      };
                      "port" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "Destination port number or well known name (imap, imaps, http, https, ...), for ranges use a dash. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Network Address Translation (abbreviated to NAT) is a way to separate external and internal networks (WANs and LANs), and to share an external IP between clients on the internal network. ";
      };
      "opnsense_firewall_nat_one_to_one" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "categories" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set of category IDs to apply. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "destination" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "invert" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Use this option to invert the sense of the match. ";
                      };
                      "net" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "The 1:1 mapping will only be used for connections to or from the specified destination. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this firewall NAT rule. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "external_net" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Enter the external subnet's starting address for the 1:1 mapping or network. This is the address or network the traffic will translate to/from. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "interface" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Choose which interface this rule applies to. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "log" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Log packets that are handled by this rule. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "nat_reflection" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "NAT reflection mode. One of `default`, `enable`, or `disable`. `default` means OPNsense uses the global firewall NAT reflection setting. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "sequence" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Specify the order of this NAT rule. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "source" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "invert" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Use this option to invert the sense of the match. ";
                      };
                      "net" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "Enter the internal IP address, CIDR or alias for the 1:1 mapping. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "type" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Select `binat` (default) or `nat` here, when nets are equally sized `binat` is usually the best option. Using `nat` we can also map unequal sized networks. A `binat` rule specifies a bidirectional mapping between an external and internal network and can be used from both ends, `nat` only applies in one direction. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "1:1 NAT maps a public IP or subnet to an internal private IP or subnet. All traffic to the public address is forwarded to the internal host or network. Unlike port forwarding, it exposes the full internal system, useful for servers behind a firewall. BINAT rules enable bidirectional translation for consistent incoming and outgoing connections. ";
      };
      "opnsense_firewall_nat_port_forward" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description for reference. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "destination" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "invert" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Whether the sense of the match is inverted. ";
                      };
                      "net" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "The IP address, CIDR or alias for the destination of the packet. ";
                      };
                      "port" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "The port for the destination of the packet. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether this port forwarding rule is enabled. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "interface" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "The interfaces on which packets must come in to match this rule. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ip_protocol" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The Internet Protocol version this rule applies to. Available values: `inet`, `inet6`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "log" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether packets handled by this rule are logged. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "nat_reflection" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "NAT reflection mode. One of `default`, `enable`, or `disable`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "protocol" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The IP protocol this rule matches. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "sequence" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "The order of this port forwarding rule. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "source" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "invert" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Whether the sense of the match is inverted. ";
                      };
                      "net" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "The IP address, CIDR or alias for the source of the packet. ";
                      };
                      "port" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "The source port for this rule. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "target" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "ip" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "The internal IP address or alias packets are forwarded to. ";
                      };
                      "port" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "The internal port number or well known name packets are forwarded to. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Destination NAT (port forwarding) redirects traffic arriving on an external interface to an internal host. Use this to expose internal services (e.g. web servers, SSH) to the outside network. ";
      };
      "opnsense_interface" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "capabilities" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "List of capabilities the interface supports. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "device" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Name of the interface device. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "flags" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "List of flags configured on the interface (equiv. to flags=xxxx in output of ifconfig). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "groups" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "List of groups the interface is a member of. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ipv4" = mkUnsetOption {
                  type = types.listOf (
                    types.submodule {
                      options = {
                        "ipaddr" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "IPv4 address assigned to the interface. ";
                        };
                        "subnetbits" = mkUnsetOption {
                          type = (types.either referenceType (types.number));
                          description = "Number of subnet bits (i.e. CIDR). ";
                        };
                        "tunnel" = mkUnsetOption {
                          type = (types.either referenceType (types.bool));
                          description = "Whether IPv4 tunnelling is enabled. ";
                        };

                      };
                    }
                  );
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ipv6" = mkUnsetOption {
                  type = types.listOf (
                    types.submodule {
                      options = {
                        "autoconf" = mkUnsetOption {
                          type = (types.either referenceType (types.bool));
                          description = "Whether auto-configuration is enabled for the address. ";
                        };
                        "deprecated" = mkUnsetOption {
                          type = (types.either referenceType (types.bool));
                          description = "Whether the address is deprecated. ";
                        };
                        "ipaddr" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "IPv6 address assigned to the interface. ";
                        };
                        "link_local" = mkUnsetOption {
                          type = (types.either referenceType (types.bool));
                          description = "Whether the address is link-local. ";
                        };
                        "subnetbits" = mkUnsetOption {
                          type = (types.either referenceType (types.number));
                          description = "Number of subnet bits (i.e. CIDR). ";
                        };
                        "tentative" = mkUnsetOption {
                          type = (types.either referenceType (types.bool));
                          description = "Whether the address is tentative. ";
                        };
                        "tunnel" = mkUnsetOption {
                          type = (types.either referenceType (types.bool));
                          description = "Whether IPv6 tunnelling is enabled. ";
                        };

                      };
                    }
                  );
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "is_physical" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether the interface is physical or virtual. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "macaddr" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "MAC address assigned to the interface. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "media" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Interface media type settings (see https://man.openbsd.org/ifmedia.4). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "media_raw" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "User-friendly interface media type. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mtu" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Maximum Transmission Unit for the interface. This is typically 1500 bytes but can vary in some circumstances. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "options" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "List of options configured on the interface (equiv. to options=xx in output of ifconfig). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "status" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Status of the interface (e.g. `"active"`). '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "supported_media" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "List of supported media type settings (see https://man.openbsd.org/ifmedia.4). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Interfaces can be used to get configurations of OPNsense interfaces. ";
      };
      "opnsense_interface_all" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "interfaces" = mkUnsetOption {
                  type = types.listOf (
                    types.submodule {
                      options = {
                        "capabilities" = mkUnsetOption {
                          type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                          description = "List of capabilities the interface supports. ";
                        };
                        "device" = mkOption {
                          type = (types.either referenceType (types.str));
                          description = "Name of the interface device. ";
                        };
                        "flags" = mkUnsetOption {
                          type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                          description = "List of flags configured on the interface (equiv. to flags=xxxx in output of ifconfig). ";
                        };
                        "groups" = mkUnsetOption {
                          type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                          description = "List of groups the interface is a member of. ";
                        };
                        "ipv4" = mkUnsetOption {
                          type = types.listOf (
                            types.submodule {
                              options = {
                                "ipaddr" = mkUnsetOption {
                                  type = (types.either referenceType (types.str));
                                  description = "IPv4 address assigned to the interface. ";
                                };
                                "subnetbits" = mkUnsetOption {
                                  type = (types.either referenceType (types.number));
                                  description = "Number of subnet bits (i.e. CIDR). ";
                                };
                                "tunnel" = mkUnsetOption {
                                  type = (types.either referenceType (types.bool));
                                  description = "Whether IPv4 tunnelling is enabled. ";
                                };

                              };
                            }
                          );
                          description = "";
                        };
                        "ipv6" = mkUnsetOption {
                          type = types.listOf (
                            types.submodule {
                              options = {
                                "autoconf" = mkUnsetOption {
                                  type = (types.either referenceType (types.bool));
                                  description = "Whether auto-configuration is enabled for the address. ";
                                };
                                "deprecated" = mkUnsetOption {
                                  type = (types.either referenceType (types.bool));
                                  description = "Whether the address is deprecated. ";
                                };
                                "ipaddr" = mkUnsetOption {
                                  type = (types.either referenceType (types.str));
                                  description = "IPv6 address assigned to the interface. ";
                                };
                                "link_local" = mkUnsetOption {
                                  type = (types.either referenceType (types.bool));
                                  description = "Whether the address is link-local. ";
                                };
                                "subnetbits" = mkUnsetOption {
                                  type = (types.either referenceType (types.number));
                                  description = "Number of subnet bits (i.e. CIDR). ";
                                };
                                "tentative" = mkUnsetOption {
                                  type = (types.either referenceType (types.bool));
                                  description = "Whether the address is tentative. ";
                                };
                                "tunnel" = mkUnsetOption {
                                  type = (types.either referenceType (types.bool));
                                  description = "Whether IPv6 tunnelling is enabled. ";
                                };

                              };
                            }
                          );
                          description = "";
                        };
                        "is_physical" = mkUnsetOption {
                          type = (types.either referenceType (types.bool));
                          description = "Whether the interface is physical or virtual. ";
                        };
                        "macaddr" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "MAC address assigned to the interface. ";
                        };
                        "media" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "Interface media type settings (see https://man.openbsd.org/ifmedia.4). ";
                        };
                        "media_raw" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "User-friendly interface media type. ";
                        };
                        "mtu" = mkUnsetOption {
                          type = (types.either referenceType (types.number));
                          description = "Maximum Transmission Unit for the interface. This is typically 1500 bytes but can vary in some circumstances. ";
                        };
                        "options" = mkUnsetOption {
                          type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                          description = "List of options configured on the interface (equiv. to options=xx in output of ifconfig). ";
                        };
                        "status" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = ''Status of the interface (e.g. `"active"`). '';
                        };
                        "supported_media" = mkUnsetOption {
                          type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                          description = "List of supported media type settings (see https://man.openbsd.org/ifmedia.4). ";
                        };

                      };
                    }
                  );
                  description = "A list of all interfaces present in OPNsense. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "InterfacesAll can be used to get a list of all configurations of OPNsense interfaces. Allows for custom filtering. ";
      };
      "opnsense_interfaces_overview" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "addr4" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Primary IPv4 address of the interface. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "addr6" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Primary IPv6 address of the interface. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "capabilities" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Interface capabilities. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "User-facing interface description. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "device" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = ''Kernel device name of the interface (e.g. `"vtnet0"`). '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether the interface is enabled. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "flags" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Interface flags. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "gateways" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Gateways associated with the interface. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "groups" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Interface groups. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "identifier" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''OPNsense logical interface identifier (e.g. `"wan"`, `"lan"`). '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ifctl_nameserver" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Name servers assigned to the interface via ifctl. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ifctl_prefix" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Prefixes assigned to the interface via ifctl. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ifctl_router" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Routers assigned to the interface via ifctl. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ifctl_searchdomain" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Search domains assigned to the interface via ifctl. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ipv4" = mkUnsetOption {
                  type = types.listOf (
                    types.submodule {
                      options = {
                        "adv_base" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "CARP advertisement base. ";
                        };
                        "adv_skew" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "CARP advertisement skew. ";
                        };
                        "carp_status" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = ''CARP status (e.g. `"MASTER"`, `"BACKUP"`). '';
                        };
                        "ipaddr" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "IPv4 address. ";
                        };
                        "peer" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "CARP peer address. ";
                        };
                        "peer6" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "CARP peer IPv6 address. ";
                        };
                        "vhid" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "CARP virtual host ID. ";
                        };

                      };
                    }
                  );
                  description = "IPv4 addresses assigned to the interface, including CARP virtual IPs. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ipv6" = mkUnsetOption {
                  type = types.listOf (
                    types.submodule {
                      options = {
                        "adv_base" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "CARP advertisement base. ";
                        };
                        "adv_skew" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "CARP advertisement skew. ";
                        };
                        "carp_status" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = ''CARP status (e.g. `"MASTER"`, `"BACKUP"`). '';
                        };
                        "ipaddr" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "IPv6 address. ";
                        };
                        "peer" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "CARP peer address. ";
                        };
                        "peer6" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "CARP peer IPv6 address. ";
                        };
                        "vhid" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "CARP virtual host ID. ";
                        };

                      };
                    }
                  );
                  description = "IPv6 addresses assigned to the interface, including CARP virtual IPs. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "is_physical" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether the interface is a physical interface. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "lagg_hash" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "LAGG hash configuration. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "lagg_options" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "flags" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "LAGG option flags. ";
                      };
                      "flowid_shift" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "LAGG flow ID shift value. ";
                      };

                    };
                  };
                  description = "LAGG options. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "lagg_proto" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''LAGG aggregation protocol (e.g. `"lacp"`). '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "lagg_statistics" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "active_ports" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "Number of active LAGG ports. ";
                      };
                      "flapping" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "LAGG flapping count. ";
                      };

                    };
                  };
                  description = "LAGG statistics. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "link_type" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Link type of the interface (e.g. `"ether"`, `"dhcp"`). '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "macaddr" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Current MAC address of the interface. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "macaddr_hw" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Hardware MAC address of the interface. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "media" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Interface media type settings. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "media_raw" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "User-friendly interface media type. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mtu" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Maximum Transmission Unit of the interface. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "nd6_flags" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "ND6 flags of the interface. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "options" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Interface options. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "routes" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Routes associated with the interface. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "status" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Link status of the interface (e.g. `"active"`). '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "supported_media" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Supported media types. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "vlan" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "parent" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "Parent interface of the VLAN. ";
                      };
                      "pcp" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "VLAN priority code point. ";
                      };
                      "proto" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "VLAN protocol. ";
                      };
                      "tag" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "VLAN tag. ";
                      };

                    };
                  };
                  description = "VLAN configuration of the interface. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "vlan_tag" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "VLAN tag of the interface. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Interfaces Overview provides live state of an OPNsense interface, including IP addresses, CARP status, VLAN, and LAGG information. ";
      };
      "opnsense_interfaces_overview_all" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "interfaces" = mkUnsetOption {
                  type = types.listOf (
                    types.submodule {
                      options = {
                        "addr4" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "Primary IPv4 address of the interface. ";
                        };
                        "addr6" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "Primary IPv6 address of the interface. ";
                        };
                        "capabilities" = mkUnsetOption {
                          type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                          description = "Interface capabilities. ";
                        };
                        "description" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "User-facing interface description. ";
                        };
                        "device" = mkOption {
                          type = (types.either referenceType (types.str));
                          description = ''Kernel device name of the interface (e.g. `"vtnet0"`). '';
                        };
                        "enabled" = mkUnsetOption {
                          type = (types.either referenceType (types.bool));
                          description = "Whether the interface is enabled. ";
                        };
                        "flags" = mkUnsetOption {
                          type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                          description = "Interface flags. ";
                        };
                        "gateways" = mkUnsetOption {
                          type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                          description = "Gateways associated with the interface. ";
                        };
                        "groups" = mkUnsetOption {
                          type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                          description = "Interface groups. ";
                        };
                        "identifier" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = ''OPNsense logical interface identifier (e.g. `"wan"`, `"lan"`). '';
                        };
                        "ifctl_nameserver" = mkUnsetOption {
                          type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                          description = "Name servers assigned to the interface via ifctl. ";
                        };
                        "ifctl_prefix" = mkUnsetOption {
                          type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                          description = "Prefixes assigned to the interface via ifctl. ";
                        };
                        "ifctl_router" = mkUnsetOption {
                          type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                          description = "Routers assigned to the interface via ifctl. ";
                        };
                        "ifctl_searchdomain" = mkUnsetOption {
                          type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                          description = "Search domains assigned to the interface via ifctl. ";
                        };
                        "ipv4" = mkUnsetOption {
                          type = types.listOf (
                            types.submodule {
                              options = {
                                "adv_base" = mkUnsetOption {
                                  type = (types.either referenceType (types.str));
                                  description = "CARP advertisement base. ";
                                };
                                "adv_skew" = mkUnsetOption {
                                  type = (types.either referenceType (types.str));
                                  description = "CARP advertisement skew. ";
                                };
                                "carp_status" = mkUnsetOption {
                                  type = (types.either referenceType (types.str));
                                  description = ''CARP status (e.g. `"MASTER"`, `"BACKUP"`). '';
                                };
                                "ipaddr" = mkUnsetOption {
                                  type = (types.either referenceType (types.str));
                                  description = "IPv4 address. ";
                                };
                                "peer" = mkUnsetOption {
                                  type = (types.either referenceType (types.str));
                                  description = "CARP peer address. ";
                                };
                                "peer6" = mkUnsetOption {
                                  type = (types.either referenceType (types.str));
                                  description = "CARP peer IPv6 address. ";
                                };
                                "vhid" = mkUnsetOption {
                                  type = (types.either referenceType (types.str));
                                  description = "CARP virtual host ID. ";
                                };

                              };
                            }
                          );
                          description = "IPv4 addresses assigned to the interface, including CARP virtual IPs. ";
                        };
                        "ipv6" = mkUnsetOption {
                          type = types.listOf (
                            types.submodule {
                              options = {
                                "adv_base" = mkUnsetOption {
                                  type = (types.either referenceType (types.str));
                                  description = "CARP advertisement base. ";
                                };
                                "adv_skew" = mkUnsetOption {
                                  type = (types.either referenceType (types.str));
                                  description = "CARP advertisement skew. ";
                                };
                                "carp_status" = mkUnsetOption {
                                  type = (types.either referenceType (types.str));
                                  description = ''CARP status (e.g. `"MASTER"`, `"BACKUP"`). '';
                                };
                                "ipaddr" = mkUnsetOption {
                                  type = (types.either referenceType (types.str));
                                  description = "IPv6 address. ";
                                };
                                "peer" = mkUnsetOption {
                                  type = (types.either referenceType (types.str));
                                  description = "CARP peer address. ";
                                };
                                "peer6" = mkUnsetOption {
                                  type = (types.either referenceType (types.str));
                                  description = "CARP peer IPv6 address. ";
                                };
                                "vhid" = mkUnsetOption {
                                  type = (types.either referenceType (types.str));
                                  description = "CARP virtual host ID. ";
                                };

                              };
                            }
                          );
                          description = "IPv6 addresses assigned to the interface, including CARP virtual IPs. ";
                        };
                        "is_physical" = mkUnsetOption {
                          type = (types.either referenceType (types.bool));
                          description = "Whether the interface is a physical interface. ";
                        };
                        "lagg_hash" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "LAGG hash configuration. ";
                        };
                        "lagg_options" = mkUnsetOption {
                          type = types.submodule {
                            options = {
                              "flags" = mkUnsetOption {
                                type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                description = "LAGG option flags. ";
                              };
                              "flowid_shift" = mkUnsetOption {
                                type = (types.either referenceType (types.str));
                                description = "LAGG flow ID shift value. ";
                              };

                            };
                          };
                          description = "LAGG options. ";
                        };
                        "lagg_proto" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = ''LAGG aggregation protocol (e.g. `"lacp"`). '';
                        };
                        "lagg_statistics" = mkUnsetOption {
                          type = types.submodule {
                            options = {
                              "active_ports" = mkUnsetOption {
                                type = (types.either referenceType (types.str));
                                description = "Number of active LAGG ports. ";
                              };
                              "flapping" = mkUnsetOption {
                                type = (types.either referenceType (types.str));
                                description = "LAGG flapping count. ";
                              };

                            };
                          };
                          description = "LAGG statistics. ";
                        };
                        "link_type" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = ''Link type of the interface (e.g. `"ether"`, `"dhcp"`). '';
                        };
                        "macaddr" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "Current MAC address of the interface. ";
                        };
                        "macaddr_hw" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "Hardware MAC address of the interface. ";
                        };
                        "media" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "Interface media type settings. ";
                        };
                        "media_raw" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "User-friendly interface media type. ";
                        };
                        "mtu" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "Maximum Transmission Unit of the interface. ";
                        };
                        "nd6_flags" = mkUnsetOption {
                          type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                          description = "ND6 flags of the interface. ";
                        };
                        "options" = mkUnsetOption {
                          type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                          description = "Interface options. ";
                        };
                        "routes" = mkUnsetOption {
                          type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                          description = "Routes associated with the interface. ";
                        };
                        "status" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = ''Link status of the interface (e.g. `"active"`). '';
                        };
                        "supported_media" = mkUnsetOption {
                          type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                          description = "Supported media types. ";
                        };
                        "vlan" = mkUnsetOption {
                          type = types.submodule {
                            options = {
                              "parent" = mkUnsetOption {
                                type = (types.either referenceType (types.str));
                                description = "Parent interface of the VLAN. ";
                              };
                              "pcp" = mkUnsetOption {
                                type = (types.either referenceType (types.str));
                                description = "VLAN priority code point. ";
                              };
                              "proto" = mkUnsetOption {
                                type = (types.either referenceType (types.str));
                                description = "VLAN protocol. ";
                              };
                              "tag" = mkUnsetOption {
                                type = (types.either referenceType (types.str));
                                description = "VLAN tag. ";
                              };

                            };
                          };
                          description = "VLAN configuration of the interface. ";
                        };
                        "vlan_tag" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "VLAN tag of the interface. ";
                        };

                      };
                    }
                  );
                  description = "A list of all interfaces present in OPNsense. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Interfaces Overview All provides live state for all OPNsense interfaces, including IP addresses, CARP status, VLAN, and LAGG information. ";
      };
      "opnsense_interfaces_vip" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "gateway" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "For some interface types a gateway is required to configure an IP Alias (ppp/pppoe/tun), leave this field empty for all other interface types. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "interface" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Choose which interface this VIP applies to. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mode" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Mode of the VIP. One of `ipalias`, `carp`, or `proxyarp`. `proxyarp` cannot be bound to by anything running on the firewall, such as IPsec, OpenVPN, etc. In most cases an `ipalias` should be used. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "network" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Provide an address and subnet to use. (e.g 192.168.0.1/24) ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Virtual IPs allow an OPNsense firewall to assign multiple IP addresses to the same network interface. ";
      };
      "opnsense_interfaces_vlan" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "device" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Custom VLAN name. Custom names are possible, but only if the start of the name matches the required prefix and contains numeric characters or dots, e.g. `vlan0.1.2` or `qinq0.3.4`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "parent" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "VLAN capable interface to attach the VLAN to, e.g. `vtnet0`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "priority" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "802.1Q VLAN PCP (priority code point). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tag" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "802.1Q VLAN tag. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "VLANs (Virtual LANs) can be used to segment a single physical network into multiple virtual networks. ";
      };
      "opnsense_kea_dhcpv4_peer" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the peer. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Peer name, there should be one entry matching this machine's "This server name". '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "role" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Peer's role. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "url" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "URL of the server instance. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure HA Peers for Kea DHCPv4. ";
      };
      "opnsense_kea_dhcpv4_reservation" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "hostname" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Hostname to offer to the client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the reservation. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ip_address" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "IP address to offer to the client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mac_address" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "MAC/Ether address of the client in question. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "subnet_id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Subnet ID the reservation belongs to. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure DHCPv4 reservations for Kea. ";
      };
      "opnsense_kea_dhcpv4_subnet" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "auto_collect" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Automatically update option data from the GUI for relevant attributes. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dns_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "DNS servers to offer to the clients. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "domain_name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Domain name to offer to the client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "domain_search" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set of Domain Names to be used by the client to locate not-fully-qualified domain names. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "match_client_id" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether to match on client-identifier. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "next_server" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Next server IP address. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ntp_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set of IP addresses indicating NTP servers available to the client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "pools" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set of pools in range or subnet format. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "routers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Default gateways to offer to the clients. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "static_routes" = mkUnsetOption {
                  type = types.listOf (
                    types.submodule {
                      options = {
                        "destination_ip" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "Destination IP address for static route. ";
                        };
                        "router_ip" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "Gateway IP for static route. ";
                        };

                      };
                    }
                  );
                  description = "Static routes that the client should install in its routing cache. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "subnet" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Subnet in use (e.g. `"192.0.2.64/26"`). '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tftp_bootfile" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Boot filename to request. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tftp_server" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "TFTP server address or fqdn. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "time_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set of RFC 868 time servers available to the client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure DHCPv4 subnets for Kea. ";
      };
      "opnsense_kea_dhcpv6_pd_pool" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "delegated_len" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Length of the delegated prefix. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the PD pool. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "prefix" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "IPv6 prefix for the PD pool. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "prefix_len" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Prefix length of the PD pool prefix. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "subnet_id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Subnet ID the PD pool belongs to. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure DHCPv6 prefix delegation pools for Kea. ";
      };
      "opnsense_kea_dhcpv6_peer" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the peer. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Peer name, there should be one entry matching this machine's "This server name". '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "role" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Peer's role. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "url" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "URL of the server instance. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure HA Peers for Kea DHCPv6. ";
      };
      "opnsense_kea_dhcpv6_reservation" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "domain_search" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Domain search list. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "duid" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "DHCP Unique Identifier (DUID) of the client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "hostname" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Hostname to offer to the client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the reservation. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ip_address" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "IPv6 address to offer to the client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "subnet_id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Subnet ID the reservation belongs to. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure DHCPv6 reservations for Kea. ";
      };
      "opnsense_kea_dhcpv6_subnet" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "allocator" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Address allocator in use. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dns_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "DNS servers offered. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "domain_search" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Domain search list. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the subnet. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "interface" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Interface to listen on. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "pd_allocator" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Prefix delegation allocator in use. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "pools" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set of address pools. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "subnet" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "IPv6 Subnet. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure DHCPv6 subnets for Kea. ";
      };
      "opnsense_kea_peer" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the peer. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Peer name, there should be one entry matching this machine's "This server name". '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "role" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Peer's role. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "url" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "URL of the server instance. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure HA Peers for Kea DHCPv4. ";
      };
      "opnsense_kea_reservation" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "hostname" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Hostname to offer to the client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the reservation. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ip_address" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "IP address to offer to the client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mac_address" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "MAC/Ether address of the client in question. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "subnet_id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Subnet ID the reservation belongs to. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure DHCPv4 reservations for Kea. ";
      };
      "opnsense_kea_subnet" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "auto_collect" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Automatically update option data from the GUI for relevant attributes. When set, values for `routers`, `dns_servers` and `ntp_servers` will be ignored. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dns_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "DNS servers to offer to the clients. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "domain_name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Domain name to offer to the client, set to this firewall's domain name when left empty. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "domain_search" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set of Domain Names to be used by the client to locate not-fully-qualified domain names. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "match_client_id" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "By default, KEA uses client-identifiers instead of MAC addresses to locate clients, disabling this option changes back to matching on MAC address which is used by most dhcp implementations. Defaults to `true`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "next_server" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Next server IP address. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ntp_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set of IP addresses indicating NTP (RFC 5905) servers available to the client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "pools" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = ''Set of pools in range or subnet format (e.g. `"192.168.0.100 - 192.168.0.200"` , `"192.0.2.64/26"`). '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "routers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Default gateways to offer to the clients. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "static_routes" = mkUnsetOption {
                  type = types.listOf (
                    types.submodule {
                      options = {
                        "destination_ip" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "Destination IP address for static route. ";
                        };
                        "router_ip" = mkUnsetOption {
                          type = (types.either referenceType (types.str));
                          description = "Gateway IP for static route. ";
                        };

                      };
                    }
                  );
                  description = "Static routes that the client should install in its routing cache. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "subnet" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Subnet in use (e.g. `"192.0.2.64/26"`). '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tftp_bootfile" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Boot filename to request. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tftp_server" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "TFTP server address or fqdn. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "time_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set of RFC 868 time servers available to the client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure DHCPv4 subnets for Kea. ";
      };
      "opnsense_openvpn_client_overwrite" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "block" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether this client is blocked. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "common_name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Client common name to match. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Description of this override. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dns_domain" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "DNS domains pushed to the client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dns_domain_search" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "DNS search domains pushed to the client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dns_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "DNS servers pushed to the client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether this override is enabled. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "local_networks" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Local networks pushed to this client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ntp_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "NTP servers pushed to the client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "push_reset" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether push-reset is sent. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "redirect_gateway" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "redirect-gateway flags. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "register_dns" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Push register-dns to Windows clients. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "remote_networks" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Remote networks reachable behind this client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "route_gateway" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Route gateway override. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "UUIDs of the OpenVPN server instances this override applies to. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tunnel_network" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "IPv4 tunnel network. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tunnel_network_v6" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "IPv6 tunnel network. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "wins_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "WINS servers pushed to the client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Read an existing OpenVPN client-specific override by UUID. ";
      };
      "opnsense_openvpn_instance" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "auth_digest" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Authentication digest. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "auth_gen_token" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Auth token lifetime. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "auth_gen_token_renewal" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Auth token renewal interval. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "auth_gen_token_secret" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Auth token signing secret. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "auth_mode" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Authentication backends. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "bridge_gateway" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Bridge gateway IP. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "bridge_pool" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Bridge DHCP pool. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "carp_depend_on" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "CARP VIP dependency. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "cert_depth" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Certificate chain depth. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "certificate" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Certificate refid. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "certificate_authority" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "CA refid. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "compress_migrate" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Compress migrate. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "crl" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "CRL refid. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "data_ciphers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Data ciphers. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "data_ciphers_fallback" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Fallback data cipher. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Description. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dev_type" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Tunnel device type. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dns_domain" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "DNS domains pushed. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dns_domain_search" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "DNS search domains pushed. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dns_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "DNS servers pushed. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether this instance is enabled. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "fragment" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Fragment size. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "http_proxy" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "HTTP proxy target. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ifconfig_pool_persist" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Persist client IP assignments. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "keepalive_interval" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Keepalive interval. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "keepalive_timeout" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Keepalive timeout. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "local" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Local bind IP. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "local_group" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Required local group. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "max_clients" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Maximum concurrent clients. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mss_fix" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable mssfix. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "no_pool" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Dynamic IP pool disabled. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ntp_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "NTP servers pushed. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "password" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Client-mode password. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "port" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Port. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "port_share" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Port-share target. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "protocol" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Network protocol. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "provision_exclusive" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Provision exclusive sessions. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "push_excluded_routes" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Excluded pushed routes. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "push_inactive" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Inactivity disconnect (seconds). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "push_route" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Pushed routes. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "redirect_gateway" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Redirect gateway flags. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "register_dns" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "register-dns on Windows. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "remote" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Remote hosts. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "remote_cert_tls" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Remote cert TLS enforcement. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "reneg_sec" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Renegotiation interval. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "role" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Role: `server` or `client`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "route" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Local routes. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "route_metric" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Route metric. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "server" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "IPv4 tunnel network. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "server_ipv6" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "IPv6 tunnel network. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "strict_user_cn" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Strict username/CN check. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tls_key" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Static key UUID. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "topology" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Tunnel topology. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tun_mtu" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Tunnel MTU. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "use_ocsp" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "OCSP enforcement. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "username" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Client-mode username. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "username_as_common_name" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Use auth username as CN. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "various_flags" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Misc flags. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "various_push_flags" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Misc push flags. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "verify_client_cert" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Client cert verification mode. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "verify_x509_name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Verify peer X509 name. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "vpn_id" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Numeric VPN ID. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Read an existing OpenVPN instance (server or client) by UUID. ";
      };
      "opnsense_openvpn_static_key" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Description for this static key. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "key" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The static key payload. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mode" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The static-key mode. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "OpenVPN static keys are pre-shared TLS or static keys used by OpenVPN instances. ";
      };
      "opnsense_quagga_bgp_aspath" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "action" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Set permit for match or deny to negate the rule. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "as" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The AS pattern you want to match, regexp allowed (e.g. `.$` or `_1$`). It's not validated so please be careful! ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "An optional description for this AS path. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this AS path. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "number" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "The ACL rule number (0-4294967294); keep in mind that there are no sequence numbers with AS-Path lists. When you want to add a new line between you have to completely remove the ACL! ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure AS Path lists for BGP. ";
      };
      "opnsense_quagga_bgp_communitylist" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "action" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Set permit for match or deny to negate the rule. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "community" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The community you want to match. You can also regex and it is not validated so please be careful. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "An optional description for this prefix list. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this community list. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "number" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Set the number of your Community-List. 1-99 are standard lists while 100-500 are expanded lists. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "seq_number" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "The ACL sequence number (10-99). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure community lists for BGP. ";
      };
      "opnsense_quagga_bgp_neighbor" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "as_override" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Override AS number of the originating router with the local AS number. This command is only allowed for eBGP peers. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "attribute_unchanged" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Specify attribute to be left unchanged when sending advertisements to a peer. Read more at FRR documentation. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "bfd" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable BFD support for this neighbor. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "connect_timer" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "The time in seconds how fast a neighbor tries to reconnect. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "default_route" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable to send Defaultroute. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "An optional description for this neighbor. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "disable_connected_check" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable to allow peerings between directly connected eBGP peers using loopback addresses. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this neighbor. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "hold_down" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "The time in seconds when a neighbor is considered dead. This is usually 3 times the keepalive timer. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "keep_alive" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Enable Keepalive timer to check if the neighbor is still up. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "link_local_interface" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Interface to use for IPv6 link-local neighbours. Must be a valid OPNsense interface in lowercase (e.g. `wan`). Please refer to the FRR documentation for more information. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "local_ip" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The local IP connecting to the neighbor. This is only required for BGP authentication. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "md5_password" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The password for BGP authentication. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "multi_hop" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable multi-hop. Specifying ebgp-multihop allows sessions with eBGP neighbors to establish when they are multiple hops away. When the neighbor is not directly connected and this knob is not enabled, the session will not establish. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "multi_protocol" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Mark this neighbor as multiprotocol capable per RFC 2283. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "next_hop_self" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable the next-hop-self command. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "next_hop_self_all" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = ''Add the parameter "all" after next-hop-self command. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "peer_ip" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The IP of your neighbor. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "prefix_list_in" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The prefix list ID for inbound direction. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "prefix_list_out" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The prefix list ID for outbound direction. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "remote_as" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "The neighbor AS. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "route_map_in" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The route map ID for inbound direction. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "route_map_out" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The route map ID for outbound direction. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "rr_client" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable route reflector client. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "update_source" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Physical name of the IPv4 interface facing the peer. Must be a valid OPNsense interface in lowercase (e.g. `wan`). Please refer to the FRR documentation for more information. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "weight" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Specify a default weight value for the neighbor’s routes. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure neighbors for BGP. ";
      };
      "opnsense_quagga_bgp_prefixlist" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "action" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Set permit for match or deny to negate the rule. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "An optional description for this prefix list. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this prefix list. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ip_version" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Set the IP version to use. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The name of this prefix list. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "network" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''The network pattern you want to match. You can also add "ge" or "le" additions after the network statement. It's not validated so please be careful! '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "number" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "The ACL sequence number (1-4294967294). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure prefix lists for BGP. ";
      };
      "opnsense_quagga_bgp_routemap" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "action" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Set permit for match or deny to negate the rule. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "aspaths" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set the AS Path list IDs to use. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "community_lists" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set the community list IDs to use. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "An optional description for this route map. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enable this route map. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The name of this route map. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "prefix_lists" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set the prefix list IDs to use. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "route_map_id" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "The Route-map ID between 1 and 65535. Be aware that the sorting will be done under the hood, so when you add an entry between it gets to the right position. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "set" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''Free text field for your set, please be careful! You can set e.g. `local-preference 300` or `community 1:1` (http://www.nongnu.org/quagga/docs/docs-multi/Route-Map-Set-Command.html#Route-Map-Set-Command). Defaults to `""`. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Configure route maps for BGP. ";
      };
      "opnsense_route" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether this route is enabled. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "gateway" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Which gateway this route applies, e.g. `WAN`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "network" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Destination network for this static route. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Routes can be used to teach your firewall which path it should take when forwarding packets to a specific network. ";
      };
      "opnsense_trust_ca" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "action" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Creation action used for this CA. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "caref" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Reference ID of the parent CA. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "city" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "common_name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Common name (CN) of the CA certificate. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "country" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "ISO 3166-1 alpha-2 country code. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "crt" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Base64-encoded PEM certificate body. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "crt_payload" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Decoded PEM certificate body. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Description of the CA. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "digest" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Digest algorithm. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "email" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the Certificate Authority. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "key_type" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Key type and size. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "lifetime" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Certificate validity period in days (template value). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Distinguished name string of the CA. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ocsp_uri" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "organization" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "organizational_unit" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "prv" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Base64-encoded PEM private key. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "prv_payload" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Decoded PEM private key (sensitive). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ref_id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Short hex reference ID used by other OPNsense subsystems to reference this CA. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "serial" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Next serial number. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "state" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "valid_from" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Unix timestamp of certificate start date. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "valid_to" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Unix timestamp of certificate expiry date. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Retrieves a Trust Certificate Authority by UUID. ";
      };
      "opnsense_trust_cert" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "action" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "altnames_dns" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "altnames_email" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "altnames_ip" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "altnames_uri" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "caref" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Reference ID of the signing CA. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "cert_type" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "city" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "common_name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "country" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "crt" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Base64-encoded PEM certificate body. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "crt_payload" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "csr" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Base64-encoded PEM CSR. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "csr_payload" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "digest" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "email" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the certificate. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "in_use" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "is_user" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "key_type" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "lifetime" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ocsp_uri" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "organization" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "organizational_unit" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "private_key_location" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "prv" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Base64-encoded PEM private key. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "prv_payload" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "ref_id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Short hex reference ID used by other OPNsense subsystems. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "rfc3280_purpose" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "state" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "valid_from" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "valid_to" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Retrieves an end-entity certificate from the OPNsense Trust store by UUID. ";
      };
      "opnsense_trust_settings" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "cipher_string" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Set of allowed TLS cipher names. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enable_config_constraints" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether OpenSSL policy constraints are enforced. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enable_legacy_sect" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether the OpenSSL legacy provider section is active. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "fetch_crls" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether the periodic CRL fetch cron job is enabled. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Always set to `trust_settings`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "install_crls" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether fetched CRLs are automatically installed. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "store_intermediate_certs" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether intermediate CA certificates are stored in the system trust store. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Reads Trust global settings from the upstream system. ";
      };
      "opnsense_unbound_acl" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "action" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Action to take for queries from the listed networks. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether this ACL entry is enabled. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Descriptive name for this ACL entry. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "networks" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "One or more CIDR blocks to match. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Access Control List entries control which source networks are permitted to query the Unbound resolver. ";
      };
      "opnsense_unbound_domain_override" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "domain" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Domain to override (NOTE: this does not have to be a valid TLD!), e.g. `test` or `mycompany.localdomain` or `1.168.192.in-addr.arpa`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether this route is enabled. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "server" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "IP address of the authoritative DNS server for this domain, e.g. `192.168.100.100`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Domain overrides can be used to forward queries for specific domains (and subsequent subdomains) to local or remote DNS servers. ";
      };
      "opnsense_unbound_forward" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "domain" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "If a domain is entered here, queries for this specific domain will be forwarded to the specified server. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether this route is enabled. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "server_ip" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "IP address of DNS server to forward all requests. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "server_port" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Port of DNS server, for usual DNS use `53`, if you use DoT set it to `853`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "type" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Type of forward. Available values: `query`, `dot`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "verify_cn" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The Common Name of the DNS server (e.g. `dns.example.com`). This field is required to verify its TLS certificate. DNS-over-TLS is susceptible to man-in-the-middle attacks unless certificates can be verified. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Query Forwarding section allows for entering arbitrary nameservers to forward queries to. Can forward queries normally, or over TLS. ";
      };
      "opnsense_unbound_host_alias" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "domain" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Domain of the host, e.g. example.com ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether this route is enabled. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "hostname" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Name of the host, without the domain part. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "override" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The associated host override to apply this alias on. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Host aliases can be used to create alternative names for a Host. ";
      };
      "opnsense_unbound_host_override" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Optional description here for your reference (not parsed). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "domain" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Domain of the host, e.g. example.com ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether this route is enabled. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "hostname" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Name of the host, without the domain part. Use `*` to create a wildcard entry. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mx_host" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Host name of MX host, e.g. mail.example.com. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mx_priority" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Priority of MX record, e.g. 10. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "server" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "IP address of the host, e.g. 192.168.100.100 or fd00:abcd::1. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "type" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Type of resource record. Available values: `A`, `AAAA`, `MX`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Host overrides can be used to change DNS results from client queries or to add custom DNS records. ";
      };
      "opnsense_unbound_settings" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "acls" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "default_action" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "By default, Unbound will allow queries from all networks. Use this setting to change this behaviour. Since the most specific net block is used, the ACLs as defined in the grid below have preference and will therefore override the behaviour of this setting for the specified networks. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "advanced" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "aggressive_nsec" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Whether to enable RFC8198-based aggressive use of the DNSSEC-Validated cache. Helps to reduce the query rate towards targets but may lead to false negative responses if there are errors in the zone config. ";
                      };
                      "cache" = mkUnsetOption {
                        type = types.submodule {
                          options = {
                            "enable_prefetch" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = "Message cache elements are prefetched before they expire to help keep the cache up to date. When enabled, this option can cause an increase of around 10% more DNS traffic and load on the server, but frequently requested items will not expire from the cache. ";
                            };
                            "host_ttl" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "Time to live in seconds for entries in the host cache. The host cache contains round-trip timing, lameness and EDNS support information. ";
                            };
                            "keep_probing_hosts" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = "Whether to keep probing hosts that are down in the infrastructure host cache. Hosts that are down are probed about every 120 seconds with an exponential backoff. If hosts do not respond within this time period, they are marked as down for the duration of the host cache TTL. ";
                            };
                            "max_negative_ttl" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "Configure a maximum Negative Time to live in seconds for RRsets and messages in the cache. When the internal TTL expires the negative response cache item is expired. This can be configured to force the resolver to query for data more often in case you wont get a valid answer. ";
                            };
                            "max_ttl" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "Configure a maximum Time to live in seconds for RRsets and messages in the cache. When the internal TTL expires the cache item is expired. This can be configured to force the resolver to query for data more often and not trust (very large) TTL values. ";
                            };
                            "min_ttl" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "Configure a minimum Time to live in seconds for RRsets and messages in the cache. If the minimum value kicks in, the data is cached for longer than the domain owner intended, and thus fewer queries are made to look up the data. The 0 value ensures the data in the cache is as the domain owner intended. High values can lead to trouble as the data in the cache might not match up with the actual data anymore. ";
                            };
                            "msg_cache_size" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = "Size of the message cache. The message cache stores DNS rcodes and validation statuses. Valid input is plain bytes, optionally appended with 'k', 'm', or 'g' for kilobytes, megabytes or gigabytes respectively. ";
                            };
                            "num_hosts" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "Number of hosts for which information is cached. ";
                            };
                            "rrset_cache_size" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = "Size of the RRset cache. Contains the actual RR data. Valid input is plain bytes, optionally appended with 'k', 'm', or 'g' for kilobytes, megabytes or gigabytes respectively. ";
                            };
                            "unwanted_reply_threshold" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "When enabled, a total number of unwanted replies is kept track of in every thread. When it reaches the threshold, a defensive action is taken and a warning is printed to the log file. This defensive action is to clear the RRSet and message caches, hopefully flushing away any poison. ";
                            };

                          };
                        };
                        description = "Cache configuration. ";
                      };
                      "discard_timeout" = mkUnsetOption {
                        type = (types.either referenceType (types.number));
                        description = ''The wait time in msec where recursion requests are dropped. This is to stop a large number of replies from accumulating. If 'Serve Expired Responses' is enabled this field should be set greater than 'Client Expired Response Timeout', otherwise, these late responses will not update the cache. The value 0 disables it. Default 1900. This setting may increase the "request queue exceeded" counter. '';
                      };
                      "dnssec_stripped" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "DNSSEC data is required for trust-anchored zones. If such data is absent, the zone becomes bogus. If this is disabled and no DNSSEC data is received, then the zone is made insecure. ";
                      };
                      "enable_prefetch_key" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "When enabled, DNSKEYs are fetched earlier in the validation process when a Delegation signer is encountered. This helps lower the latency of requests but does utilize a little more CPU. ";
                      };
                      "hide_identity" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "When enabled, id.server and hostname.bind queries are refused. ";
                      };
                      "hide_version" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "When enabled, version.server and version.bind queries are refused. ";
                      };
                      "incoming_num_tcp" = mkUnsetOption {
                        type = (types.either referenceType (types.number));
                        description = "The number of incoming TCP buffers to allocate per thread. If 0 is selected then no TCP queries, from clients, are accepted. ";
                      };
                      "insecure_domains" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "List of domains to mark as insecure. DNSSEC chain of trust is ignored towards the domain name. ";
                      };
                      "jostle_timeout" = mkUnsetOption {
                        type = (types.either referenceType (types.number));
                        description = "This timeout is used for when the server is very busy. Set to a value that usually results in one round-trip to the authority servers. If too many queries arrive, then 50% of the queries are allowed to run to completion, and the other 50% are replaced with the new incoming query if they have already spent more than their allowed time. This protects against denial of service by slow queries or high query rates. ";
                      };
                      "logging" = mkUnsetOption {
                        type = types.submodule {
                          options = {
                            "extended_statistics" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = "When enabled, extended statistics are printed. ";
                            };
                            "log_local_actions" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = "When enabled, log lines to inform about local zone actions. These lines are like the local-zone type inform prints out, but they are also printed for the other types of local zones. ";
                            };
                            "log_queries" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = "When enabled, prints one line per query to the log, with the log timestamp and IP address, name, type and class. Note that it takes time to print these lines, which makes the server (significantly) slower. Odd (non-printable) characters in names are printed as '?'. ";
                            };
                            "log_replies" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = "When enabled, prints one line per reply to the log, with the log timestamp and IP address, name, type, class, return code, time to resolve, whether the reply is from the cache and the response size. Note that it takes time to print these lines, which makes the server (significantly) slower. Odd (non-printable) characters in names are printed as '?'. ";
                            };
                            "log_servfail" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = "When enabled, log lines that say why queries return SERVFAIL to clients. This is separate from the verbosity debug logs, much smaller, and printed at the error level, not the info level of debug info from verbosity. ";
                            };
                            "tag_query_reply" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = "When enabled, prints the word 'query: ' and 'reply: ' with logged queries and replies. This makes filtering logs easier. ";
                            };
                            "validation_level" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "Have the validator print validation failures to the log. Regardless of the verbosity setting. Default is 0, off. At 1, for every user query that fails a line is printed to the logs. This way you can monitor what happens with validation. Use a diagnosis tool, such as dig or drill, to find out why validation is failing for these queries. At 2, not only the query that failed is printed but also the reason why Unbound thought it was wrong and which server sent the faulty data. ";
                            };
                            "verbosity_level" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "Select the log verbosity. Level 0 means no verbosity, only errors. Level 1 gives operational information. Level 2 gives detailed operational information. Level 3 gives query level information, output per query. Level 4 gives algorithm level information. Level 5 logs client identification for cache misses. ";
                            };

                          };
                        };
                        description = "Logging configuration. ";
                      };
                      "num_queries_per_thread" = mkUnsetOption {
                        type = (types.either referenceType (types.number));
                        description = ''The number of queries that every thread will service simultaneously. If more queries arrive that need to be serviced, and no queries can be jostled out (see "jostle_timeout"), then these queries are dropped. This forces the client to resend after a timeout, allowing the server time to work on the existing queries. '';
                      };
                      "outgoing_num_tcp" = mkUnsetOption {
                        type = (types.either referenceType (types.number));
                        description = "The number of outgoing TCP buffers to allocate per thread. If 0 is selected then no TCP queries, to authoritative servers, are done. ";
                      };
                      "outgoing_range" = mkUnsetOption {
                        type = (types.either referenceType (types.number));
                        description = "The number of ports to open. This number of file descriptors can be opened per thread. Larger numbers need extra resources from the operating system. For performance a very large value is best. For reference, usually double the amount of queries per thread is used. ";
                      };
                      "private_addresses" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "These are addresses on your private network, and are not allowed to be returned for public internet names. Any occurrence of such addresses are removed from DNS answers. Additionally, the DNSSEC validator may mark the answers bogus. This protects against so-called DNS Rebinding (Only applicable when DNS rebind check is enabled in `System->Settings->Administration`). ";
                      };
                      "private_domains" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "List of domains to mark as private. These domains and all its subdomains are allowed to contain private addresses. ";
                      };
                      "qname_min_strict" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Whether to send minimum amount of information to upstream servers to enhance privacy. Do not fall-back to sending full QNAME to potentially broken nameservers. A lot of domains will not be resolvable when this option in enabled. Only use if you know what you are doing. ";
                      };
                      "serve_expired" = mkUnsetOption {
                        type = types.submodule {
                          options = {
                            "client_response_timeout" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = "Time in milliseconds before replying to the client with expired data. This essentially enables the serve- stable behavior as specified in RFC 8767 that first tries to resolve before immediately responding with expired data. A recommended value per RFC 8767 is 1800. Setting this to 0 will disable this behavior. ";
                            };
                            "enabled" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = ''Serve expired responses from the cache with a TTL of 0 without waiting for the actual resolution to finish. The TTL can be modified with "record_reply_ttl" value. '';
                            };
                            "record_reply_ttl" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = ''TTL value to use when replying with expired data. If "Client Expired Response Timeout" is also used then it is recommended to use 30 as the value as per RFC 8767. '';
                            };
                            "reset_ttl" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = ''Whether to set the TTL of expired records to the "TTL for Expired Responses" value after a failed attempt to retrieve the record from an upstream server. This makes sure that the expired records will be served as long as there are queries for it. '';
                            };
                            "ttl" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = "Limits the serving of expired responses to the configured amount of seconds after expiration. A value of 0 disables the limit. A suggested value per RFC 8767 is between 86400 (1 day) and 259200 (3 days). ";
                            };

                          };
                        };
                        description = "Serve expired cache configuration. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dnsbl" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "blocklist_domains" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "List of domains to blocklist. Only exact matches are supported. ";
                      };
                      "blocklists" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "List of domains (URLs) from where blocklist will be downloaded. ";
                      };
                      "destination_address" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = ''Destination ip address for entries in the blocklist (leave as `""` to use default: `0.0.0.0`). Not used when "return_nxdomain" is set. '';
                      };
                      "enabled" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Enable the usage of DNS blocklists. ";
                      };
                      "force_safe_search" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Force the usage of SafeSearch on Google, DuckDuckGo, Bing, Qwant, PixaBay and YouTube. ";
                      };
                      "return_nxdomain" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Whether to use the DNS response code NXDOMAIN instead of a destination address. ";
                      };
                      "type" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "Select which kind of DNSBL you want to use. ";
                      };
                      "whitelist_domains" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "List of domains to whitelist. You can use regular expressions. ";
                      };
                      "wildcard_domains" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "List of wildcard domains to blocklist. All subdomains of the given domain will be blocked. Blocking first-level domains is not supported. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "forwarding" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "enabled" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "The configured system nameservers will be used to forward queries to. This will override any entry in the grid below, except for entries with a specific domain. DNS over TLS will never be used for any query bound for a system nameserver. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "general" = mkUnsetOption {
                  type = types.submodule {
                    options = {
                      "dhcp_domain_override" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "The default domain name to use for DHCP lease registration. If empty, the system domain is used. ";
                      };
                      "dns64_prefix" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "";
                      };
                      "enable_dns64" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "When set, Unbound will synthesize AAAA records from A records if no actual AAAA records are present. ";
                      };
                      "enable_dnssec" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Whether DNSSEC validation is enabled. ";
                      };
                      "enable_reload_cache_flush" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "When set, the DNS cache is flushed every time the service is restarted. ";
                      };
                      "enable_txt_support" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "When set, then DNS queries for TXT records will also be registered with the source address of the query. ";
                      };
                      "enable_wpad" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "When set, Unbound will respond to queries for wpad.yourdomain.tld. ";
                      };
                      "enabled" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "Whether Unbound DNS resolver is enabled. ";
                      };
                      "listen_interfaces" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "Interface IP addresses used for responding to queries from clients. If an interface has both IPv4 and IPv6 IPs, both are used. Queries to other interface IPs not selected below are discarded. The default behavior is to respond to queries on every available IPv4 and IPv6 address. ";
                      };
                      "local_zone_type" = mkUnsetOption {
                        type = (types.either referenceType (types.str));
                        description = "The local zone type controls how the local zone is handled. By default unbound will be a 'transparent' forwarding server for the local zone. Other options like 'static' will make it directly authoritative for the local zone. ";
                      };
                      "no_a_records" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "When set, Unbound will remove all A records from the answer section of all responses. ";
                      };
                      "outgoing_interfaces" = mkUnsetOption {
                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                        description = "Interface IP addresses used for outgoing queries to authoritative servers and receiving their replies. If an interface has both IPv4 and IPv6 IPs, both are used. The default behavior is to use all available IPv4 and IPv6 addresses. ";
                      };
                      "port" = mkUnsetOption {
                        type = (types.either referenceType (types.number));
                        description = "The TCP/UDP port used for responding to DNS queries. ";
                      };
                      "register_dhcp_leases" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "When set, then machines that specify their hostname when requesting a DHCP lease will be registered in Unbound, so that their name can be resolved. ";
                      };
                      "register_dhcp_static_mappings" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "When set, then DHCP static mappings will be registered in Unbound, so that their name can be resolved. ";
                      };
                      "register_ipv6_link_local" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "When set, then IPv6 link-local addresses will also be registered. ";
                      };
                      "register_systems_records" = mkUnsetOption {
                        type = (types.either referenceType (types.bool));
                        description = "When set, then entries configured under System: Settings: General will be added to Unbound's DNS resolver. ";
                      };

                    };
                  };
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Always set to `singleton`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Reads Unbound DNS resolver settings from the upstream system. ";
      };
      "opnsense_wireguard_client" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether this client config is enabled. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "keep_alive" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "The persistent keepalive interval in seconds. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Name of the client config. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "psk" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Shared secret (PSK) for this peer. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "public_key" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Public key of this client config. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "server_address" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The public IP address the endpoint listens to. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "server_port" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "The port the endpoint listens to. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tunnel_address" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "List of addresses allowed to pass trough the tunnel adapter. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Client resources can be used to setup Wireguard clients. ";
      };
      "opnsense_wireguard_server" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "disable_routes" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Disables installation of routes. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "dns" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "The interface specific DNS servers. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether this server is enabled. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "gateway" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The gateway IP here when using Disable Routes feature. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the resource. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "instance" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The instance number to give the wg interface a unique name (wgX). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mtu" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "The interface MTU for this interface. Set to `-1` to use the MTU from main interface. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Name of the server. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "peers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "List of peer IDs for this server. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "port" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "The fixed port for this instance to listen on. The standard port range starts at 51820. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "private_key" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Private key of this server. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "public_key" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Public key of this server. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tunnel_address" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "List of addresses to configure on the tunnel adapter. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Server resources can be used to setup Wireguard servers. ";
      };
      "opnsense_wireguard_settings" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated opnsense provider";
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

                "enabled" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Whether the WireGuard daemon is enabled. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Always set to `wireguard_settings`. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "Reads WireGuard general settings from the upstream system. ";
      };

    };
  };
  # hacky unfortunately
  options = {
    inherit (config.generated) resource data provider;
  };
}
