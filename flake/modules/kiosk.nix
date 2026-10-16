{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.kiosk;
  # First view is the boot URL; when more than one view is set the rotation
  # service advances the single Chromium through the rest over DevTools.
  bootUrl = if cfg.views == [ ] then cfg.url else (lib.head cfg.views).url;
  rotate = builtins.length cfg.views > 1;
  viewsFile = pkgs.writeText "kiosk-views.json" (builtins.toJSON cfg.views);
  kioskBrowser = pkgs.writeShellScript "kiosk-browser" ''
    ${lib.concatMapStringsSep "\n" (
      output: "${pkgs.wlr-randr}/bin/wlr-randr --output ${output} --off || true"
    ) cfg.disableOutputs}
    exec ${pkgs.chromium}/bin/chromium \
      --ozone-platform=wayland \
      --enable-features=UseOzonePlatform \
      --no-first-run \
      --noerrdialogs \
      --disable-infobars \
      --disable-features=Translate,OverlayScrollbar \
      --check-for-update-interval=31536000 \
      --password-store=basic \
      --disable-session-crashed-bubble \
      --remote-debugging-port=9222 \
      --remote-allow-origins=* \
      --force-device-scale-factor=${cfg.scaleFactor} \
      --app="${bootUrl}"
  '';
in
{
  options.homelab.kiosk = {
    enable = lib.mkEnableOption "homelab kiosk display using cage and chromium";

    url = lib.mkOption {
      type = lib.types.str;
      default = "http://10.0.40.12/d/efa86fd1d0c121a26444b636a3f509a8?kiosk";
      description = "URL to display on the kiosk screen. Ignored when 'views' is non-empty.";
    };

    views = lib.mkOption {
      type = lib.types.listOf (
        lib.types.submodule {
          options = {
            url = lib.mkOption {
              type = lib.types.str;
              description = "URL shown for this view.";
            };
            duration = lib.mkOption {
              type = lib.types.ints.positive;
              default = 60;
              description = "Seconds to keep this view on screen before advancing.";
            };
            css = lib.mkOption {
              type = lib.types.nullOr lib.types.str;
              default = null;
              description = "CSS injected after each navigation, for example to hide the site's own navigation chrome.";
            };
          };
        }
      );
      default = [ ];
      description = ''
        Ordered views to rotate through on the kiosk display. When two or more
        are set, the kiosk opens the first and cycles the rest on a timer; when
        empty, the single 'url' is shown with no rotation.
      '';
    };

    drmDevice = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = "/dev/dri/card1";
      description = "DRM device for cage/wlroots to bind to";
    };

    user = lib.mkOption {
      type = lib.types.str;
      default = "kiosk";
      description = "System user to run cage and browser under";
    };

    scaleFactor = lib.mkOption {
      type = lib.types.str;
      default = "1.0";
      description = "Device scale factor for Chromium";
    };

    disableOutputs = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = "List of output names to disable via wlr-randr before launching Chromium";
    };

    haTokenFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "File holding a Home Assistant long-lived token for the kiosk face. The tmpfs root wipes the Chromium profile every reboot, so when set, a boot service re-seeds the token into the kiosk browser over its local DevTools port. The file must live under /persist and is never part of the store closure.";
    };
  };

  config = lib.mkIf cfg.enable {
    users.users.${cfg.user} = {
      isNormalUser = true;
      uid = 1001;
      group = cfg.user;
      extraGroups = [
        "video"
        "input"
        "audio"
      ];
      createHome = true;
      home = "/home/${cfg.user}";
    };
    users.groups.${cfg.user} = {
      gid = 1001;
    };

    fonts.packages = with pkgs; [
      dejavu_fonts
      freefont_ttf
      noto-fonts
      noto-fonts-color-emoji
    ];

    services.cage = {
      enable = true;
      user = cfg.user;
      extraArguments = [ "-s" ];
      environment = {
        WLR_LIBINPUT_NO_DEVICES = "1";
        WLR_NO_HARDWARE_CURSORS = "1";
        XDG_RUNTIME_DIR = "/run/user/1001";
      }
      // (lib.optionalAttrs (cfg.drmDevice != null) {
        WLR_DRM_DEVICES = cfg.drmDevice;
      });
      program = kioskBrowser;
    };

    systemd.services."cage-tty1" = {
      wantedBy = [ "multi-user.target" ];
      restartIfChanged = lib.mkForce true;
      serviceConfig = {
        Restart = "always";
        RestartSec = "3s";
      };
    };

    # Cycle the single Chromium through cfg.views over its DevTools port. Only
    # meaningful with more than one view; a single view needs no driver.
    systemd.services."kiosk-rotate" = lib.mkIf rotate {
      description = "Rotate the kiosk display through its configured views";
      after = [ "cage-tty1.service" ];
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        User = cfg.user;
        Restart = "always";
        RestartSec = "10s";
      };
      path = [ pkgs.python3 ];
      script = ''
        exec ${pkgs.python3}/bin/python3 ${./kiosk-rotate.py} \
          --cdp http://127.0.0.1:9222 \
          --views-file ${viewsFile}
      '';
    };

    systemd.services."kiosk-ha-token-seed" = lib.mkIf (cfg.haTokenFile != null) {
      description = "Seed Home Assistant token into kiosk browser";
      after = [ "cage-tty1.service" ];
      wantedBy = [ "multi-user.target" ];
      # Parked while no display is connected (all DRM outputs on homelab-05
      # disconnected since 2026-09-25; last successful seed that day). The
      # seeder fails without a Jarvis page target, and any failed unit marks
      # every nixos-rebuild switch failed (status 4), reddening fleet
      # deploys. To re-enable after reconnecting the display, delete this
      # line, redeploy, and the boot-time seeding resumes automatically.
      enable = false;
      unitConfig.ConditionPathExists = [ cfg.haTokenFile ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        Restart = "on-failure";
        RestartSec = "30s";
      };
      path = [ pkgs.python3 ];
      script = ''
        exec ${pkgs.python3}/bin/python3 ${./kiosk-ha-token-seed.py} \
          --token-file ${cfg.haTokenFile}
      '';
    };

    systemd.services."getty@tty1".enable = false;
    systemd.services."autovt@tty1".enable = false;

    boot.kernelParams = [ "consoleblank=0" ];
  };
}
