# Authoritative Access declarations. Resource keys and import IDs are stable.
{
  account = "5d99d63dea23dde67ccb07e5dfb31107";
  applicationDefaults = {
    account_id = "5d99d63dea23dde67ccb07e5dfb31107";
    session_duration = "24h";
    lifecycle = {
      prevent_destroy = true;
    };
  };
  applications = {
    approvals = {
      app_launcher_visible = true;
      auto_redirect_to_identity = false;
      destinations = [
        {
          type = "public";
          uri = "approvals.rupan.dev";
        }
      ];
      domain = "approvals.rupan.dev";
      enable_binding_cookie = false;
      http_only_cookie_attribute = true;
      name = "approvals";
      options_preflight_bypass = false;
      policies = [
        {
          id = "\${cloudflare_zero_trust_access_policy.approvals_owner.id}";
          precedence = 1;
        }
      ];
      type = "self_hosted";
    };
    bypass_arcade_controller = {
      app_launcher_visible = true;
      auto_redirect_to_identity = false;
      destinations = [
        {
          type = "public";
          uri = "arcade.rupan.dev";
        }
      ];
      domain = "arcade.rupan.dev";
      enable_binding_cookie = false;
      http_only_cookie_attribute = true;
      name = "Bypass - Arcade controller";
      options_preflight_bypass = false;
      policies = [
        {
          id = "\${cloudflare_zero_trust_access_policy.bypass.id}";
          precedence = 1;
        }
      ];
      type = "self_hosted";
    };
    bypass_flux_webhook = {
      app_launcher_visible = true;
      auto_redirect_to_identity = false;
      destinations = [
        {
          type = "public";
          uri = "flux-wh-33b0c8004348.rupan.dev";
        }
      ];
      domain = "flux-wh-33b0c8004348.rupan.dev";
      enable_binding_cookie = false;
      http_only_cookie_attribute = true;
      name = "Bypass - Flux webhook";
      options_preflight_bypass = false;
      policies = [
        {
          id = "\${cloudflare_zero_trust_access_policy.bypass.id}";
          precedence = 1;
        }
      ];
      type = "self_hosted";
    };
    bypass_home_assistant = {
      app_launcher_visible = true;
      auto_redirect_to_identity = false;
      destinations = [
        {
          type = "public";
          uri = "ha.rupan.dev";
        }
      ];
      domain = "ha.rupan.dev";
      enable_binding_cookie = false;
      http_only_cookie_attribute = true;
      name = "Bypass - Home Assistant";
      options_preflight_bypass = false;
      policies = [
        {
          id = "\${cloudflare_zero_trust_access_policy.bypass.id}";
          precedence = 1;
        }
      ];
      type = "self_hosted";
    };
    bypass_music_assistant_and_ledfx = {
      app_launcher_visible = true;
      auto_redirect_to_identity = false;
      destinations = [
        {
          type = "public";
          uri = "ma.rupan.dev";
        }
      ];
      domain = "ma.rupan.dev";
      enable_binding_cookie = false;
      http_only_cookie_attribute = true;
      name = "Bypass - Music Assistant";
      options_preflight_bypass = false;
      policies = [
        {
          id = "\${cloudflare_zero_trust_access_policy.bypass.id}";
          precedence = 1;
        }
      ];
      type = "self_hosted";
    };
    bypass_music_sync_and_tools = {
      app_launcher_visible = true;
      auto_redirect_to_identity = false;
      destinations = [
        {
          type = "public";
          uri = "music.rupan.dev";
        }
        {
          type = "public";
          uri = "nix.rupan.dev";
        }
        {
          type = "public";
          uri = "soluble.rupan.dev";
        }
        {
          type = "public";
          uri = "obsidian.rupan.dev";
        }
        {
          type = "public";
          uri = "n8n.rupan.dev";
        }
      ];
      domain = "music.rupan.dev";
      enable_binding_cookie = false;
      http_only_cookie_attribute = false;
      name = "Bypass - Music, sync and tools";
      options_preflight_bypass = false;
      policies = [
        {
          id = "\${cloudflare_zero_trust_access_policy.bypass.id}";
          precedence = 1;
        }
      ];
      type = "self_hosted";
    };
    bypass_notes_and_project_sites = {
      app_launcher_visible = true;
      auto_redirect_to_identity = false;
      destinations = [
        {
          type = "public";
          uri = "rupanism.rupan.dev";
        }
        {
          type = "public";
          uri = "ism.rupan.dev";
        }
        {
          type = "public";
          uri = "notes.rupan.dev";
        }
        {
          type = "public";
          uri = "pulse.rupan.dev";
        }
        {
          type = "public";
          uri = "spatia.rupan.dev";
        }
      ];
      domain = "rupanism.rupan.dev";
      enable_binding_cookie = false;
      http_only_cookie_attribute = false;
      name = "Bypass - Notes and project sites";
      options_preflight_bypass = false;
      policies = [
        {
          id = "\${cloudflare_zero_trust_access_policy.bypass.id}";
          precedence = 1;
        }
      ];
      type = "self_hosted";
    };
    bypass_photos_files_and_dashboards = {
      app_launcher_visible = true;
      auto_redirect_to_identity = false;
      destinations = [
        {
          type = "public";
          uri = "books.rupan.dev";
        }
        {
          type = "public";
          uri = "grafana.rupan.dev";
        }
        {
          type = "public";
          uri = "photos.rupan.dev";
        }
        {
          type = "public";
          uri = "media.rupan.dev";
        }
        {
          type = "public";
          uri = "jellyfin.rupan.dev";
        }
      ];
      domain = "books.rupan.dev";
      enable_binding_cookie = false;
      http_only_cookie_attribute = false;
      name = "Bypass - Photos, files and dashboards";
      options_preflight_bypass = false;
      policies = [
        {
          id = "\${cloudflare_zero_trust_access_policy.bypass.id}";
          precedence = 1;
        }
      ];
      type = "self_hosted";
    };
    bypass_requests_and_music_tools = {
      app_launcher_visible = true;
      auto_redirect_to_identity = false;
      destinations = [
        {
          type = "public";
          uri = "apptly.rupan.dev";
        }
        {
          type = "public";
          uri = "lidarr.rupan.dev";
        }
        {
          type = "public";
          uri = "aurral.rupan.dev";
        }
        {
          type = "public";
          uri = "spotify.rupan.dev";
        }
        {
          type = "public";
          uri = "seerr.rupan.dev";
        }
      ];
      domain = "apptly.rupan.dev";
      enable_binding_cookie = false;
      http_only_cookie_attribute = true;
      name = "Bypass - Requests and music tools";
      options_preflight_bypass = false;
      policies = [
        {
          id = "\${cloudflare_zero_trust_access_policy.bypass.id}";
          precedence = 1;
        }
      ];
      type = "self_hosted";
    };
    bypass_tally_tracker_endpoints = {
      app_launcher_visible = false;
      auto_redirect_to_identity = false;
      destinations = [
        {
          type = "public";
          uri = "tally.rupan.dev/t.js";
        }
        {
          type = "public";
          uri = "tally.rupan.dev/api/send";
        }
      ];
      domain = "tally.rupan.dev/t.js";
      enable_binding_cookie = false;
      http_only_cookie_attribute = true;
      name = "Bypass - Tally tracker endpoints";
      options_preflight_bypass = false;
      policies = [
        {
          id = "\${cloudflare_zero_trust_access_policy.bypass.id}";
          precedence = 1;
        }
      ];
      type = "self_hosted";
    };
    bypass_uwagent = {
      app_launcher_visible = true;
      auto_redirect_to_identity = false;
      destinations = [
        {
          type = "public";
          uri = "uwagent.rupan.dev";
        }
      ];
      domain = "uwagent.rupan.dev";
      enable_binding_cookie = false;
      http_only_cookie_attribute = true;
      name = "Bypass - UWagent";
      options_preflight_bypass = false;
      policies = [
        {
          id = "\${cloudflare_zero_trust_access_policy.bypass.id}";
          precedence = 1;
        }
      ];
      type = "self_hosted";
    };
    bypass_websites_and_automation = {
      app_launcher_visible = true;
      auto_redirect_to_identity = false;
      destinations = [
        {
          type = "public";
          uri = "demo.rupan.dev";
        }
        {
          type = "public";
          uri = "ntfy.rupan.dev";
        }
        {
          type = "public";
          uri = "renovate-approve.rupan.dev";
        }
        {
          type = "public";
          uri = "nix-agent.rupan.dev";
        }
        {
          type = "public";
          uri = "photo.rupan.dev";
        }
        {
          type = "public";
          uri = "majorfinder.rupan.dev";
        }
        {
          type = "public";
          uri = "www.rupan.dev";
        }
        {
          type = "public";
          uri = "rupan.dev";
        }
      ];
      domain = "demo.rupan.dev";
      enable_binding_cookie = false;
      http_only_cookie_attribute = true;
      name = "Bypass - Websites and automation";
      options_preflight_bypass = false;
      policies = [
        {
          id = "\${cloudflare_zero_trust_access_policy.bypass.id}";
          precedence = 1;
        }
      ];
      type = "self_hosted";
    };
    bypass_woodpecker_ci = {
      app_launcher_visible = false;
      auto_redirect_to_identity = false;
      destinations = [
        {
          type = "public";
          uri = "ci.rupan.dev";
        }
      ];
      domain = "ci.rupan.dev";
      enable_binding_cookie = false;
      http_only_cookie_attribute = true;
      name = "Bypass - Woodpecker CI";
      options_preflight_bypass = false;
      policies = [
        {
          id = "\${cloudflare_zero_trust_access_policy.bypass.id}";
          precedence = 1;
        }
      ];
      type = "self_hosted";
    };
    default = {
      app_launcher_visible = true;
      auto_redirect_to_identity = false;
      destinations = [
        {
          type = "public";
          uri = "*.rupan.dev";
        }
      ];
      domain = "*.rupan.dev";
      enable_binding_cookie = false;
      http_only_cookie_attribute = false;
      name = "*";
      options_preflight_bypass = false;
      policies = [
        {
          id = "\${cloudflare_zero_trust_access_policy.main_rupan.id}";
          precedence = 1;
        }
      ];
      type = "self_hosted";
    };
    forgejo = {
      app_launcher_visible = true;
      auto_redirect_to_identity = false;
      destinations = [
        {
          type = "public";
          uri = "git.rupan.dev";
        }
      ];
      domain = "git.rupan.dev";
      enable_binding_cookie = false;
      http_only_cookie_attribute = true;
      name = "forgejo";
      options_preflight_bypass = false;
      policies = [
        {
          id = "\${cloudflare_zero_trust_access_policy.roommates.id}";
          precedence = 1;
        }
      ];
      type = "self_hosted";
    };
    games = {
      allowed_idps = [ "d052562e-7c79-4bcc-bb0c-1808eb5e9c66" ];
      app_launcher_visible = true;
      auto_redirect_to_identity = true;
      destinations = [
        {
          type = "public";
          uri = "games.rupan.dev";
        }
      ];
      domain = "games.rupan.dev";
      enable_binding_cookie = false;
      http_only_cookie_attribute = false;
      name = "games";
      options_preflight_bypass = false;
      policies = [
        {
          id = "\${cloudflare_zero_trust_access_policy.friends.id}";
          precedence = 1;
        }
      ];
      type = "self_hosted";
    };
    grafana_oidc = {
      allowed_idps = [
        "7f9f5ab9-64b6-46ec-a8a4-33d10351f5ba"
        "d052562e-7c79-4bcc-bb0c-1808eb5e9c66"
      ];
      app_launcher_visible = true;
      auto_redirect_to_identity = false;
      domain = "rupan.cloudflareaccess.com/cdn-cgi/access/sso/oidc/a3ecc26035113025380ef4894c0fd69740739507d6b73e75c849c7ad29ab16a8";
      name = "Grafana OIDC";
      policies = [
        {
          id = "\${cloudflare_zero_trust_access_policy.main_rupan.id}";
          precedence = 1;
        }
      ];
      saas_app = {
        access_token_lifetime = "15m";
        allow_pkce_without_client_secret = false;
        app_launcher_url = "https://grafana.rupan.dev";
        auth_type = "oidc";
        grant_types = [ "authorization_code_with_pkce" ];
        redirect_uris = [ "https://grafana.rupan.dev/login/generic_oauth" ];
        scopes = [
          "openid"
          "email"
          "profile"
        ];
      };
      type = "saas";
    };
    immich_oidc = {
      allowed_idps = [
        "7f9f5ab9-64b6-46ec-a8a4-33d10351f5ba"
        "d052562e-7c79-4bcc-bb0c-1808eb5e9c66"
      ];
      app_launcher_visible = true;
      auto_redirect_to_identity = false;
      domain = "rupan.cloudflareaccess.com/cdn-cgi/access/sso/oidc/788ef25d0a3b1074d0dbe1e635cbbe8f237901415f822d2f9ac9184e470fb6c0";
      name = "Immich OIDC";
      policies = [
        {
          id = "\${cloudflare_zero_trust_access_policy.main_rupan.id}";
          precedence = 1;
        }
      ];
      saas_app = {
        access_token_lifetime = "15m";
        app_launcher_url = "https://photos.rupan.dev";
        auth_type = "oidc";
        grant_types = [ "authorization_code" ];
        redirect_uris = [
          "https://photos.rupan.dev/auth/login"
          "https://photos.rupan.dev/user-settings"
          "https://photos.rupan.dev/api/oauth/mobile-redirect"
        ];
        scopes = [
          "openid"
          "email"
          "profile"
        ];
      };
      type = "saas";
    };
    sovereign = {
      app_launcher_visible = true;
      auto_redirect_to_identity = false;
      destinations = [
        {
          type = "public";
          uri = "sovereign.rupan.dev";
        }
      ];
      domain = "sovereign.rupan.dev";
      enable_binding_cookie = false;
      http_only_cookie_attribute = true;
      name = "sovereign";
      options_preflight_bypass = false;
      policies = [
        {
          id = "\${cloudflare_zero_trust_access_policy.sovereign_owner.id}";
          precedence = 1;
        }
      ];
      type = "self_hosted";
    };
    tally_owner_dashboard = {
      app_launcher_visible = false;
      auto_redirect_to_identity = false;
      destinations = [
        {
          type = "public";
          uri = "tally.rupan.dev";
        }
      ];
      domain = "tally.rupan.dev";
      enable_binding_cookie = false;
      http_only_cookie_attribute = true;
      name = "Tally - owner dashboard";
      options_preflight_bypass = false;
      policies = [
        {
          id = "\${cloudflare_zero_trust_access_policy.main_rupan.id}";
          precedence = 1;
        }
      ];
      type = "self_hosted";
    };
    warp_login_app = {
      auto_redirect_to_identity = false;
      domain = "rupan.cloudflareaccess.com/warp";
      name = "Warp Login App";
      policies = [
        {
          id = "\${cloudflare_zero_trust_access_policy.main_rupan.id}";
          precedence = 1;
        }
      ];
      type = "warp";
    };
  };
  policies = {
    bypass = {
      decision = "bypass";
      include = [
        {
          everyone = {
          };
        }
      ];
      name = "Bypass";
      session_duration = "24h";
    };
    friends = {
      connection_rules = {
        rdp = {
        };
      };
      decision = "allow";
      include = [
        {
          email = {
            email = "AdityaParekh944@gmail.com";
          };
        }
        {
          email = {
            email = "blueeyedsharp@gmail.com";
          };
        }
        {
          email = {
            email = "rupanpandyan@gmail.com";
          };
        }
      ];
      name = "Friends";
      session_duration = "730h";
    };
    main_rupan = {
      decision = "allow";
      include = [
        {
          email = {
            email = "rupanpandyan@gmail.com";
          };
        }
      ];
      name = "Main - Rupan";
      session_duration = "24h";
    };
    roommates = {
      connection_rules = {
        rdp = {
        };
      };
      decision = "allow";
      include = [
        {
          email = {
            email = "blueeyedsharp@gmail.com";
          };
        }
        {
          email = {
            email = "jaybalu06@gmail.com";
          };
        }
        {
          email = {
            email = "rupanpandyan@gmail.com";
          };
        }
      ];
      name = "Roommates";
    };
    approvals_owner = {
      decision = "allow";
      include = [
        {
          email = {
            email = "rupanpandyan@gmail.com";
          };
        }
      ];
      name = "Allow Rupan";
    };
    sovereign_owner = {
      decision = "allow";
      include = [
        {
          email = {
            email = "rupanpandyan@gmail.com";
          };
        }
      ];
      name = "Allow owner";
      session_duration = "24h";
    };
  };
  imports = [
    {
      to = "cloudflare_zero_trust_access_application.bypass_music_sync_and_tools";
      id = "accounts/5d99d63dea23dde67ccb07e5dfb31107/b9183f48-492b-4bfc-99a8-afdb5918391f";
    }
    {
      to = "cloudflare_zero_trust_access_application.bypass_photos_files_and_dashboards";
      id = "accounts/5d99d63dea23dde67ccb07e5dfb31107/2903531b-6e4c-4204-ab79-e67fb8521730";
    }
    {
      to = "cloudflare_zero_trust_access_application.bypass_notes_and_project_sites";
      id = "accounts/5d99d63dea23dde67ccb07e5dfb31107/d467ead1-22e3-4a49-a854-1768d8d665c7";
    }
    {
      to = "cloudflare_zero_trust_access_application.bypass_home_assistant";
      id = "accounts/5d99d63dea23dde67ccb07e5dfb31107/a6cc9c1c-9a3b-46e7-8c39-c20fb986e13f";
    }
    {
      to = "cloudflare_zero_trust_access_application.bypass_websites_and_automation";
      id = "accounts/5d99d63dea23dde67ccb07e5dfb31107/e5f1d5ec-f2d3-47e1-aa29-5767d9ffb4c5";
    }
    {
      to = "cloudflare_zero_trust_access_application.bypass_flux_webhook";
      id = "accounts/5d99d63dea23dde67ccb07e5dfb31107/7019f991-5249-41d0-b732-812c8dc36233";
    }
    {
      to = "cloudflare_zero_trust_access_application.bypass_music_assistant_and_ledfx";
      id = "accounts/5d99d63dea23dde67ccb07e5dfb31107/fac24712-9b75-4206-9e5f-d6a22ab7c5dd";
    }
    {
      to = "cloudflare_zero_trust_access_application.bypass_arcade_controller";
      id = "accounts/5d99d63dea23dde67ccb07e5dfb31107/8300a16c-2cf7-4ad3-b7b5-103b2a333885";
    }
    {
      to = "cloudflare_zero_trust_access_application.bypass_uwagent";
      id = "accounts/5d99d63dea23dde67ccb07e5dfb31107/e4cfbb1b-45e6-4814-9350-eb7f2f0e4647";
    }
    {
      to = "cloudflare_zero_trust_access_application.bypass_woodpecker_ci";
      id = "accounts/5d99d63dea23dde67ccb07e5dfb31107/01a01c4d-56d2-4138-bbe4-7e49186b0193";
    }
    {
      to = "cloudflare_zero_trust_access_application.bypass_tally_tracker_endpoints";
      id = "accounts/5d99d63dea23dde67ccb07e5dfb31107/cb7b2fcc-9946-4497-94b3-abe5b4a69947";
    }
    {
      to = "cloudflare_zero_trust_access_application.bypass_requests_and_music_tools";
      id = "accounts/5d99d63dea23dde67ccb07e5dfb31107/292cbfd2-877a-47d6-88e4-ffccebe3d6a3";
    }
    {
      to = "cloudflare_zero_trust_access_application.tally_owner_dashboard";
      id = "accounts/5d99d63dea23dde67ccb07e5dfb31107/a534778f-88f4-45b6-9345-c2591e5a658a";
    }
    {
      to = "cloudflare_zero_trust_access_application.games";
      id = "accounts/5d99d63dea23dde67ccb07e5dfb31107/cc0a0604-7d43-4c9e-8ad7-d065a220bd53";
    }
    {
      to = "cloudflare_zero_trust_access_application.approvals";
      id = "accounts/5d99d63dea23dde67ccb07e5dfb31107/65a98a16-6466-40ca-9aa4-018794fd50bc";
    }
    {
      to = "cloudflare_zero_trust_access_application.forgejo";
      id = "accounts/5d99d63dea23dde67ccb07e5dfb31107/95ae142a-ff91-423c-b5b4-9a612536d016";
    }
    {
      to = "cloudflare_zero_trust_access_application.grafana_oidc";
      id = "accounts/5d99d63dea23dde67ccb07e5dfb31107/448b175a-dd78-4d6a-9847-8c122b3a9759";
    }
    {
      to = "cloudflare_zero_trust_access_application.immich_oidc";
      id = "accounts/5d99d63dea23dde67ccb07e5dfb31107/d694f134-6774-4247-80e2-19d0e32690eb";
    }
    {
      to = "cloudflare_zero_trust_access_application.sovereign";
      id = "accounts/5d99d63dea23dde67ccb07e5dfb31107/b6df265b-18b1-40b0-abfb-ff9fa6313b09";
    }
    {
      to = "cloudflare_zero_trust_access_application.default";
      id = "accounts/5d99d63dea23dde67ccb07e5dfb31107/da49f059-152b-41dc-9805-96dbcaaee6e3";
    }
    {
      to = "cloudflare_zero_trust_access_application.warp_login_app";
      id = "accounts/5d99d63dea23dde67ccb07e5dfb31107/2cadccb5-93c0-4df6-8a97-3806e10384fb";
    }
    {
      to = "cloudflare_zero_trust_access_policy.friends";
      id = "5d99d63dea23dde67ccb07e5dfb31107/ca3129d1-55ad-42ff-bac0-8f316ff84fcd";
    }
    {
      to = "cloudflare_zero_trust_access_policy.roommates";
      id = "5d99d63dea23dde67ccb07e5dfb31107/3cec368a-43c3-4c44-82e9-c1be0d6f3788";
    }
    {
      to = "cloudflare_zero_trust_access_policy.bypass";
      id = "5d99d63dea23dde67ccb07e5dfb31107/7015eaad-fad5-462b-905c-43d74bd648aa";
    }
    {
      to = "cloudflare_zero_trust_access_policy.main_rupan";
      id = "5d99d63dea23dde67ccb07e5dfb31107/d41ac5f5-7568-4a0d-a737-9968291d2fa2";
    }
    {
      to = "cloudflare_zero_trust_access_policy.approvals_owner";
      id = "5d99d63dea23dde67ccb07e5dfb31107/9537d0de-1429-420b-b8a9-3a3daaaf59cf";
    }
    {
      to = "cloudflare_zero_trust_access_policy.sovereign_owner";
      id = "5d99d63dea23dde67ccb07e5dfb31107/583b5994-6743-43c8-95b1-c146fed6618a";
    }
  ];
}
