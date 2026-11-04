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
  _file = "generated cloudflare provider";
  config.terraform.required_providers.cloudflare = {
    source = "cloudflare/cloudflare";
    version = "5.28.0";
  };
  config.generated.provider."cloudflare" = mkUnsetOption {
    type = types.attrsOf (
      types.submodule (
        { name, ... }: {
          _file = "generated cloudflare provider";
          options = {
            _name = mkOption {
              internal = true;
              type = types.str;
              default = name;
            };
            _version = mkOption {
              internal = true;
              type = types.str;
              default = "5.28.0";
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
              default = "\${cloudflare.${name}}";
              defaultText = literalExpression "\"\${cloudflare.${name}}\"";
              description = ''
                ID of provider, read only. Only returns a terraform reference string.
              '';
            };
            "api_key" = mkUnsetOption {
              type = (types.either referenceType (types.str));
              description = "The API key for operations. Alternatively, can be configured using the `CLOUDFLARE_API_KEY` environment variable. API keys are [now considered legacy by Cloudflare](https://developers.cloudflare.com/fundamentals/api/get-started/keys/#limitations), API tokens should be used instead. Must provide only one of `api_key`, `api_token`, `api_user_service_key`. ";
            };
            "api_token" = mkUnsetOption {
              type = (types.either referenceType (types.str));
              description = "The API Token for operations. Alternatively, can be configured using the `CLOUDFLARE_API_TOKEN` environment variable. Must provide only one of `api_key`, `api_token`, `api_user_service_key`. ";
            };
            "api_user_service_key" = mkUnsetOption {
              type = (types.either referenceType (types.str));
              description = "A special Cloudflare API key good for a restricted set of endpoints. Alternatively, can be configured using the `CLOUDFLARE_API_USER_SERVICE_KEY` environment variable. Must provide only one of `api_key`, `api_token`, `api_user_service_key`. ";
            };
            "base_url" = mkUnsetOption {
              type = (types.either referenceType (types.str));
              description = "Value to override the default HTTP client base URL. Alternatively, can be configured using the `base_url` environment variable. ";
            };
            "email" = mkUnsetOption {
              type = (types.either referenceType (types.str));
              description = "A registered Cloudflare email address. Alternatively, can be configured using the `CLOUDFLARE_EMAIL` environment variable. Required when using `api_key`. Conflicts with `api_token`. ";
            };
            "user_agent_operator_suffix" = mkUnsetOption {
              type = (types.either referenceType (types.str));
              description = "A value to append to the HTTP User Agent for all API calls. This value is not something most users need to modify however, if you are using a non-standard provider or operator configuration, this is recommended to assist in uniquely identifying your traffic. **Setting this value will remove the Terraform version from the HTTP User Agent string and may have unintended consequences**. Alternatively, can be configured using the `CLOUDFLARE_USER_AGENT_OPERATOR_SUFFIX` environment variable. ";
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
      "cloudflare_ruleset" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated cloudflare provider";
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

                "account_id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The unique ID of the account. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "description" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "An informative description of the ruleset. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The unique ID of the ruleset. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "kind" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = ''
                    The kind of the ruleset.
                    Available values: "managed", "custom", "root", "zone". '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "last_updated" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The timestamp of when the ruleset was last modified. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "The human-readable name of the ruleset. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "phase" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = ''
                    The phase of the ruleset.
                    Available values: "ddos_l4", "ddos_l7", "http_config_settings", "http_custom_errors", "http_log_custom_fields", "http_ratelimit", "http_request_cache_settings", "http_request_dynamic_redirect", "http_request_firewall_custom", "http_request_firewall_managed", "http_request_late_transform", "http_request_origin", "http_request_redirect", "http_request_sanitize", "http_request_sbfm", "http_request_transform", "http_response_cache_settings", "http_response_compression", "http_response_firewall_managed", "http_response_headers_transform", "magic_transit", "magic_transit_ids_managed", "magic_transit_managed", "magic_transit_ratelimit". '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "rules" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.listOf (
                        types.submodule {
                          options = {
                            "action" = mkOption {
                              type = (types.either referenceType (types.str));
                              description = ''
                                The action to perform when the rule matches.
                                Available values: "block", "challenge", "compress_response", "ddos_dynamic", "execute", "force_connection_close", "js_challenge", "log", "log_custom_field", "managed_challenge", "redirect", "rewrite", "route", "score", "serve_error", "set_cache_control", "set_cache_settings", "set_cache_tags", "set_config", "skip". '';
                            };
                            "action_parameters" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "additional_cacheable_ports" = mkUnsetOption {
                                        type = (types.either referenceType (types.listOf (types.either referenceType (types.number))));
                                        description = "A list of additional ports that caching should be enabled on. ";
                                      };
                                      "algorithms" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.listOf (
                                              types.submodule {
                                                options = {
                                                  "name" = mkUnsetOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = ''
                                                      Name of the compression algorithm to enable.
                                                      Available values: "none", "auto", "default", "gzip", "brotli", "zstd". '';
                                                  };

                                                };
                                              }
                                            )
                                          )
                                        );
                                        description = "Custom order for compression algorithms. ";
                                      };
                                      "asset_name" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The name of a custom asset to serve as the response. ";
                                      };
                                      "automatic_https_rewrites" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether to enable Automatic HTTPS Rewrites. ";
                                      };
                                      "autominify" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "css" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether to minify CSS files. ";
                                                };
                                                "html" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether to minify HTML files. ";
                                                };
                                                "js" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether to minify JavaScript files. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "Which file extensions to minify automatically. ";
                                      };
                                      "bic" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether to enable Browser Integrity Check (BIC). ";
                                      };
                                      "browser_ttl" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "default" = mkUnsetOption {
                                                  type = (types.either referenceType (types.number));
                                                  description = ''The browser TTL (in seconds) if you choose the "override_origin" mode. '';
                                                };
                                                "mode" = mkOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = ''
                                                    The browser TTL mode.
                                                    Available values: "respect_origin", "bypass_by_default", "override_origin", "bypass". '';
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "How long client browsers should cache the response. Cloudflare cache purge will not purge content cached on client browsers, so high browser TTLs may lead to stale content. ";
                                      };
                                      "cache" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether the request's response from the origin is eligible for caching. Caching itself will still depend on the cache control header and your other caching configurations. ";
                                      };
                                      "cache_key" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "cache_by_device_type" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether to separate cached content based on the visitor's device type. ";
                                                };
                                                "cache_deception_armor" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether to protect from web cache deception attacks, while allowing static assets to be cached. ";
                                                };
                                                "custom_key" = mkUnsetOption {
                                                  type = (
                                                    types.either referenceType (
                                                      types.submodule {
                                                        options = {
                                                          "cookie" = mkUnsetOption {
                                                            type = (
                                                              types.either referenceType (
                                                                types.submodule {
                                                                  options = {
                                                                    "check_presence" = mkUnsetOption {
                                                                      type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                                                      description = "A list of cookies to check for the presence of. The presence of these cookies is included in the cache key. ";
                                                                    };
                                                                    "include" = mkUnsetOption {
                                                                      type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                                                      description = "A list of cookies to include in the cache key. ";
                                                                    };

                                                                  };
                                                                }
                                                              )
                                                            );
                                                            description = "Which cookies to include in the cache key. ";
                                                          };
                                                          "header" = mkUnsetOption {
                                                            type = (
                                                              types.either referenceType (
                                                                types.submodule {
                                                                  options = {
                                                                    "check_presence" = mkUnsetOption {
                                                                      type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                                                      description = "A list of headers to check for the presence of. The presence of these headers is included in the cache key. ";
                                                                    };
                                                                    "contains" = mkUnsetOption {
                                                                      type = (
                                                                        types.either referenceType (
                                                                          types.attrsOf (types.either referenceType (types.listOf (types.either referenceType (types.str))))
                                                                        )
                                                                      );
                                                                      description = "A mapping of header names to a list of values. If a header is present in the request and contains any of the values provided, its value is included in the cache key. ";
                                                                    };
                                                                    "exclude_origin" = mkUnsetOption {
                                                                      type = (types.either referenceType (types.bool));
                                                                      description = "Whether to exclude the origin header in the cache key. ";
                                                                    };
                                                                    "include" = mkUnsetOption {
                                                                      type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                                                      description = "A list of headers to include in the cache key. ";
                                                                    };

                                                                  };
                                                                }
                                                              )
                                                            );
                                                            description = "Which headers to include in the cache key. ";
                                                          };
                                                          "host" = mkUnsetOption {
                                                            type = (
                                                              types.either referenceType (
                                                                types.submodule {
                                                                  options = {
                                                                    "resolved" = mkUnsetOption {
                                                                      type = (types.either referenceType (types.bool));
                                                                      description = "Whether to use the resolved host in the cache key. ";
                                                                    };

                                                                  };
                                                                }
                                                              )
                                                            );
                                                            description = "How to use the host in the cache key. ";
                                                          };
                                                          "query_string" = mkUnsetOption {
                                                            type = (
                                                              types.either referenceType (
                                                                types.submodule {
                                                                  options = {
                                                                    "exclude" = mkUnsetOption {
                                                                      type = (
                                                                        types.either referenceType (
                                                                          types.submodule {
                                                                            options = {
                                                                              "all" = mkUnsetOption {
                                                                                type = (types.either referenceType (types.bool));
                                                                                description = "Whether to exclude all query string parameters from the cache key. ";
                                                                              };
                                                                              "list" = mkUnsetOption {
                                                                                type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                                                                description = "A list of query string parameters to exclude from the cache key. ";
                                                                              };

                                                                            };
                                                                          }
                                                                        )
                                                                      );
                                                                      description = "Which query string parameters to exclude from the cache key. ";
                                                                    };
                                                                    "include" = mkUnsetOption {
                                                                      type = (
                                                                        types.either referenceType (
                                                                          types.submodule {
                                                                            options = {
                                                                              "all" = mkUnsetOption {
                                                                                type = (types.either referenceType (types.bool));
                                                                                description = "Whether to include all query string parameters in the cache key. ";
                                                                              };
                                                                              "list" = mkUnsetOption {
                                                                                type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                                                                description = "A list of query string parameters to include in the cache key. ";
                                                                              };

                                                                            };
                                                                          }
                                                                        )
                                                                      );
                                                                      description = "Which query string parameters to include in the cache key. ";
                                                                    };

                                                                  };
                                                                }
                                                              )
                                                            );
                                                            description = "Which query string parameters to include in or exclude from the cache key. ";
                                                          };
                                                          "user" = mkUnsetOption {
                                                            type = (
                                                              types.either referenceType (
                                                                types.submodule {
                                                                  options = {
                                                                    "device_type" = mkUnsetOption {
                                                                      type = (types.either referenceType (types.bool));
                                                                      description = "Whether to use the user agent's device type in the cache key. ";
                                                                    };
                                                                    "geo" = mkUnsetOption {
                                                                      type = (types.either referenceType (types.bool));
                                                                      description = "Whether to use the user agents's country in the cache key. ";
                                                                    };
                                                                    "lang" = mkUnsetOption {
                                                                      type = (types.either referenceType (types.bool));
                                                                      description = "Whether to use the user agent's language in the cache key. ";
                                                                    };

                                                                  };
                                                                }
                                                              )
                                                            );
                                                            description = "How to use characteristics of the request user agent in the cache key. ";
                                                          };

                                                        };
                                                      }
                                                    )
                                                  );
                                                  description = "Which components of the request are included or excluded from the cache key. ";
                                                };
                                                "ignore_query_strings_order" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether to treat requests with the same query parameters the same, regardless of the order those query parameters are in. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "Which components of the request are included in or excluded from the cache key Cloudflare uses to store the response in cache. ";
                                      };
                                      "cache_reserve" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "eligible" = mkOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether Cache Reserve is enabled. If this is true and a request meets eligibility criteria, Cloudflare will write the resource to Cache Reserve. ";
                                                };
                                                "minimum_file_size" = mkUnsetOption {
                                                  type = (types.either referenceType (types.number));
                                                  description = "The minimum file size eligible for storage in Cache Reserve. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "Settings to determine whether the request's response from origin is eligible for Cache Reserve (requires a Cache Reserve add-on plan). ";
                                      };
                                      "content" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The response content. ";
                                      };
                                      "content_converter" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether to enable content conversion (e.g., HTML to Markdown). ";
                                      };
                                      "content_type" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = ''
                                          The content type header to set with the error response.
                                          Available values: "application/json", "text/html", "text/plain", "text/xml". '';
                                      };
                                      "cookie_fields" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.listOf (
                                              types.submodule {
                                                options = {
                                                  "name" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The name of the cookie. ";
                                                  };

                                                };
                                              }
                                            )
                                          )
                                        );
                                        description = "The cookie fields to log. ";
                                      };
                                      "disable_apps" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether to disable Cloudflare Apps. ";
                                      };
                                      "disable_rum" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether to disable Real User Monitoring (RUM). ";
                                      };
                                      "disable_zaraz" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether to disable Zaraz. ";
                                      };
                                      "edge_ttl" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "default" = mkUnsetOption {
                                                  type = (types.either referenceType (types.number));
                                                  description = ''The edge TTL (in seconds) if you choose the "override_origin" mode. '';
                                                };
                                                "mode" = mkOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = ''
                                                    The edge TTL mode.
                                                    Available values: "respect_origin", "bypass_by_default", "override_origin". '';
                                                };
                                                "status_code_ttl" = mkUnsetOption {
                                                  type = (
                                                    types.either referenceType (
                                                      types.listOf (
                                                        types.submodule {
                                                          options = {
                                                            "status_code" = mkUnsetOption {
                                                              type = (types.either referenceType (types.number));
                                                              description = "A single status code to apply the TTL to. ";
                                                            };
                                                            "status_code_range" = mkUnsetOption {
                                                              type = (
                                                                types.either referenceType (
                                                                  types.submodule {
                                                                    options = {
                                                                      "from" = mkUnsetOption {
                                                                        type = (types.either referenceType (types.number));
                                                                        description = "The lower bound of the range. ";
                                                                      };
                                                                      "to" = mkUnsetOption {
                                                                        type = (types.either referenceType (types.number));
                                                                        description = "The upper bound of the range. ";
                                                                      };

                                                                    };
                                                                  }
                                                                )
                                                              );
                                                              description = "A range of status codes to apply the TTL to. ";
                                                            };
                                                            "value" = mkOption {
                                                              type = (types.either referenceType (types.number));
                                                              description = ''The time to cache the response for (in seconds). A value of 0 is equivalent to setting the cache control header with the value "no-cache". A value of -1 is equivalent to setting the cache control header with the value of "no-store". '';
                                                            };

                                                          };
                                                        }
                                                      )
                                                    )
                                                  );
                                                  description = "A list of TTLs to apply to specific status codes or status code ranges. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "How long the Cloudflare edge network should cache the response. ";
                                      };
                                      "email_obfuscation" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether to enable Email Obfuscation. ";
                                      };
                                      "expression" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = "An expression to generate cache tags for set_cache_tags action. ";
                                      };
                                      "fonts" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether to enable Cloudflare Fonts. ";
                                      };
                                      "from_list" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "key" = mkOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = "An expression that evaluates to the list lookup key. ";
                                                };
                                                "name" = mkOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = "The name of the list to match against. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "A redirect based on a bulk list lookup. ";
                                      };
                                      "from_value" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "preserve_query_string" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether to keep the query string of the original request. ";
                                                };
                                                "status_code" = mkUnsetOption {
                                                  type = (types.either referenceType (types.number));
                                                  description = "The status code to use for the redirect. ";
                                                };
                                                "target_url" = mkOption {
                                                  type = (
                                                    types.either referenceType (
                                                      types.submodule {
                                                        options = {
                                                          "expression" = mkUnsetOption {
                                                            type = (types.either referenceType (types.str));
                                                            description = "An expression that evaluates to a URL to redirect the request to. ";
                                                          };
                                                          "value" = mkUnsetOption {
                                                            type = (types.either referenceType (types.str));
                                                            description = "A URL to redirect the request to. ";
                                                          };

                                                        };
                                                      }
                                                    )
                                                  );
                                                  description = "A URL to redirect the request to. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "A redirect based on the request properties. ";
                                      };
                                      "headers" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.attrsOf (
                                              types.submodule {
                                                options = {
                                                  "expression" = mkUnsetOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "An expression that evaluates to a value for the header. ";
                                                  };
                                                  "operation" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = ''
                                                      The operation to perform on the header.
                                                      Available values: "add", "set", "remove". '';
                                                  };
                                                  "value" = mkUnsetOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "A static value for the header. ";
                                                  };

                                                };
                                              }
                                            )
                                          )
                                        );
                                        description = "A map of headers to rewrite. ";
                                      };
                                      "host_header" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = "A value to rewrite the HTTP host header to. ";
                                      };
                                      "hotlink_protection" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether to enable Hotlink Protection. ";
                                      };
                                      "id" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of the ruleset to execute. ";
                                      };
                                      "immutable" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "cloudflare_only" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether to apply the directive only to Cloudflare's cache. ";
                                                };
                                                "operation" = mkOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = ''
                                                    The operation to perform.
                                                    Available values: "set", "remove". '';
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "Set the immutable cache control directive. ";
                                      };
                                      "increment" = mkUnsetOption {
                                        type = (types.either referenceType (types.number));
                                        description = "A delta to change the score by, which can be either positive or negative. ";
                                      };
                                      "matched_data" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "public_key" = mkOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = "The public key to encrypt matched data logs with. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "The configuration to use for matched data logging. ";
                                      };
                                      "max_age" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "cloudflare_only" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether to apply the directive only to Cloudflare's cache. ";
                                                };
                                                "operation" = mkOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = ''
                                                    The operation to perform.
                                                    Available values: "set", "remove". '';
                                                };
                                                "value" = mkUnsetOption {
                                                  type = (types.either referenceType (types.number));
                                                  description = "The value for the directive in seconds. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "Set the max-age cache control directive. ";
                                      };
                                      "mirage" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether to enable Mirage. ";
                                      };
                                      "must_revalidate" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "cloudflare_only" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether to apply the directive only to Cloudflare's cache. ";
                                                };
                                                "operation" = mkOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = ''
                                                    The operation to perform.
                                                    Available values: "set", "remove". '';
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "Set the must-revalidate cache control directive. ";
                                      };
                                      "must_understand" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "cloudflare_only" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether to apply the directive only to Cloudflare's cache. ";
                                                };
                                                "operation" = mkOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = ''
                                                    The operation to perform.
                                                    Available values: "set", "remove". '';
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "Set the must-understand cache control directive. ";
                                      };
                                      "no_cache" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "cloudflare_only" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether to apply the directive only to Cloudflare's cache. ";
                                                };
                                                "operation" = mkOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = ''
                                                    The operation to perform.
                                                    Available values: "set", "remove". '';
                                                };
                                                "qualifiers" = mkUnsetOption {
                                                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                                  description = "The qualifiers for the directive. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "Set the no-cache cache control directive. ";
                                      };
                                      "no_store" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "cloudflare_only" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether to apply the directive only to Cloudflare's cache. ";
                                                };
                                                "operation" = mkOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = ''
                                                    The operation to perform.
                                                    Available values: "set", "remove". '';
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "Set the no-store cache control directive. ";
                                      };
                                      "no_transform" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "cloudflare_only" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether to apply the directive only to Cloudflare's cache. ";
                                                };
                                                "operation" = mkOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = ''
                                                    The operation to perform.
                                                    Available values: "set", "remove". '';
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "Set the no-transform cache control directive. ";
                                      };
                                      "operation" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = ''
                                          The operation to perform for set_cache_tags action.
                                          Available values: "set", "add", "remove". '';
                                      };
                                      "opportunistic_encryption" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether to enable Opportunistic Encryption. ";
                                      };
                                      "origin" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "host" = mkUnsetOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = "A resolved host to route to. ";
                                                };
                                                "port" = mkUnsetOption {
                                                  type = (types.either referenceType (types.number));
                                                  description = "A destination port to route to. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "An origin to route to. ";
                                      };
                                      "origin_cache_control" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether Cloudflare will aim to strictly adhere to RFC 7234. ";
                                      };
                                      "origin_error_page_passthru" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether to generate Cloudflare error pages for issues from the origin server. ";
                                      };
                                      "origin_range_requests" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "mode" = mkOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = ''
                                                    Whether to use range requests. `default` is the behaviour the zone gets without this rule.
                                                    Available values: "on", "off", "default". '';
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "Controls whether Cloudflare fetches a large asset from the origin as a series of range requests instead of one whole-body request. ";
                                      };
                                      "overrides" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "action" = mkUnsetOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = "An action to override all rules with. This option has lower precedence than rule and category overrides. ";
                                                };
                                                "categories" = mkUnsetOption {
                                                  type = (
                                                    types.either referenceType (
                                                      types.listOf (
                                                        types.submodule {
                                                          options = {
                                                            "action" = mkUnsetOption {
                                                              type = (types.either referenceType (types.str));
                                                              description = "The action to override rules in the category with. ";
                                                            };
                                                            "category" = mkOption {
                                                              type = (types.either referenceType (types.str));
                                                              description = "The name of the category to override. ";
                                                            };
                                                            "enabled" = mkUnsetOption {
                                                              type = (types.either referenceType (types.bool));
                                                              description = "Whether to enable execution of rules in the category. ";
                                                            };
                                                            "sensitivity_level" = mkUnsetOption {
                                                              type = (types.either referenceType (types.str));
                                                              description = ''
                                                                The sensitivity level to use for rules in the category. This option is only applicable for DDoS phases.
                                                                Available values: "default", "medium", "low", "eoff". '';
                                                            };

                                                          };
                                                        }
                                                      )
                                                    )
                                                  );
                                                  description = "A list of category-level overrides. This option has the second-highest precedence after rule-level overrides. ";
                                                };
                                                "enabled" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether to enable execution of all rules. This option has lower precedence than rule and category overrides. ";
                                                };
                                                "rules" = mkUnsetOption {
                                                  type = (
                                                    types.either referenceType (
                                                      types.listOf (
                                                        types.submodule {
                                                          options = {
                                                            "action" = mkUnsetOption {
                                                              type = (types.either referenceType (types.str));
                                                              description = "The action to override the rule with. ";
                                                            };
                                                            "enabled" = mkUnsetOption {
                                                              type = (types.either referenceType (types.bool));
                                                              description = "Whether to enable execution of the rule. ";
                                                            };
                                                            "id" = mkOption {
                                                              type = (types.either referenceType (types.str));
                                                              description = "The ID of the rule to override. ";
                                                            };
                                                            "score_threshold" = mkUnsetOption {
                                                              type = (types.either referenceType (types.number));
                                                              description = "The score threshold to use for the rule. ";
                                                            };
                                                            "sensitivity_level" = mkUnsetOption {
                                                              type = (types.either referenceType (types.str));
                                                              description = ''
                                                                The sensitivity level to use for the rule. This option is only applicable for DDoS phases.
                                                                Available values: "default", "medium", "low", "eoff". '';
                                                            };

                                                          };
                                                        }
                                                      )
                                                    )
                                                  );
                                                  description = "A list of rule-level overrides. This option has the highest precedence. ";
                                                };
                                                "sensitivity_level" = mkUnsetOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = ''
                                                    A sensitivity level to set for all rules. This option has lower precedence than rule and category overrides and is only applicable for DDoS phases.
                                                    Available values: "default", "medium", "low", "eoff". '';
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "A set of overrides to apply to the target ruleset. ";
                                      };
                                      "phases" = mkUnsetOption {
                                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                        description = ''
                                          A list of phases to skip the execution of. This option is incompatible with the rulesets option.
                                          Available values: "ddos_l4", "ddos_l7", "http_config_settings", "http_custom_errors", "http_log_custom_fields", "http_ratelimit", "http_request_cache_settings", "http_request_dynamic_redirect", "http_request_firewall_custom", "http_request_firewall_managed", "http_request_late_transform", "http_request_origin", "http_request_redirect", "http_request_sanitize", "http_request_sbfm", "http_request_transform", "http_response_cache_settings", "http_response_compression", "http_response_firewall_managed", "http_response_headers_transform", "magic_transit", "magic_transit_ids_managed", "magic_transit_managed", "magic_transit_ratelimit". '';
                                      };
                                      "polish" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = ''
                                          The Polish level to configure.
                                          Available values: "off", "lossless", "lossy", "webp". '';
                                      };
                                      "private" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "cloudflare_only" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether to apply the directive only to Cloudflare's cache. ";
                                                };
                                                "operation" = mkOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = ''
                                                    The operation to perform.
                                                    Available values: "set", "remove". '';
                                                };
                                                "qualifiers" = mkUnsetOption {
                                                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                                  description = "The qualifiers for the directive. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "Set the private cache control directive. ";
                                      };
                                      "products" = mkUnsetOption {
                                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                        description = ''
                                          A list of legacy security products to skip the execution of.
                                          Available values: "bic", "hot", "rateLimit", "securityLevel", "uaBlock", "waf", "zoneLockdown". '';
                                      };
                                      "proxy_revalidate" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "cloudflare_only" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether to apply the directive only to Cloudflare's cache. ";
                                                };
                                                "operation" = mkOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = ''
                                                    The operation to perform.
                                                    Available values: "set", "remove". '';
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "Set the proxy-revalidate cache control directive. ";
                                      };
                                      "public" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "cloudflare_only" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether to apply the directive only to Cloudflare's cache. ";
                                                };
                                                "operation" = mkOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = ''
                                                    The operation to perform.
                                                    Available values: "set", "remove". '';
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "Set the public cache control directive. ";
                                      };
                                      "raw_response_fields" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.listOf (
                                              types.submodule {
                                                options = {
                                                  "name" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The name of the response header. ";
                                                  };
                                                  "preserve_duplicates" = mkUnsetOption {
                                                    type = (types.either referenceType (types.bool));
                                                    description = "Whether to log duplicate values of the same header. ";
                                                  };

                                                };
                                              }
                                            )
                                          )
                                        );
                                        description = "The raw response fields to log. ";
                                      };
                                      "read_timeout" = mkUnsetOption {
                                        type = (types.either referenceType (types.number));
                                        description = "A timeout value between two successive read operations to use for your origin server. Historically, the timeout value between two read options from Cloudflare to an origin server is 100 seconds. If you are attempting to reduce HTTP 524 errors because of timeouts from an origin server, try increasing this timeout value. ";
                                      };
                                      "redirects_for_ai_training" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether to redirect verified AI training crawlers to canonical URLs. ";
                                      };
                                      "request_body_buffering" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = ''
                                          The request body buffering mode to configure.
                                          Available values: "none", "standard", "full". '';
                                      };
                                      "request_fields" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.listOf (
                                              types.submodule {
                                                options = {
                                                  "name" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The name of the header. ";
                                                  };

                                                };
                                              }
                                            )
                                          )
                                        );
                                        description = "The raw request fields to log. ";
                                      };
                                      "respect_strong_etags" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether Cloudflare should respect strong ETag (entity tag) headers. If false, Cloudflare converts strong ETag headers to weak ETag headers. ";
                                      };
                                      "response" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "content" = mkOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = "The content to return. ";
                                                };
                                                "content_type" = mkOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = "The type of the content to return. ";
                                                };
                                                "status_code" = mkOption {
                                                  type = (types.either referenceType (types.number));
                                                  description = "The status code to return. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "The response to show when the block is applied. ";
                                      };
                                      "response_body_buffering" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = ''
                                          The response body buffering mode to configure.
                                          Available values: "none", "standard". '';
                                      };
                                      "response_fields" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.listOf (
                                              types.submodule {
                                                options = {
                                                  "name" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The name of the response header. ";
                                                  };
                                                  "preserve_duplicates" = mkUnsetOption {
                                                    type = (types.either referenceType (types.bool));
                                                    description = "Whether to log duplicate values of the same header. ";
                                                  };

                                                };
                                              }
                                            )
                                          )
                                        );
                                        description = "The transformed response fields to log. ";
                                      };
                                      "rocket_loader" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether to enable Rocket Loader. ";
                                      };
                                      "rules" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.attrsOf (types.either referenceType (types.listOf (types.either referenceType (types.str))))
                                          )
                                        );
                                        description = "A mapping of ruleset IDs to a list of rule IDs in that ruleset to skip the execution of. This option is incompatible with the ruleset option. ";
                                      };
                                      "ruleset" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = ''
                                          A ruleset to skip the execution of. This option is incompatible with the rulesets option.
                                          Available values: "current". '';
                                      };
                                      "rulesets" = mkUnsetOption {
                                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                        description = "A list of ruleset IDs to skip the execution of. This option is incompatible with the ruleset and phases options. ";
                                      };
                                      "s_maxage" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "cloudflare_only" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether to apply the directive only to Cloudflare's cache. ";
                                                };
                                                "operation" = mkOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = ''
                                                    The operation to perform.
                                                    Available values: "set", "remove". '';
                                                };
                                                "value" = mkUnsetOption {
                                                  type = (types.either referenceType (types.number));
                                                  description = "The value for the directive in seconds. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "Set the s-maxage cache control directive. ";
                                      };
                                      "security_level" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = ''
                                          The Security Level to configure.
                                          Available values: "off", "essentially_off", "low", "medium", "high", "under_attack". '';
                                      };
                                      "serve_stale" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "disable_stale_while_updating" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether Cloudflare should disable serving stale content while getting the latest content from the origin. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "When to serve stale content from cache. ";
                                      };
                                      "server_side_excludes" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether to enable Server-Side Excludes. ";
                                      };
                                      "sni" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "value" = mkOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = "A value to override the SNI to. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "A Server Name Indication (SNI) override. ";
                                      };
                                      "ssl" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = ''
                                          The SSL level to configure.
                                          Available values: "off", "flexible", "full", "strict", "origin_pull". '';
                                      };
                                      "stale_if_error" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "cloudflare_only" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether to apply the directive only to Cloudflare's cache. ";
                                                };
                                                "operation" = mkOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = ''
                                                    The operation to perform.
                                                    Available values: "set", "remove". '';
                                                };
                                                "value" = mkUnsetOption {
                                                  type = (types.either referenceType (types.number));
                                                  description = "The value for the directive in seconds. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "Set the stale-if-error cache control directive. ";
                                      };
                                      "stale_while_revalidate" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "cloudflare_only" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether to apply the directive only to Cloudflare's cache. ";
                                                };
                                                "operation" = mkOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = ''
                                                    The operation to perform.
                                                    Available values: "set", "remove". '';
                                                };
                                                "value" = mkUnsetOption {
                                                  type = (types.either referenceType (types.number));
                                                  description = "The value for the directive in seconds. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "Set the stale-while-revalidate cache control directive. ";
                                      };
                                      "status_code" = mkUnsetOption {
                                        type = (types.either referenceType (types.number));
                                        description = "The status code to use for the error. ";
                                      };
                                      "strip_etags" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether to strip the ETag header from the response. ";
                                      };
                                      "strip_last_modified" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether to strip the Last-Modified header from the response. ";
                                      };
                                      "strip_set_cookie" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether to strip the Set-Cookie header from the response. ";
                                      };
                                      "sxg" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether to enable Signed Exchanges (SXG). ";
                                      };
                                      "transformed_request_fields" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.listOf (
                                              types.submodule {
                                                options = {
                                                  "name" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The name of the header. ";
                                                  };

                                                };
                                              }
                                            )
                                          )
                                        );
                                        description = "The transformed request fields to log. ";
                                      };
                                      "uri" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "path" = mkUnsetOption {
                                                  type = (
                                                    types.either referenceType (
                                                      types.submodule {
                                                        options = {
                                                          "expression" = mkUnsetOption {
                                                            type = (types.either referenceType (types.str));
                                                            description = "An expression that evaluates to a value to rewrite the URI path to. ";
                                                          };
                                                          "value" = mkUnsetOption {
                                                            type = (types.either referenceType (types.str));
                                                            description = "A value to rewrite the URI path to. ";
                                                          };

                                                        };
                                                      }
                                                    )
                                                  );
                                                  description = "A URI path rewrite. ";
                                                };
                                                "query" = mkUnsetOption {
                                                  type = (
                                                    types.either referenceType (
                                                      types.submodule {
                                                        options = {
                                                          "expression" = mkUnsetOption {
                                                            type = (types.either referenceType (types.str));
                                                            description = "An expression that evaluates to a value to rewrite the URI query to. ";
                                                          };
                                                          "value" = mkUnsetOption {
                                                            type = (types.either referenceType (types.str));
                                                            description = "A value to rewrite the URI query to. ";
                                                          };

                                                        };
                                                      }
                                                    )
                                                  );
                                                  description = "A URI query rewrite. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "A URI rewrite. ";
                                      };
                                      "values" = mkUnsetOption {
                                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                        description = "The cache tag values for set_cache_tags action. ";
                                      };
                                      "vary" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "default" = mkOption {
                                                  type = (
                                                    types.either referenceType (
                                                      types.submodule {
                                                        options = {
                                                          "action" = mkOption {
                                                            type = (types.either referenceType (types.str));
                                                            description = ''
                                                              How the header value is treated when building the cache key.
                                                              Available values: "bypass", "passthrough", "normalize". '';
                                                          };

                                                        };
                                                      }
                                                    )
                                                  );
                                                  description = "Controls how response Vary headers without a per-header override contribute to the cache key. ";
                                                };
                                                "headers" = mkUnsetOption {
                                                  type = (
                                                    types.either referenceType (
                                                      types.attrsOf (
                                                        types.submodule {
                                                          options = {
                                                            "action" = mkOption {
                                                              type = (types.either referenceType (types.str));
                                                              description = ''
                                                                How the header value is treated when building the cache key.
                                                                Available values: "bypass", "passthrough", "normalize". '';
                                                            };
                                                            "languages" = mkUnsetOption {
                                                              type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                                              description = "The set of languages to normalize against. Only valid for the `accept-language` header. ";
                                                            };
                                                            "media_types" = mkUnsetOption {
                                                              type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                                              description = "The set of media types to normalize against. Only valid for the `accept` header. ";
                                                            };

                                                          };
                                                        }
                                                      )
                                                    )
                                                  );
                                                  description = "A mapping of lowercase request header names to their vary configuration. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "Controls how cached responses vary based on request headers. `default` is required and applies to any Vary response header that does not have a per-header override. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "The parameters configuring the rule's action. ";
                            };
                            "description" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = "An informative description of the rule. ";
                            };
                            "enabled" = mkUnsetOption {
                              type = (types.either referenceType (types.bool));
                              description = "Whether the rule should be executed. ";
                            };
                            "exposed_credential_check" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "password_expression" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "An expression that selects the password used in the credentials check. ";
                                      };
                                      "username_expression" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "An expression that selects the user ID used in the credentials check. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "Configuration for exposed credential checking. ";
                            };
                            "expression" = mkOption {
                              type = (types.either referenceType (types.str));
                              description = "The expression defining which traffic will match the rule. ";
                            };
                            "id" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = "The unique ID of the rule. ";
                            };
                            "logging" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "enabled" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether to generate a log when the rule matches. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "An object configuring the rule's logging behavior. ";
                            };
                            "ratelimit" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "characteristics" = mkOption {
                                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                        description = "Characteristics of the request on which the rate limit counter will be incremented. ";
                                      };
                                      "counting_expression" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = "An expression that defines when the rate limit counter should be incremented. It defaults to the same as the rule's expression. ";
                                      };
                                      "mitigation_timeout" = mkUnsetOption {
                                        type = (types.either referenceType (types.number));
                                        description = "Period of time in seconds after which the action will be disabled following its first execution. ";
                                      };
                                      "period" = mkOption {
                                        type = (types.either referenceType (types.number));
                                        description = "Period in seconds over which the counter is being incremented. ";
                                      };
                                      "requests_per_period" = mkUnsetOption {
                                        type = (types.either referenceType (types.number));
                                        description = "The threshold of requests per period after which the action will be executed for the first time. ";
                                      };
                                      "requests_to_origin" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether counting is only performed when an origin is reached. ";
                                      };
                                      "score_per_period" = mkUnsetOption {
                                        type = (types.either referenceType (types.number));
                                        description = "The score threshold per period for which the action will be executed the first time. ";
                                      };
                                      "score_response_header_name" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = "A response header name provided by the origin, which contains the score to increment rate limit counter with. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "An object configuring the rule's rate limit behavior. ";
                            };
                            "ref" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = "The reference of the rule (the rule's ID by default). ";
                            };

                          };
                        }
                      )
                    )
                  );
                  description = "The list of rules in the ruleset. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "version" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The version of the ruleset. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "zone_id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The unique ID of the zone. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "";
      };
      "cloudflare_zero_trust_access_application" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated cloudflare provider";
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

                "account_id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The Account ID to use for this endpoint. Mutually exclusive with the Zone ID. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "allow_authenticate_via_warp" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "When set to true, users can authenticate to this application using their WARP session.  When set to false this application will always require direct IdP authentication. This setting always overrides the organization setting for WARP authentication. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "allow_iframe" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enables loading application content in an iFrame. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "allowed_idps" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "The identity providers your users can select when connecting to this application. Defaults to all IdPs configured in your account. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "app_launcher_logo_url" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The image URL of the logo shown in the App Launcher header. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "app_launcher_visible" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Displays the application in the App Launcher. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "aud" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Audience tag. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "auto_redirect_to_identity" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "When set to `true`, users skip the identity provider selection step during login. You must specify only one identity provider in allowed_idps. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "bg_color" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The background color of the App Launcher page. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "cors_headers" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.submodule {
                        options = {
                          "allow_all_headers" = mkUnsetOption {
                            type = (types.either referenceType (types.bool));
                            description = "Allows all HTTP request headers. ";
                          };
                          "allow_all_methods" = mkUnsetOption {
                            type = (types.either referenceType (types.bool));
                            description = "Allows all HTTP request methods. ";
                          };
                          "allow_all_origins" = mkUnsetOption {
                            type = (types.either referenceType (types.bool));
                            description = "Allows all origins. ";
                          };
                          "allow_credentials" = mkUnsetOption {
                            type = (types.either referenceType (types.bool));
                            description = "When set to `true`, includes credentials (cookies, authorization headers, or TLS client certificates) with requests. ";
                          };
                          "allowed_headers" = mkUnsetOption {
                            type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                            description = "Allowed HTTP request headers. ";
                          };
                          "allowed_methods" = mkUnsetOption {
                            type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                            description = "Allowed HTTP request methods. ";
                          };
                          "allowed_origins" = mkUnsetOption {
                            type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                            description = "Allowed origins. ";
                          };
                          "max_age" = mkUnsetOption {
                            type = (types.either referenceType (types.number));
                            description = "The maximum number of seconds the results of a preflight request can be cached. ";
                          };

                        };
                      }
                    )
                  );
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "custom_deny_message" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The custom error message shown to a user when they are denied access to the application. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "custom_deny_url" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The custom URL a user is redirected to when they are denied access to the application when failing identity-based rules. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "custom_non_identity_deny_url" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The custom URL a user is redirected to when they are denied access to the application when failing non-identity rules. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "custom_pages" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "The custom pages that will be displayed when applicable for this application ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "destinations" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.listOf (
                        types.submodule {
                          options = {
                            "cidr" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = "The CIDR range of the destination. Single IPs will be computed as /32. ";
                            };
                            "hostname" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = "The hostname of the destination. Matches a valid SNI served by an HTTPS origin. ";
                            };
                            "l4_protocol" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = ''
                                The L4 protocol of the destination. When omitted, both UDP and TCP traffic will match.
                                Available values: "tcp", "udp". '';
                            };
                            "mcp_server_id" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = "A MCP server id configured in ai-controls. Access will secure the MCP server if accessed through a MCP portal. ";
                            };
                            "port_range" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = "The port range of the destination. Can be a single port or a range of ports. When omitted, all ports will match. ";
                            };
                            "type" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = ''Available values: "public", "private", "via_mcp_server_portal", "worker", "preview_worker", "all_workers", "all_preview_workers". '';
                            };
                            "uri" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = "The URI of the destination. Public destinations' URIs can include a domain and path with [wildcards](https://developers.cloudflare.com/cloudflare-one/policies/access/app-paths/). ";
                            };
                            "vnet_id" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = "The VNET ID to match the destination. When omitted, all VNETs will match. ";
                            };
                            "worker_id" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = "The ID of the Cloudflare Worker to protect with Access. Required when type is `worker` or `preview_worker`. ";
                            };

                          };
                        }
                      )
                    )
                  );
                  description = "List of destinations secured by Access. This supersedes `self_hosted_domains` to allow for more flexibility in defining different types of domains. If `destinations` are provided, then `self_hosted_domains` will be ignored. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "domain" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The primary hostname and path secured by Access. This domain will be displayed if the app is visible in the App Launcher. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "enable_binding_cookie" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enables the binding cookie, which increases security against compromised authorization tokens and CSRF attacks. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "footer_links" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.listOf (
                        types.submodule {
                          options = {
                            "name" = mkOption {
                              type = (types.either referenceType (types.str));
                              description = "The hypertext in the footer link. ";
                            };
                            "url" = mkOption {
                              type = (types.either referenceType (types.str));
                              description = "the hyperlink in the footer link. ";
                            };

                          };
                        }
                      )
                    )
                  );
                  description = "The links in the App Launcher footer. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "header_bg_color" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The background color of the App Launcher header. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "http_only_cookie_attribute" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enables the HttpOnly cookie attribute, which increases security against XSS attacks. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "landing_page_design" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.submodule {
                        options = {
                          "button_color" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "The background color of the log in button on the landing page. ";
                          };
                          "button_text_color" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "The color of the text in the log in button on the landing page. ";
                          };
                          "image_url" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "The URL of the image shown on the landing page. ";
                          };
                          "message" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "The message shown on the landing page. ";
                          };
                          "title" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "The title shown on the landing page. ";
                          };

                        };
                      }
                    )
                  );
                  description = "The design of the App Launcher landing page shown to users when they log in. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "logo_url" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The image URL for the logo shown in the App Launcher dashboard. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mfa_config" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.submodule {
                        options = {
                          "allowed_authenticators" = mkUnsetOption {
                            type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                            description = ''
                              The authenticators allowed for MFA.
                              Available values: "totp", "biometrics", "security_key". '';
                          };
                          "mfa_disabled" = mkUnsetOption {
                            type = (types.either referenceType (types.bool));
                            description = "Whether MFA is disabled for this application. ";
                          };
                          "session_duration" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "How often a user will be forced to re-authenticate with MFA. ";
                          };

                        };
                      }
                    )
                  );
                  description = "Configures multi-factor authentication (MFA) settings for the application. Only valid for self_hosted, ssh, vnc, and rdp application types. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The name of the application. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "oauth_configuration" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.submodule {
                        options = {
                          "dynamic_client_registration" = mkUnsetOption {
                            type = (
                              types.either referenceType (
                                types.submodule {
                                  options = {
                                    "allow_any_on_localhost" = mkUnsetOption {
                                      type = (types.either referenceType (types.bool));
                                      description = "Allows any client with redirect URIs on localhost. ";
                                    };
                                    "allow_any_on_loopback" = mkUnsetOption {
                                      type = (types.either referenceType (types.bool));
                                      description = "Allows any client with redirect URIs on 127.0.0.1. ";
                                    };
                                    "allowed_uris" = mkUnsetOption {
                                      type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                      description = "The URIs that are allowed as redirect URIs for dynamically registered clients. Must use the `https` protocol. Paths may end in `/*` to match all sub-paths. ";
                                    };
                                    "enabled" = mkUnsetOption {
                                      type = (types.either referenceType (types.bool));
                                      description = "Whether dynamic client registration is enabled. ";
                                    };

                                  };
                                }
                              )
                            );
                            description = "Settings for OAuth dynamic client registration. ";
                          };
                          "enabled" = mkUnsetOption {
                            type = (types.either referenceType (types.bool));
                            description = "Whether the OAuth configuration is enabled for this application. When set to `false`, Access will not handle OAuth for this application. Defaults to `true` if omitted. ";
                          };
                          "grant" = mkUnsetOption {
                            type = (
                              types.either referenceType (
                                types.submodule {
                                  options = {
                                    "access_token_lifetime" = mkUnsetOption {
                                      type = (types.either referenceType (types.str));
                                      description = "The lifetime of the access token. Must be in the format `300ms` or `2h45m`. Valid time units are ns, us (or µs), ms, s, m, h. ";
                                    };
                                    "session_duration" = mkUnsetOption {
                                      type = (types.either referenceType (types.str));
                                      description = "The duration of the OAuth session. Must be in the format `300ms` or `2h45m`. Valid time units are ns, us (or µs), ms, s, m, h. ";
                                    };

                                  };
                                }
                              )
                            );
                            description = "Settings for OAuth grant behavior. ";
                          };

                        };
                      }
                    )
                  );
                  description = "Optional configuration for managing an OAuth authorization flow controlled by Access. When set, Access will act as the OAuth authorization server for this application. This feature is currently in beta. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "options_preflight_bypass" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Allows options preflight requests to bypass Access authentication and go directly to the origin. Cannot turn on if cors_headers is set. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "path_cookie_attribute" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enables cookie paths to scope an application's JWT to the application path. If disabled, the JWT will scope to the hostname by default ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "policies" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.listOf (
                        types.submodule {
                          options = {
                            "connection_rules" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "rdp" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "allowed_clipboard_local_to_remote_formats" = mkUnsetOption {
                                                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                                  description = "Clipboard formats allowed when copying from local machine to remote RDP session. ";
                                                };
                                                "allowed_clipboard_remote_to_local_formats" = mkUnsetOption {
                                                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                                  description = "Clipboard formats allowed when copying from remote RDP session to local machine. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "The RDP-specific rules that define clipboard behavior for RDP connections. ";
                                      };
                                      "ssh" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "allow_email_alias" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Enables using Identity Provider email alias as SSH username. ";
                                                };
                                                "usernames" = mkOption {
                                                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                                  description = "Contains the Unix usernames that may be used when connecting over SSH. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "The SSH-specific rules that define how users may connect to the targets secured by your application. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "The rules that define how users may connect to the targets secured by your application. ";
                            };
                            "decision" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = ''
                                The action Access will take if a user matches this policy. Infrastructure application policies can only use the Allow action.
                                Available values: "allow", "deny", "non_identity", "bypass". '';
                            };
                            "exclude" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.listOf (
                                    types.submodule {
                                      options = {
                                        "any_valid_service_token" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = { };
                                              }
                                            )
                                          );
                                          description = "An empty object which matches on all service tokens. ";
                                        };
                                        "auth_context" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "ac_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ACID of an Authentication context. ";
                                                  };
                                                  "id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of an Authentication context. ";
                                                  };
                                                  "identity_provider_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of your Azure identity provider. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "auth_method" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "auth_method" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The type of authentication method https://datatracker.ietf.org/doc/html/rfc8176#section-2. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "azure_ad" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of an Azure group. ";
                                                  };
                                                  "identity_provider_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of your Azure identity provider. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "certificate" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = { };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "common_name" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "common_name" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The common name to match. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "device_posture" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "integration_uid" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of a device posture integration. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "email" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "email" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The email of the user. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "email_domain" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "domain" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The email domain to match. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "email_list" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of a previously created email list. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "everyone" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = { };
                                              }
                                            )
                                          );
                                          description = "An empty object which matches on all users. ";
                                        };
                                        "external_evaluation" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "evaluate_url" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The API endpoint containing your business logic. ";
                                                  };
                                                  "keys_url" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The API endpoint containing the key that Access uses to verify that the response came from your API. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "geo" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "country_code" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The country code that should be matched. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "github_organization" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "identity_provider_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of your Github identity provider. ";
                                                  };
                                                  "name" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The name of the organization. ";
                                                  };
                                                  "team" = mkUnsetOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The name of the team ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "group" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of a previously created Access group. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "gsuite" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "email" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The email of the Google Workspace group. ";
                                                  };
                                                  "identity_provider_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of your Google Workspace identity provider. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "ip" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "ip" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "An IPv4 or IPv6 CIDR block. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "ip_list" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of a previously created IP list. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "linked_app_token" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "app_uid" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of an Access OIDC SaaS application ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "login_method" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of an identity provider. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "oidc" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "claim_name" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The name of the OIDC claim. ";
                                                  };
                                                  "claim_value" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The OIDC claim value to look for. ";
                                                  };
                                                  "identity_provider_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of your OIDC identity provider. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "okta" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "identity_provider_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of your Okta identity provider. ";
                                                  };
                                                  "name" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The name of the Okta group. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "saml" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "attribute_name" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The name of the SAML attribute. ";
                                                  };
                                                  "attribute_value" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The SAML attribute value to look for. ";
                                                  };
                                                  "identity_provider_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of your SAML identity provider. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "service_token" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "token_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of a Service Token. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };

                                      };
                                    }
                                  )
                                )
                              );
                              description = "Rules evaluated with a NOT logical operator. To match the policy, a user cannot meet any of the Exclude rules. ";
                            };
                            "id" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = "The UUID of the policy ";
                            };
                            "include" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.listOf (
                                    types.submodule {
                                      options = {
                                        "any_valid_service_token" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = { };
                                              }
                                            )
                                          );
                                          description = "An empty object which matches on all service tokens. ";
                                        };
                                        "auth_context" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "ac_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ACID of an Authentication context. ";
                                                  };
                                                  "id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of an Authentication context. ";
                                                  };
                                                  "identity_provider_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of your Azure identity provider. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "auth_method" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "auth_method" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The type of authentication method https://datatracker.ietf.org/doc/html/rfc8176#section-2. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "azure_ad" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of an Azure group. ";
                                                  };
                                                  "identity_provider_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of your Azure identity provider. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "certificate" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = { };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "common_name" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "common_name" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The common name to match. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "device_posture" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "integration_uid" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of a device posture integration. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "email" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "email" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The email of the user. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "email_domain" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "domain" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The email domain to match. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "email_list" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of a previously created email list. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "everyone" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = { };
                                              }
                                            )
                                          );
                                          description = "An empty object which matches on all users. ";
                                        };
                                        "external_evaluation" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "evaluate_url" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The API endpoint containing your business logic. ";
                                                  };
                                                  "keys_url" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The API endpoint containing the key that Access uses to verify that the response came from your API. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "geo" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "country_code" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The country code that should be matched. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "github_organization" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "identity_provider_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of your Github identity provider. ";
                                                  };
                                                  "name" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The name of the organization. ";
                                                  };
                                                  "team" = mkUnsetOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The name of the team ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "group" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of a previously created Access group. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "gsuite" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "email" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The email of the Google Workspace group. ";
                                                  };
                                                  "identity_provider_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of your Google Workspace identity provider. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "ip" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "ip" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "An IPv4 or IPv6 CIDR block. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "ip_list" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of a previously created IP list. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "linked_app_token" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "app_uid" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of an Access OIDC SaaS application ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "login_method" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of an identity provider. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "oidc" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "claim_name" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The name of the OIDC claim. ";
                                                  };
                                                  "claim_value" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The OIDC claim value to look for. ";
                                                  };
                                                  "identity_provider_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of your OIDC identity provider. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "okta" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "identity_provider_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of your Okta identity provider. ";
                                                  };
                                                  "name" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The name of the Okta group. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "saml" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "attribute_name" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The name of the SAML attribute. ";
                                                  };
                                                  "attribute_value" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The SAML attribute value to look for. ";
                                                  };
                                                  "identity_provider_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of your SAML identity provider. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "service_token" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "token_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of a Service Token. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };

                                      };
                                    }
                                  )
                                )
                              );
                              description = "Rules evaluated with an OR logical operator. A user needs to meet only one of the Include rules. ";
                            };
                            "mfa_config" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "allowed_authenticators" = mkUnsetOption {
                                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                        description = ''
                                          The authenticators allowed for MFA.
                                          Available values: "totp", "biometrics", "security_key", "ssh_piv_key". '';
                                      };
                                      "mfa_disabled" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether MFA is disabled for this policy. ";
                                      };
                                      "session_duration" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = "How often a user will be forced to re-authenticate with MFA. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "Configures multi-factor authentication (MFA) settings for this policy. For infrastructure applications only `ssh_piv_key` is a supported authenticator; for other application types use `totp`, `biometrics`, or `security_key`. ";
                            };
                            "name" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = "The name of the Access policy. ";
                            };
                            "precedence" = mkUnsetOption {
                              type = (types.either referenceType (types.number));
                              description = "The order of execution for this policy. Must be unique for each policy within an app. ";
                            };
                            "require" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.listOf (
                                    types.submodule {
                                      options = {
                                        "any_valid_service_token" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = { };
                                              }
                                            )
                                          );
                                          description = "An empty object which matches on all service tokens. ";
                                        };
                                        "auth_context" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "ac_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ACID of an Authentication context. ";
                                                  };
                                                  "id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of an Authentication context. ";
                                                  };
                                                  "identity_provider_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of your Azure identity provider. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "auth_method" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "auth_method" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The type of authentication method https://datatracker.ietf.org/doc/html/rfc8176#section-2. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "azure_ad" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of an Azure group. ";
                                                  };
                                                  "identity_provider_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of your Azure identity provider. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "certificate" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = { };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "common_name" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "common_name" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The common name to match. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "device_posture" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "integration_uid" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of a device posture integration. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "email" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "email" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The email of the user. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "email_domain" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "domain" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The email domain to match. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "email_list" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of a previously created email list. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "everyone" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = { };
                                              }
                                            )
                                          );
                                          description = "An empty object which matches on all users. ";
                                        };
                                        "external_evaluation" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "evaluate_url" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The API endpoint containing your business logic. ";
                                                  };
                                                  "keys_url" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The API endpoint containing the key that Access uses to verify that the response came from your API. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "geo" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "country_code" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The country code that should be matched. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "github_organization" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "identity_provider_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of your Github identity provider. ";
                                                  };
                                                  "name" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The name of the organization. ";
                                                  };
                                                  "team" = mkUnsetOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The name of the team ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "group" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of a previously created Access group. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "gsuite" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "email" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The email of the Google Workspace group. ";
                                                  };
                                                  "identity_provider_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of your Google Workspace identity provider. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "ip" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "ip" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "An IPv4 or IPv6 CIDR block. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "ip_list" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of a previously created IP list. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "linked_app_token" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "app_uid" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of an Access OIDC SaaS application ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "login_method" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of an identity provider. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "oidc" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "claim_name" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The name of the OIDC claim. ";
                                                  };
                                                  "claim_value" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The OIDC claim value to look for. ";
                                                  };
                                                  "identity_provider_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of your OIDC identity provider. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "okta" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "identity_provider_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of your Okta identity provider. ";
                                                  };
                                                  "name" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The name of the Okta group. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "saml" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "attribute_name" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The name of the SAML attribute. ";
                                                  };
                                                  "attribute_value" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The SAML attribute value to look for. ";
                                                  };
                                                  "identity_provider_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of your SAML identity provider. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };
                                        "service_token" = mkUnsetOption {
                                          type = (
                                            types.either referenceType (
                                              types.submodule {
                                                options = {
                                                  "token_id" = mkOption {
                                                    type = (types.either referenceType (types.str));
                                                    description = "The ID of a Service Token. ";
                                                  };

                                                };
                                              }
                                            )
                                          );
                                          description = "";
                                        };

                                      };
                                    }
                                  )
                                )
                              );
                              description = "Rules evaluated with an AND logical operator. To match the policy, a user must meet all of the Require rules. ";
                            };

                          };
                        }
                      )
                    )
                  );
                  description = "The policies that Access applies to the application, in ascending order of precedence. Items can reference existing policies or create new policies exclusive to the application. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "read_service_tokens_from_header" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''
                    Allows matching Access Service Tokens passed HTTP in a single header with this name.
                    This works as an alternative to the (CF-Access-Client-Id, CF-Access-Client-Secret) pair of headers.
                    The header value will be interpreted as a json object similar to:
                      {
                        "cf-access-client-id": "88bf3b6d86161464f6509f7219099e57.access.example.com",
                        "cf-access-client-secret": "<example>"
                      } '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "saas_app" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.submodule {
                        options = {
                          "access_token_lifetime" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "The lifetime of the OIDC Access Token after creation. Valid units are m,h. Must be greater than or equal to 1m and less than or equal to 24h. ";
                          };
                          "allow_pkce_without_client_secret" = mkUnsetOption {
                            type = (types.either referenceType (types.bool));
                            description = "If client secret should be required on the token endpoint when authorization_code_with_pkce grant is used. ";
                          };
                          "app_launcher_url" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "The URL where this applications tile redirects users ";
                          };
                          "auth_type" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = ''
                              Optional identifier indicating the authentication protocol used for the saas app. Required for OIDC. Default if unset is "saml"
                              Available values: "saml", "oidc". '';
                          };
                          "client_id" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "The application client id ";
                          };
                          "client_secret" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "The application client secret, only returned on POST request. ";
                          };
                          "consumer_service_url" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "The service provider's endpoint that is responsible for receiving and parsing a SAML assertion. ";
                          };
                          "custom_attributes" = mkUnsetOption {
                            type = (
                              types.either referenceType (
                                types.listOf (
                                  types.submodule {
                                    options = {
                                      "friendly_name" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The SAML FriendlyName of the attribute. ";
                                      };
                                      "name" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The name of the attribute. ";
                                      };
                                      "name_format" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = ''
                                          A globally unique name for an identity or service provider.
                                          Available values: "urn:oasis:names:tc:SAML:2.0:attrname-format:unspecified", "urn:oasis:names:tc:SAML:2.0:attrname-format:basic", "urn:oasis:names:tc:SAML:2.0:attrname-format:uri". '';
                                      };
                                      "required" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "If the attribute is required when building a SAML assertion. ";
                                      };
                                      "source" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "name" = mkUnsetOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = "The name of the IdP attribute. ";
                                                };
                                                "name_by_idp" = mkUnsetOption {
                                                  type = (
                                                    types.either referenceType (
                                                      types.listOf (
                                                        types.submodule {
                                                          options = {
                                                            "idp_id" = mkUnsetOption {
                                                              type = (types.either referenceType (types.str));
                                                              description = "The UID of the IdP. ";
                                                            };
                                                            "source_name" = mkUnsetOption {
                                                              type = (types.either referenceType (types.str));
                                                              description = "The name of the IdP provided attribute. ";
                                                            };

                                                          };
                                                        }
                                                      )
                                                    )
                                                  );
                                                  description = "A mapping from IdP ID to attribute name. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "";
                                      };

                                    };
                                  }
                                )
                              )
                            );
                            description = "";
                          };
                          "custom_claims" = mkUnsetOption {
                            type = (
                              types.either referenceType (
                                types.listOf (
                                  types.submodule {
                                    options = {
                                      "name" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The name of the claim. ";
                                      };
                                      "required" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "If the claim is required when building an OIDC token. ";
                                      };
                                      "scope" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = ''
                                          The scope of the claim.
                                          Available values: "groups", "profile", "email", "openid". '';
                                      };
                                      "source" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "name" = mkUnsetOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = "The name of the IdP claim. ";
                                                };
                                                "name_by_idp" = mkUnsetOption {
                                                  type = (types.either referenceType (types.attrsOf (types.either referenceType (types.str))));
                                                  description = "A mapping from IdP ID to claim name. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "";
                                      };

                                    };
                                  }
                                )
                              )
                            );
                            description = "";
                          };
                          "default_relay_state" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "The URL that the user will be redirected to after a successful login for IDP initiated logins. ";
                          };
                          "grant_types" = mkUnsetOption {
                            type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                            description = "The OIDC flows supported by this application ";
                          };
                          "group_filter_regex" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "A regex to filter Cloudflare groups returned in ID token and userinfo endpoint ";
                          };
                          "hybrid_and_implicit_options" = mkUnsetOption {
                            type = (
                              types.either referenceType (
                                types.submodule {
                                  options = {
                                    "return_access_token_from_authorization_endpoint" = mkUnsetOption {
                                      type = (types.either referenceType (types.bool));
                                      description = "If an Access Token should be returned from the OIDC Authorization endpoint ";
                                    };
                                    "return_id_token_from_authorization_endpoint" = mkUnsetOption {
                                      type = (types.either referenceType (types.bool));
                                      description = "If an ID Token should be returned from the OIDC Authorization endpoint ";
                                    };

                                  };
                                }
                              )
                            );
                            description = "";
                          };
                          "idp_entity_id" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "The unique identifier for your SaaS application. ";
                          };
                          "name_id_format" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = ''
                              The format of the name identifier sent to the SaaS application.
                              Available values: "id", "email". '';
                          };
                          "name_id_transform_jsonata" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "A [JSONata](https://jsonata.org/) expression that transforms an application's user identities into a NameID value for its SAML assertion. This expression should evaluate to a singular string. The output of this expression can override the `name_id_format` setting. ";
                          };
                          "public_key" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "The Access public certificate that will be used to verify your identity. ";
                          };
                          "redirect_uris" = mkUnsetOption {
                            type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                            description = "The permitted URL's for Cloudflare to return Authorization codes and Access/ID tokens ";
                          };
                          "refresh_token_options" = mkUnsetOption {
                            type = (
                              types.either referenceType (
                                types.submodule {
                                  options = {
                                    "lifetime" = mkUnsetOption {
                                      type = (types.either referenceType (types.str));
                                      description = "How long a refresh token will be valid for after creation. Valid units are m,h,d. Must be longer than 1m. ";
                                    };

                                  };
                                }
                              )
                            );
                            description = "";
                          };
                          "saml_attribute_transform_jsonata" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "A [JSONata] (https://jsonata.org/) expression that transforms an application's user identities into attribute assertions in the SAML response. The expression can transform id, email, name, and groups values. It can also transform fields listed in the saml_attributes or oidc_fields of the identity provider used to authenticate. The output of this expression must be a JSON object. ";
                          };
                          "scopes" = mkUnsetOption {
                            type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                            description = ''Define the user information shared with access, "offline_access" scope will be automatically enabled if refresh tokens are enabled '';
                          };
                          "sp_entity_id" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "A globally unique name for an identity or service provider. ";
                          };
                          "sso_endpoint" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "The endpoint where your SaaS application will send login requests. ";
                          };

                        };
                      }
                    )
                  );
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "same_site_cookie_attribute" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Sets the SameSite cookie setting, which provides increased security against CSRF attacks. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "scim_config" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.submodule {
                        options = {
                          "authentication" = mkUnsetOption {
                            type = (
                              types.either referenceType (
                                types.submodule {
                                  options = {
                                    "authorization_url" = mkUnsetOption {
                                      type = (types.either referenceType (types.str));
                                      description = "URL used to generate the auth code used during token generation. ";
                                    };
                                    "client_id" = mkUnsetOption {
                                      type = (types.either referenceType (types.str));
                                      description = "Client ID used to authenticate when generating a token for authenticating with the remote SCIM service. ";
                                    };
                                    "client_secret" = mkUnsetOption {
                                      type = (types.either referenceType (types.str));
                                      description = "Secret used to authenticate when generating a token for authenticating with the remove SCIM service. ";
                                    };
                                    "password" = mkUnsetOption {
                                      type = (types.either referenceType (types.str));
                                      description = "Password used to authenticate with the remote SCIM service. ";
                                    };
                                    "scheme" = mkOption {
                                      type = (types.either referenceType (types.str));
                                      description = ''
                                        The authentication scheme to use when making SCIM requests to this application.
                                        Available values: "httpbasic", "oauthbearertoken", "oauth2", "access_service_token". '';
                                    };
                                    "scopes" = mkUnsetOption {
                                      type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                      description = "The authorization scopes to request when generating the token used to authenticate with the remove SCIM service. ";
                                    };
                                    "token" = mkUnsetOption {
                                      type = (types.either referenceType (types.str));
                                      description = "Token used to authenticate with the remote SCIM service. ";
                                    };
                                    "token_url" = mkUnsetOption {
                                      type = (types.either referenceType (types.str));
                                      description = "URL used to generate the token used to authenticate with the remote SCIM service. ";
                                    };
                                    "user" = mkUnsetOption {
                                      type = (types.either referenceType (types.str));
                                      description = "User name used to authenticate with the remote SCIM service. ";
                                    };

                                  };
                                }
                              )
                            );
                            description = "Attributes for configuring HTTP Basic authentication scheme for SCIM provisioning to an application. ";
                          };
                          "deactivate_on_delete" = mkUnsetOption {
                            type = (types.either referenceType (types.bool));
                            description = "If false, propagates DELETE requests to the target application for SCIM resources. If true, sets 'active' to false on the SCIM resource. Note: Some targets do not support DELETE operations. ";
                          };
                          "enabled" = mkUnsetOption {
                            type = (types.either referenceType (types.bool));
                            description = "Whether SCIM provisioning is turned on for this application. ";
                          };
                          "idp_uid" = mkOption {
                            type = (types.either referenceType (types.str));
                            description = "The UID of the IdP to use as the source for SCIM resources to provision to this application. ";
                          };
                          "mappings" = mkUnsetOption {
                            type = (
                              types.either referenceType (
                                types.listOf (
                                  types.submodule {
                                    options = {
                                      "enabled" = mkUnsetOption {
                                        type = (types.either referenceType (types.bool));
                                        description = "Whether or not this mapping is enabled. ";
                                      };
                                      "filter" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = "A [SCIM filter expression](https://datatracker.ietf.org/doc/html/rfc7644#section-3.4.2.2) that matches resources that should be provisioned to this application. ";
                                      };
                                      "operations" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "create" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether or not this mapping applies to create (POST) operations. ";
                                                };
                                                "delete" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether or not this mapping applies to DELETE operations. ";
                                                };
                                                "update" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Whether or not this mapping applies to update (PATCH/PUT) operations. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "Whether or not this mapping applies to creates, updates, or deletes. ";
                                      };
                                      "schema" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "Which SCIM resource type this mapping applies to. ";
                                      };
                                      "strictness" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = ''
                                          The level of adherence to outbound resource schemas when provisioning to this mapping. ‘Strict’ removes unknown values, while ‘passthrough’ passes unknown values to the target.
                                          Available values: "strict", "passthrough". '';
                                      };
                                      "transform_jsonata" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = "A [JSONata](https://jsonata.org/) expression that transforms the resource before provisioning it in the application. ";
                                      };

                                    };
                                  }
                                )
                              )
                            );
                            description = "A list of mappings to apply to SCIM resources before provisioning them in this application. These can transform or filter the resources to be provisioned. ";
                          };
                          "remote_uri" = mkOption {
                            type = (types.either referenceType (types.str));
                            description = "The base URI for the application's SCIM-compatible API. ";
                          };

                        };
                      }
                    )
                  );
                  description = "Configuration for provisioning to this application via SCIM. This is currently in closed beta. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "self_hosted_domains" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "List of public domains that Access will secure. This field is deprecated in favor of `destinations` and will be supported until **November 21, 2025.** If `destinations` are provided, then `self_hosted_domains` will be ignored. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "service_auth_401_redirect" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Returns a 401 status code when the request is blocked by a Service Auth policy. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "session_duration" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The amount of time that tokens issued for this application will be valid. Must be in the format `300ms` or `2h45m`. Valid time units are: ns, us (or µs), ms, s, m, h. For infrastructure applications, this is the maximum connection duration; connections are terminated once it elapses. Infrastructure durations must use s, m, or h and resolve to whole seconds. Defaults to `24h` for supported application types. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "skip_app_launcher_login_page" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Determines when to skip the App Launcher landing page. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "skip_interstitial" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Enables automatic authentication through cloudflared. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tags" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "The tags you want assigned to an application. Tags are used to filter applications in the App Launcher dashboard. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "target_criteria" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.listOf (
                        types.submodule {
                          options = {
                            "exclude" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "tags" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.attrsOf (types.either referenceType (types.listOf (types.either referenceType (types.str))))
                                          )
                                        );
                                        description = "Map of target tag keys to values. Values within a key are OR'd. ";
                                      };
                                      "target_attributes" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "hostname" = mkOption {
                                                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                                  description = "";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "Hostname selector map. Only the hostname key is supported. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "Target is excluded when any selector in this rule matches. ";
                            };
                            "include" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "tags" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.attrsOf (types.either referenceType (types.listOf (types.either referenceType (types.str))))
                                          )
                                        );
                                        description = "Map of target tag keys to values. Values within a key are OR'd. ";
                                      };
                                      "target_attributes" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "hostname" = mkOption {
                                                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                                  description = "";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "Hostname selector map. Only the hostname key is supported. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "Target matches when any selector in this rule matches. ";
                            };
                            "port" = mkOption {
                              type = (types.either referenceType (types.number));
                              description = "The port that the targets use for the chosen communication protocol. A port cannot be assigned to multiple protocols. ";
                            };
                            "protocol" = mkOption {
                              type = (types.either referenceType (types.str));
                              description = ''
                                The communication protocol your application secures.
                                Available values: "SSH", "RDP". '';
                            };
                            "require" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "tags" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.attrsOf (types.either referenceType (types.listOf (types.either referenceType (types.str))))
                                          )
                                        );
                                        description = "Map of target tag keys to values. Values within a key are OR'd. ";
                                      };
                                      "target_attributes" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "hostname" = mkOption {
                                                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                                  description = "";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "Hostname selector map. Only the hostname key is supported. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "Target matches only when every selector in this rule matches. ";
                            };
                            "target_attributes" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.attrsOf (types.either referenceType (types.listOf (types.either referenceType (types.str))))
                                )
                              );
                              description = "Deprecated: use include.target_attributes for hostname selectors and rule tags for tag-based matching. New or changed maps may contain only hostname. The API accepts unchanged legacy maps on update, including non-hostname keys. ";
                            };

                          };
                        }
                      )
                    )
                  );
                  description = "Defines targets covered by the application. Use either the legacy top-level target_attributes format or the rule format, but not both. The rule format requires include; require and exclude are optional. Each rule must contain a non-empty target_attributes or tags selector. Hostname selectors accept 1-5000 non-empty strings. Tag maps accept 1-50 non-empty keys, each with 1-50 non-empty values. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "type" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''
                    The application type.
                    Available values: "self_hosted", "saas", "ssh", "vnc", "app_launcher", "warp", "biso", "bookmark", "dash_sso", "infrastructure", "rdp", "mcp", "mcp_portal", "proxy_endpoint". '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "zone_id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The Zone ID to use for this endpoint. Mutually exclusive with the Account ID. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = "";
      };
      "cloudflare_zero_trust_access_policy" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated cloudflare provider";
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

                "account_id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Identifier. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "app_count" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "Number of access applications currently using this policy. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "approval_groups" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.listOf (
                        types.submodule {
                          options = {
                            "approvals_needed" = mkOption {
                              type = (types.either referenceType (types.number));
                              description = "The number of approvals needed to obtain access. ";
                            };
                            "email_addresses" = mkUnsetOption {
                              type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                              description = "A list of emails that can approve the access request. ";
                            };
                            "email_list_uuid" = mkUnsetOption {
                              type = (types.either referenceType (types.str));
                              description = "The UUID of an re-usable email list. ";
                            };

                          };
                        }
                      )
                    )
                  );
                  description = "Administrators who can approve a temporary authentication request. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "approval_required" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Requires the user to request access from an administrator at the start of each session. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "connection_rules" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.submodule {
                        options = {
                          "rdp" = mkUnsetOption {
                            type = (
                              types.either referenceType (
                                types.submodule {
                                  options = {
                                    "allowed_clipboard_local_to_remote_formats" = mkUnsetOption {
                                      type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                      description = "Clipboard formats allowed when copying from local machine to remote RDP session. ";
                                    };
                                    "allowed_clipboard_remote_to_local_formats" = mkUnsetOption {
                                      type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                      description = "Clipboard formats allowed when copying from remote RDP session to local machine. ";
                                    };

                                  };
                                }
                              )
                            );
                            description = "The RDP-specific rules that define clipboard behavior for RDP connections. ";
                          };

                        };
                      }
                    )
                  );
                  description = "The rules that define how users may connect to targets secured by your application. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "created_at" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "decision" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = ''
                    The action Access will take if a user matches this policy. Infrastructure application policies can only use the Allow action.
                    Available values: "allow", "deny", "non_identity", "bypass". '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "exclude" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.listOf (
                        types.submodule {
                          options = {
                            "any_valid_service_token" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = { };
                                  }
                                )
                              );
                              description = "An empty object which matches on all service tokens. ";
                            };
                            "auth_context" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "ac_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ACID of an Authentication context. ";
                                      };
                                      "id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of an Authentication context. ";
                                      };
                                      "identity_provider_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of your Azure identity provider. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "auth_method" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "auth_method" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The type of authentication method https://datatracker.ietf.org/doc/html/rfc8176#section-2. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "azure_ad" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of an Azure group. ";
                                      };
                                      "identity_provider_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of your Azure identity provider. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "certificate" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = { };
                                  }
                                )
                              );
                              description = "";
                            };
                            "cloudflare_account_member" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "account_id" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = "Identifier. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "common_name" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "common_name" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The common name to match. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "device_posture" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "account_id" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of the account that owns the device posture integration. ";
                                      };
                                      "integration_uid" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of a device posture integration. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "email" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "email" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The email of the user. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "email_domain" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "domain" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The email domain to match. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "email_list" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of a previously created email list. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "everyone" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = { };
                                  }
                                )
                              );
                              description = "An empty object which matches on all users. ";
                            };
                            "external_evaluation" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "evaluate_url" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The API endpoint containing your business logic. ";
                                      };
                                      "keys_url" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The API endpoint containing the key that Access uses to verify that the response came from your API. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "geo" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "country_code" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The country code that should be matched. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "github_organization" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "identity_provider_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of your Github identity provider. ";
                                      };
                                      "name" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The name of the organization. ";
                                      };
                                      "team" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The name of the team ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "group" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of a previously created Access group. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "gsuite" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "email" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The email of the Google Workspace group. ";
                                      };
                                      "identity_provider_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of your Google Workspace identity provider. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "ip" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "ip" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "An IPv4 or IPv6 CIDR block. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "ip_list" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of a previously created IP list. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "linked_app_token" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "app_uid" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of an Access OIDC SaaS application ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "login_method" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of an identity provider. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "oidc" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "claim_name" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The name of the OIDC claim. ";
                                      };
                                      "claim_value" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The OIDC claim value to look for. ";
                                      };
                                      "identity_provider_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of your OIDC identity provider. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "okta" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "identity_provider_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of your Okta identity provider. ";
                                      };
                                      "name" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The name of the Okta group. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "saml" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "attribute_name" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The name of the SAML attribute. ";
                                      };
                                      "attribute_value" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The SAML attribute value to look for. ";
                                      };
                                      "identity_provider_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of your SAML identity provider. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "service_token" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "token_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of a Service Token. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "user_risk_score" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "user_risk_score" = mkOption {
                                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                        description = "A list of risk score levels to match. Values can be low, medium, high, or unscored. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };

                          };
                        }
                      )
                    )
                  );
                  description = "Rules evaluated with a NOT logical operator. To match the policy, a user cannot meet any of the Exclude rules. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The UUID of the policy ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "include" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.listOf (
                        types.submodule {
                          options = {
                            "any_valid_service_token" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = { };
                                  }
                                )
                              );
                              description = "An empty object which matches on all service tokens. ";
                            };
                            "auth_context" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "ac_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ACID of an Authentication context. ";
                                      };
                                      "id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of an Authentication context. ";
                                      };
                                      "identity_provider_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of your Azure identity provider. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "auth_method" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "auth_method" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The type of authentication method https://datatracker.ietf.org/doc/html/rfc8176#section-2. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "azure_ad" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of an Azure group. ";
                                      };
                                      "identity_provider_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of your Azure identity provider. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "certificate" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = { };
                                  }
                                )
                              );
                              description = "";
                            };
                            "cloudflare_account_member" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "account_id" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = "Identifier. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "common_name" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "common_name" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The common name to match. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "device_posture" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "account_id" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of the account that owns the device posture integration. ";
                                      };
                                      "integration_uid" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of a device posture integration. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "email" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "email" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The email of the user. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "email_domain" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "domain" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The email domain to match. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "email_list" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of a previously created email list. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "everyone" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = { };
                                  }
                                )
                              );
                              description = "An empty object which matches on all users. ";
                            };
                            "external_evaluation" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "evaluate_url" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The API endpoint containing your business logic. ";
                                      };
                                      "keys_url" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The API endpoint containing the key that Access uses to verify that the response came from your API. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "geo" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "country_code" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The country code that should be matched. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "github_organization" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "identity_provider_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of your Github identity provider. ";
                                      };
                                      "name" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The name of the organization. ";
                                      };
                                      "team" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The name of the team ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "group" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of a previously created Access group. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "gsuite" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "email" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The email of the Google Workspace group. ";
                                      };
                                      "identity_provider_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of your Google Workspace identity provider. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "ip" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "ip" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "An IPv4 or IPv6 CIDR block. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "ip_list" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of a previously created IP list. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "linked_app_token" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "app_uid" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of an Access OIDC SaaS application ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "login_method" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of an identity provider. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "oidc" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "claim_name" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The name of the OIDC claim. ";
                                      };
                                      "claim_value" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The OIDC claim value to look for. ";
                                      };
                                      "identity_provider_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of your OIDC identity provider. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "okta" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "identity_provider_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of your Okta identity provider. ";
                                      };
                                      "name" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The name of the Okta group. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "saml" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "attribute_name" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The name of the SAML attribute. ";
                                      };
                                      "attribute_value" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The SAML attribute value to look for. ";
                                      };
                                      "identity_provider_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of your SAML identity provider. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "service_token" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "token_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of a Service Token. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "user_risk_score" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "user_risk_score" = mkOption {
                                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                        description = "A list of risk score levels to match. Values can be low, medium, high, or unscored. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };

                          };
                        }
                      )
                    )
                  );
                  description = "Rules evaluated with an OR logical operator. A user needs to meet only one of the Include rules. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "isolation_required" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Require this application to be served in an isolated browser for users matching this policy. 'Client Web Isolation' must be on for the account in order to use this feature. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "mfa_config" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.submodule {
                        options = {
                          "allowed_authenticators" = mkUnsetOption {
                            type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                            description = "Lists the MFA methods that users can authenticate with. ";
                          };
                          "mfa_disabled" = mkUnsetOption {
                            type = (types.either referenceType (types.bool));
                            description = "Indicates whether to disable MFA for this resource. This option is available at the application and policy level. ";
                          };
                          "session_duration" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "Defines the duration of an MFA session. Must be in minutes (m) or hours (h). Minimum: 0m. Maximum: 720h (30 days). Examples:`5m` or `24h`. ";
                          };

                        };
                      }
                    )
                  );
                  description = "Configures multi-factor authentication (MFA) settings. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "The name of the Access policy. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "purpose_justification_prompt" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "A custom message that will appear on the purpose justification screen. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "purpose_justification_required" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "Require users to enter a justification when they log in to the application. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "require" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.listOf (
                        types.submodule {
                          options = {
                            "any_valid_service_token" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = { };
                                  }
                                )
                              );
                              description = "An empty object which matches on all service tokens. ";
                            };
                            "auth_context" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "ac_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ACID of an Authentication context. ";
                                      };
                                      "id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of an Authentication context. ";
                                      };
                                      "identity_provider_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of your Azure identity provider. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "auth_method" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "auth_method" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The type of authentication method https://datatracker.ietf.org/doc/html/rfc8176#section-2. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "azure_ad" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of an Azure group. ";
                                      };
                                      "identity_provider_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of your Azure identity provider. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "certificate" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = { };
                                  }
                                )
                              );
                              description = "";
                            };
                            "cloudflare_account_member" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "account_id" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = "Identifier. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "common_name" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "common_name" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The common name to match. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "device_posture" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "account_id" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of the account that owns the device posture integration. ";
                                      };
                                      "integration_uid" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of a device posture integration. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "email" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "email" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The email of the user. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "email_domain" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "domain" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The email domain to match. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "email_list" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of a previously created email list. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "everyone" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = { };
                                  }
                                )
                              );
                              description = "An empty object which matches on all users. ";
                            };
                            "external_evaluation" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "evaluate_url" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The API endpoint containing your business logic. ";
                                      };
                                      "keys_url" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The API endpoint containing the key that Access uses to verify that the response came from your API. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "geo" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "country_code" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The country code that should be matched. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "github_organization" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "identity_provider_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of your Github identity provider. ";
                                      };
                                      "name" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The name of the organization. ";
                                      };
                                      "team" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The name of the team ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "group" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of a previously created Access group. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "gsuite" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "email" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The email of the Google Workspace group. ";
                                      };
                                      "identity_provider_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of your Google Workspace identity provider. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "ip" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "ip" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "An IPv4 or IPv6 CIDR block. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "ip_list" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of a previously created IP list. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "linked_app_token" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "app_uid" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of an Access OIDC SaaS application ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "login_method" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of an identity provider. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "oidc" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "claim_name" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The name of the OIDC claim. ";
                                      };
                                      "claim_value" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The OIDC claim value to look for. ";
                                      };
                                      "identity_provider_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of your OIDC identity provider. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "okta" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "identity_provider_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of your Okta identity provider. ";
                                      };
                                      "name" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The name of the Okta group. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "saml" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "attribute_name" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The name of the SAML attribute. ";
                                      };
                                      "attribute_value" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The SAML attribute value to look for. ";
                                      };
                                      "identity_provider_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of your SAML identity provider. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "service_token" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "token_id" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "The ID of a Service Token. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };
                            "user_risk_score" = mkUnsetOption {
                              type = (
                                types.either referenceType (
                                  types.submodule {
                                    options = {
                                      "user_risk_score" = mkOption {
                                        type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                        description = "A list of risk score levels to match. Values can be low, medium, high, or unscored. ";
                                      };

                                    };
                                  }
                                )
                              );
                              description = "";
                            };

                          };
                        }
                      )
                    )
                  );
                  description = "Rules evaluated with an AND logical operator. To match the policy, a user must meet all of the Require rules. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "reusable" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "session_duration" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "The amount of time that tokens issued for the application will be valid. Must be in the format `300ms` or `2h45m`. Valid time units are: ns, us (or µs), ms, s, m, h. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "updated_at" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = ''
          Accepted Permissions

          - `Access: Apps and Policies Read`
          - `Access: Apps and Policies Write`
        '';
      };
      "cloudflare_zero_trust_tunnel_cloudflared_config" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated cloudflare provider";
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

                "account_id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "Identifier. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "config" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.submodule {
                        options = {
                          "ingress" = mkUnsetOption {
                            type = (
                              types.either referenceType (
                                types.listOf (
                                  types.submodule {
                                    options = {
                                      "hostname" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = "Public hostname for this service. ";
                                      };
                                      "origin_request" = mkUnsetOption {
                                        type = (
                                          types.either referenceType (
                                            types.submodule {
                                              options = {
                                                "access" = mkUnsetOption {
                                                  type = (
                                                    types.either referenceType (
                                                      types.submodule {
                                                        options = {
                                                          "aud_tag" = mkOption {
                                                            type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                                            description = "Access applications that are allowed to reach this hostname for this Tunnel. Audience tags can be identified in the dashboard or via the List Access policies API. ";
                                                          };
                                                          "required" = mkUnsetOption {
                                                            type = (types.either referenceType (types.bool));
                                                            description = "Deny traffic that has not fulfilled Access authorization. ";
                                                          };
                                                          "team_name" = mkOption {
                                                            type = (types.either referenceType (types.str));
                                                            description = "";
                                                          };

                                                        };
                                                      }
                                                    )
                                                  );
                                                  description = "For all L7 requests to this hostname, cloudflared will validate each request's Cf-Access-Jwt-Assertion request header. ";
                                                };
                                                "ca_pool" = mkUnsetOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = "Path to the certificate authority (CA) for the certificate of your origin. This option should be used only if your certificate is not signed by Cloudflare. ";
                                                };
                                                "connect_timeout" = mkUnsetOption {
                                                  type = (types.either referenceType (types.number));
                                                  description = "Timeout for establishing a new TCP connection to your origin server. This excludes the time taken to establish TLS, which is controlled by tlsTimeout. ";
                                                };
                                                "disable_chunked_encoding" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Disables chunked transfer encoding. Useful if you are running a WSGI server. ";
                                                };
                                                "http2_origin" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Attempt to connect to origin using HTTP2. Origin must be configured as https. ";
                                                };
                                                "http_host_header" = mkUnsetOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = "Sets the HTTP Host header on requests sent to the local service. ";
                                                };
                                                "keep_alive_connections" = mkUnsetOption {
                                                  type = (types.either referenceType (types.number));
                                                  description = "Maximum number of idle keepalive connections between Tunnel and your origin. This does not restrict the total number of concurrent connections. ";
                                                };
                                                "keep_alive_timeout" = mkUnsetOption {
                                                  type = (types.either referenceType (types.number));
                                                  description = "Timeout after which an idle keepalive connection can be discarded. ";
                                                };
                                                "match_sn_ito_host" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Auto configure the Hostname on the origin server certificate. ";
                                                };
                                                "no_happy_eyeballs" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Disable the “happy eyeballs” algorithm for IPv4/IPv6 fallback if your local network has misconfigured one of the protocols. ";
                                                };
                                                "no_tls_verify" = mkUnsetOption {
                                                  type = (types.either referenceType (types.bool));
                                                  description = "Disables TLS verification of the certificate presented by your origin. Will allow any certificate from the origin to be accepted. ";
                                                };
                                                "origin_server_name" = mkUnsetOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = "Hostname that cloudflared should expect from your origin server certificate. ";
                                                };
                                                "proxy_type" = mkUnsetOption {
                                                  type = (types.either referenceType (types.str));
                                                  description = ''cloudflared starts a proxy server to translate HTTP traffic into TCP when proxying, for example, SSH or RDP. This configures what type of proxy will be started. Valid options are: "" for the regular proxy and "socks" for a SOCKS5 proxy. '';
                                                };
                                                "tcp_keep_alive" = mkUnsetOption {
                                                  type = (types.either referenceType (types.number));
                                                  description = "The timeout after which a TCP keepalive packet is sent on a connection between Tunnel and the origin server. ";
                                                };
                                                "tls_timeout" = mkUnsetOption {
                                                  type = (types.either referenceType (types.number));
                                                  description = "Timeout for completing a TLS handshake to your origin server, if you have chosen to connect Tunnel to an HTTPS server. ";
                                                };

                                              };
                                            }
                                          )
                                        );
                                        description = "Configuration parameters for the public hostname specific connection settings between cloudflared and origin server. ";
                                      };
                                      "path" = mkUnsetOption {
                                        type = (types.either referenceType (types.str));
                                        description = "Requests with this path route to this public hostname. ";
                                      };
                                      "service" = mkOption {
                                        type = (types.either referenceType (types.str));
                                        description = "Protocol and address of destination server. Supported protocols: http://, https://, unix://, tcp://, ssh://, rdp://, unix+tls://, smb://. Alternatively can return a HTTP status code http_status:[code] e.g. 'http_status:404'. ";
                                      };

                                    };
                                  }
                                )
                              )
                            );
                            description = "List of public hostname definitions. At least one ingress rule needs to be defined for the tunnel. ";
                          };
                          "origin_request" = mkUnsetOption {
                            type = (
                              types.either referenceType (
                                types.submodule {
                                  options = {
                                    "access" = mkUnsetOption {
                                      type = (
                                        types.either referenceType (
                                          types.submodule {
                                            options = {
                                              "aud_tag" = mkOption {
                                                type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                                                description = "Access applications that are allowed to reach this hostname for this Tunnel. Audience tags can be identified in the dashboard or via the List Access policies API. ";
                                              };
                                              "required" = mkUnsetOption {
                                                type = (types.either referenceType (types.bool));
                                                description = "Deny traffic that has not fulfilled Access authorization. ";
                                              };
                                              "team_name" = mkOption {
                                                type = (types.either referenceType (types.str));
                                                description = "";
                                              };

                                            };
                                          }
                                        )
                                      );
                                      description = "For all L7 requests to this hostname, cloudflared will validate each request's Cf-Access-Jwt-Assertion request header. ";
                                    };
                                    "ca_pool" = mkUnsetOption {
                                      type = (types.either referenceType (types.str));
                                      description = "Path to the certificate authority (CA) for the certificate of your origin. This option should be used only if your certificate is not signed by Cloudflare. ";
                                    };
                                    "connect_timeout" = mkUnsetOption {
                                      type = (types.either referenceType (types.number));
                                      description = "Timeout for establishing a new TCP connection to your origin server. This excludes the time taken to establish TLS, which is controlled by tlsTimeout. ";
                                    };
                                    "disable_chunked_encoding" = mkUnsetOption {
                                      type = (types.either referenceType (types.bool));
                                      description = "Disables chunked transfer encoding. Useful if you are running a WSGI server. ";
                                    };
                                    "http2_origin" = mkUnsetOption {
                                      type = (types.either referenceType (types.bool));
                                      description = "Attempt to connect to origin using HTTP2. Origin must be configured as https. ";
                                    };
                                    "http_host_header" = mkUnsetOption {
                                      type = (types.either referenceType (types.str));
                                      description = "Sets the HTTP Host header on requests sent to the local service. ";
                                    };
                                    "keep_alive_connections" = mkUnsetOption {
                                      type = (types.either referenceType (types.number));
                                      description = "Maximum number of idle keepalive connections between Tunnel and your origin. This does not restrict the total number of concurrent connections. ";
                                    };
                                    "keep_alive_timeout" = mkUnsetOption {
                                      type = (types.either referenceType (types.number));
                                      description = "Timeout after which an idle keepalive connection can be discarded. ";
                                    };
                                    "match_sn_ito_host" = mkUnsetOption {
                                      type = (types.either referenceType (types.bool));
                                      description = "Auto configure the Hostname on the origin server certificate. ";
                                    };
                                    "no_happy_eyeballs" = mkUnsetOption {
                                      type = (types.either referenceType (types.bool));
                                      description = "Disable the “happy eyeballs” algorithm for IPv4/IPv6 fallback if your local network has misconfigured one of the protocols. ";
                                    };
                                    "no_tls_verify" = mkUnsetOption {
                                      type = (types.either referenceType (types.bool));
                                      description = "Disables TLS verification of the certificate presented by your origin. Will allow any certificate from the origin to be accepted. ";
                                    };
                                    "origin_server_name" = mkUnsetOption {
                                      type = (types.either referenceType (types.str));
                                      description = "Hostname that cloudflared should expect from your origin server certificate. ";
                                    };
                                    "proxy_type" = mkUnsetOption {
                                      type = (types.either referenceType (types.str));
                                      description = ''cloudflared starts a proxy server to translate HTTP traffic into TCP when proxying, for example, SSH or RDP. This configures what type of proxy will be started. Valid options are: "" for the regular proxy and "socks" for a SOCKS5 proxy. '';
                                    };
                                    "tcp_keep_alive" = mkUnsetOption {
                                      type = (types.either referenceType (types.number));
                                      description = "The timeout after which a TCP keepalive packet is sent on a connection between Tunnel and the origin server. ";
                                    };
                                    "tls_timeout" = mkUnsetOption {
                                      type = (types.either referenceType (types.number));
                                      description = "Timeout for completing a TLS handshake to your origin server, if you have chosen to connect Tunnel to an HTTPS server. ";
                                    };

                                  };
                                }
                              )
                            );
                            description = "Configuration parameters for the public hostname specific connection settings between cloudflared and origin server. ";
                          };

                        };
                      }
                    )
                  );
                  description = "The tunnel configuration and ingress rules. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "created_at" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the tunnel. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "source" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''
                    Indicates if this is a locally or remotely configured tunnel. If `local`, manage the tunnel using a YAML file on the origin machine. If `cloudflare`, manage the tunnel's configuration on the Zero Trust dashboard.
                    Available values: "local", "cloudflare". '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tunnel_id" = mkOption {
                  type = (types.either referenceType (types.str));
                  description = "UUID of the tunnel. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "version" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = "The version of the Tunnel Configuration. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = ''
          Accepted Permissions

          - `Cloudflare One Connector: cloudflared Read`
          - `Cloudflare One Connector: cloudflared Write`
          - `Cloudflare One Connectors Read`
          - `Cloudflare One Connectors Write`
          - `Cloudflare Tunnel Read`
          - `Cloudflare Tunnel Write`
        '';
      };

    };
    data = {
      "cloudflare_zone" = mkUnsetOption {
        type = types.attrsOf (
          types.submodule (
            { name, ... }: {
              _file = "generated cloudflare provider";
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

                "account" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.submodule {
                        options = {
                          "id" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "Identifier ";
                          };
                          "name" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "The name of the account. ";
                          };

                        };
                      }
                    )
                  );
                  description = "The account the zone belongs to. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "activated_on" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''
                    The last time proof of ownership was detected and the zone was made
                    active. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "cname_suffix" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''
                    Allows the customer to use a custom apex.
                    *Tenants Only Configuration*. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "created_on" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "When the zone was created. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "development_mode" = mkUnsetOption {
                  type = (types.either referenceType (types.number));
                  description = ''
                    The interval (in seconds) from when development mode expires
                    (positive integer) or last expired (negative integer) for the
                    domain. If development mode has never been enabled, this value is 0. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "filter" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.submodule {
                        options = {
                          "account" = mkUnsetOption {
                            type = (
                              types.either referenceType (
                                types.submodule {
                                  options = {
                                    "id" = mkUnsetOption {
                                      type = (types.either referenceType (types.str));
                                      description = "Filter by an account ID. ";
                                    };
                                    "name" = mkUnsetOption {
                                      type = (types.either referenceType (types.str));
                                      description = ''
                                        An account Name. Optional filter operators can be provided to extend refine the search:
                                          * `equal` (default)
                                          * `not_equal`
                                          * `starts_with`
                                          * `ends_with`
                                          * `contains`
                                          * `starts_with_case_sensitive`
                                          * `ends_with_case_sensitive`
                                          * `contains_case_sensitive` '';
                                    };

                                  };
                                }
                              )
                            );
                            description = "";
                          };
                          "direction" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = ''
                              Direction to order zones.
                              Available values: "asc", "desc". '';
                          };
                          "match" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = ''
                              Whether to match all search requirements or at least one (any).
                              Available values: "any", "all". '';
                          };
                          "name" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = ''
                              A domain name. Optional filter operators can be provided to extend refine the search:
                                * `equal` (default)
                                * `not_equal`
                                * `starts_with`
                                * `ends_with`
                                * `contains`
                                * `starts_with_case_sensitive`
                                * `ends_with_case_sensitive`
                                * `contains_case_sensitive` '';
                          };
                          "order" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = ''
                              Field to order zones by.
                              Available values: "name", "status", "account.id", "account.name", "plan.id". '';
                          };
                          "status" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = ''
                              Specify a zone status to filter by.
                              Available values: "initializing", "pending", "active", "moved". '';
                          };
                          "type" = mkUnsetOption {
                            type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                            description = ''Zone types to filter by. Multiple types can be specified as a comma-separated list (e.g., ?type=full,partial,secondary). When this parameter is not provided, zones with type "internal" are excluded from the results. '';
                          };

                        };
                      }
                    )
                  );
                  description = "";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Identifier ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "meta" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.submodule {
                        options = {
                          "cdn_only" = mkUnsetOption {
                            type = (types.either referenceType (types.bool));
                            description = "The zone is only configured for CDN. ";
                          };
                          "custom_certificate_quota" = mkUnsetOption {
                            type = (types.either referenceType (types.number));
                            description = "Number of Custom Certificates the zone can have. ";
                          };
                          "dns_only" = mkUnsetOption {
                            type = (types.either referenceType (types.bool));
                            description = "The zone is only configured for DNS. ";
                          };
                          "foundation_dns" = mkUnsetOption {
                            type = (types.either referenceType (types.bool));
                            description = "The zone is setup with Foundation DNS. ";
                          };
                          "page_rule_quota" = mkUnsetOption {
                            type = (types.either referenceType (types.number));
                            description = "Number of Page Rules a zone can have. ";
                          };
                          "phishing_detected" = mkUnsetOption {
                            type = (types.either referenceType (types.bool));
                            description = "The zone has been flagged for phishing. ";
                          };
                          "step" = mkUnsetOption {
                            type = (types.either referenceType (types.number));
                            description = "";
                          };

                        };
                      }
                    )
                  );
                  description = "Metadata about the zone. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "modified_on" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "When the zone was last modified. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''The domain name. Per [RFC 1035](https://datatracker.ietf.org/doc/html/rfc1035#section-2.3.4) the overall zone name can be up to 253 characters, with each segment ("label") not exceeding 63 characters. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "name_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "The name servers Cloudflare assigns to a zone. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "original_dnshost" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "DNS host at the time of switching to Cloudflare. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "original_name_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Original name servers before moving to Cloudflare. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "original_registrar" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Registrar for the domain at the time of switching to Cloudflare. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "owner" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.submodule {
                        options = {
                          "id" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "Identifier ";
                          };
                          "name" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "Name of the owner. ";
                          };
                          "type" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "The type of owner. ";
                          };

                        };
                      }
                    )
                  );
                  description = "The owner of the zone. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "paused" = mkUnsetOption {
                  type = (types.either referenceType (types.bool));
                  description = ''
                    Indicates whether the zone is only using Cloudflare DNS services. A
                    true value means the zone will not receive security or performance
                    benefits. '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "permissions" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "Legacy permissions based on legacy user membership information. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "plan" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.submodule {
                        options = {
                          "can_subscribe" = mkUnsetOption {
                            type = (types.either referenceType (types.bool));
                            description = "States if the subscription can be activated. ";
                          };
                          "currency" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "The denomination of the customer. ";
                          };
                          "externally_managed" = mkUnsetOption {
                            type = (types.either referenceType (types.bool));
                            description = "If this Zone is managed by another company. ";
                          };
                          "frequency" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "How often the customer is billed. ";
                          };
                          "id" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "Identifier ";
                          };
                          "is_subscribed" = mkUnsetOption {
                            type = (types.either referenceType (types.bool));
                            description = "States if the subscription active. ";
                          };
                          "legacy_discount" = mkUnsetOption {
                            type = (types.either referenceType (types.bool));
                            description = "If the legacy discount applies to this Zone. ";
                          };
                          "legacy_id" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "The legacy name of the plan. ";
                          };
                          "name" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "Name of the owner. ";
                          };
                          "price" = mkUnsetOption {
                            type = (types.either referenceType (types.number));
                            description = "How much the customer is paying. ";
                          };

                        };
                      }
                    )
                  );
                  description = "A Zones subscription information. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "status" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''
                    The zone status on Cloudflare.
                    Available values: "initializing", "pending", "active", "moved". '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tenant" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.submodule {
                        options = {
                          "id" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "Identifier ";
                          };
                          "name" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "The name of the Tenant account. ";
                          };

                        };
                      }
                    )
                  );
                  description = "The root organizational unit that this zone belongs to (such as a tenant or organization). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "tenant_unit" = mkUnsetOption {
                  type = (
                    types.either referenceType (
                      types.submodule {
                        options = {
                          "id" = mkUnsetOption {
                            type = (types.either referenceType (types.str));
                            description = "Identifier ";
                          };

                        };
                      }
                    )
                  );
                  description = "The immediate parent organizational unit that this zone belongs to (such as under a tenant or sub-organization). ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "type" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = ''
                    A full zone implies that DNS is hosted with Cloudflare. A partial zone is
                    typically a partner-hosted zone or a CNAME setup.
                    Available values: "full", "partial", "secondary", "internal". '';
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "vanity_name_servers" = mkUnsetOption {
                  type = (types.either referenceType (types.listOf (types.either referenceType (types.str))));
                  description = "An array of domains used for custom name servers. This is only available for Business and Enterprise plans. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "verification_key" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Verification key for partial zone setup. ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };
                "zone_id" = mkUnsetOption {
                  type = (types.either referenceType (types.str));
                  description = "Identifier ";
                  apply = val: if val ? __toString then builtins.toString val else val;
                };

              };
            }
          )
        );
        description = ''
          Accepted Permissions

          - `Access: Apps and Policies Read`
          - `Access: Apps and Policies Revoke`
          - `Access: Apps and Policies Write`
          - `Access: Mutual TLS Certificates Write`
          - `Access: Organizations, Identity Providers, and Groups Write`
          - `Analytics Read`
          - `Apps Write`
          - `Cache Purge`
          - `DNS Read`
          - `DNS Write`
          - `Firewall Services Read`
          - `Firewall Services Write`
          - `Load Balancers Read`
          - `Load Balancers Write`
          - `Logs Read`
          - `Logs Write`
          - `Page Rules Read`
          - `Page Rules Write`
          - `SSL and Certificates Read`
          - `SSL and Certificates Write`
          - `Stream Read`
          - `Stream Write`
          - `Trust and Safety Read`
          - `Trust and Safety Write`
          - `Workers Routes Read`
          - `Workers Routes Write`
          - `Workers Scripts Read`
          - `Workers Scripts Write`
          - `Zaraz Admin`
          - `Zaraz Edit`
          - `Zaraz Read`
          - `Zero Trust: PII Read`
          - `Zone Read`
          - `Zone Settings Read`
          - `Zone Settings Write`
          - `Zone Write`
          - `Zone Zone Read`
        '';
      };

    };
  };
  # hacky unfortunately
  options = {
    inherit (config.generated) resource data provider;
  };
}
