{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.syncthing;
in
{
  options.homelab.syncthing = {
    enable = lib.mkEnableOption "Syncthing backup receiver on nas-01";
    guiAddress = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1:8384";
      description = "Web GUI listen address on nas-01 (loopback only; access via SSH tunnel).";
    };
    storagePath = lib.mkOption {
      type = lib.types.path;
      default = "/tank/backups/syncthing";
      description = "Root path where synced files are stored.";
    };
  };

  config = lib.mkIf cfg.enable {
    # Restrict Syncthing sync ports (22000 TCP/UDP) and discovery (21027 UDP) to LAN and NetBird subnets.
    # Note: nas-01 receives NetBird traffic (100.64.0.0/10) routed via adguard-netbird-01 into 10.0.30.0/24.
    networking.firewall.extraInputRules = ''
      ip saddr { 10.0.10.0/24, 10.0.30.0/24, 100.64.0.0/10 } tcp dport 22000 accept
      ip saddr { 10.0.10.0/24, 10.0.30.0/24, 100.64.0.0/10 } udp dport 22000 accept
      ip saddr { 10.0.10.0/24, 10.0.30.0/24, 100.64.0.0/10 } udp dport 21027 accept
    '';
    # Web GUI is bound strictly to loopback (127.0.0.1:8384). No external firewall port is opened.

    users.users.syncthing = {
      isSystemUser = true;
      group = "syncthing";
      home = lib.mkForce "/persist/syncthing";
    };
    users.groups.syncthing = { };

    systemd.tmpfiles.rules = [
      "d /persist/syncthing 0700 syncthing syncthing -"
      "d ${cfg.storagePath} 0750 syncthing syncthing -"
      "d ${cfg.storagePath}/laptop 0750 syncthing syncthing -"
    ];

    services.syncthing = {
      enable = true;
      user = "syncthing";
      group = "syncthing";
      dataDir = cfg.storagePath;
      configDir = "/persist/syncthing";
      guiAddress = cfg.guiAddress;
      openDefaultPorts = false; # Explicitly configured above
      overrideDevices = true;
      overrideFolders = true;
      settings = {
        options = {
          urAccepted = -1; # Disable telemetry / usage reporting
          crashReportingEnabled = false; # Disable crash reports
          globalAnnounceEnabled = false; # Disable public announce servers
          localAnnounceEnabled = true; # LAN multicast discovery
          relaysEnabled = false; # Disable public relay servers
          natEnabled = false; # Disable UPnP / NAT-PMP port mapping
        };
        devices = {
          "laptop-nixos" = {
            id = "4LT3RLW-PVXTJAA-LBRBUSK-JDAEAXN-JH735IR-FDVT54T-Z4MQ6YH-JAVXZQU";
            addresses = [ "dynamic" ];
            autoAcceptFolders = false;
          };
        };
        folders =
          let
            mkBackupFolder = id: subpath: {
              inherit id;
              path = "${cfg.storagePath}/laptop/${subpath}";
              devices = [ "laptop-nixos" ];
              type = "receiveonly";
              versioning = {
                type = "staggered";
                params = {
                  cleanInterval = "3600";
                  maxAge = "2592000"; # 30 days
                };
              };
            };
          in
          {
            "laptop-documents-personal" = mkBackupFolder "laptop-documents-personal" "documents";
            "laptop-documents-apps" = mkBackupFolder "laptop-documents-apps" "Documents";
            "laptop-projects" = mkBackupFolder "laptop-projects" "projects";
            "laptop-code" = mkBackupFolder "laptop-code" "code";
            "laptop-obsidian" = mkBackupFolder "laptop-obsidian" "obsidian";
            "laptop-school" = mkBackupFolder "laptop-school" "school";
            "laptop-businesses" = mkBackupFolder "laptop-businesses" "businesses";
            "laptop-pictures" = mkBackupFolder "laptop-pictures" "Pictures";
            "laptop-videos" = mkBackupFolder "laptop-videos" "Videos";
            "laptop-research" = mkBackupFolder "laptop-research" "research";
            "laptop-homelab" = mkBackupFolder "laptop-homelab" "homelab";
            "laptop-nixos" = mkBackupFolder "laptop-nixos" "nixos";
          };
      };
    };

    systemd.services.syncthing = {
      after = [ "tank-backups-syncthing.mount" ];
      requires = [ "tank-backups-syncthing.mount" ];
      unitConfig.RequiresMountsFor = [
        "/persist/syncthing"
        cfg.storagePath
      ];
      serviceConfig = {
        DynamicUser = lib.mkForce false;
        User = "syncthing";
        Group = "syncthing";
        ReadWritePaths = [
          "/persist/syncthing"
          cfg.storagePath
        ];
        ExecStartPre = [
          "+${pkgs.writeShellScript "ensure-syncthing-permissions" ''
            set -eu
            ${pkgs.coreutils}/bin/install -d -m 0700 -o syncthing -g syncthing /persist/syncthing
            ${pkgs.coreutils}/bin/chown -R syncthing:syncthing /persist/syncthing
            ${pkgs.coreutils}/bin/install -d -m 0750 -o syncthing -g syncthing ${cfg.storagePath}
            ${pkgs.coreutils}/bin/install -d -m 0750 -o syncthing -g syncthing ${cfg.storagePath}/laptop
            ${pkgs.coreutils}/bin/chown syncthing:syncthing ${cfg.storagePath} ${cfg.storagePath}/laptop
          ''}"
        ];
      };
    };
  };
}
