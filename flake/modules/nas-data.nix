{
  config,
  pkgs,
  lib,
  fleetInventory,
  ...
}:
let
  localBackupDatasets = [
    "tank/cluster"
    "tank/forgejo"
    "tank/backups"
    "tank/backups/syncthing"
    "tank/photos"
    "tank/documents"
    "tank/s3"
  ];
  # Rebuildable caches, metrics, logs and models stay out of the backup.
  localBackupExcludes = [
    "observability-prometheus-*"
    "observability-storage-loki-*"
    "voice-ollama-models-*"
    "voice-whisper-models-*"
    "immich-immich-model-cache-*"
    "media-clamav-db-*"
    "nix-nix-iso-data-*"
    "thumbs"
    "encoded-video"
  ];
in
{
  networking.firewall.allowedTCPPorts = [
    2049
  ];

  # The OPNsense API cert only carries DNS:OPNsense.internal (no IP SANs), so
  # CI jobs on this host must resolve that name to reach the firewall by TLS.
  # Forgejo webhook deliveries to Woodpecker must resolve ci.internal even
  # when systemd-resolved has degraded to the secondary resolver, which
  # carries no internal overrides (lookup failure drops the delivery with
  # no retry and the pipeline is never created).
  networking.hosts = {
    "192.168.1.1" = [ "OPNsense.internal" ];
    "10.0.30.14" = [ "ci.internal" ];
  };

  # tank/media and the media2 pool appear as one tree at the original path,
  # so NFS clients, Samba and the static PV are unchanged. New files go to the
  # branch with the most free space; link and rename clone the parent path
  # onto the source branch, so *arr hardlink imports keep working.
  fileSystems."/tank/media" = {
    device = "/mnt/media-tank:/mnt/media-2tb";
    fsType = "fuse.mergerfs";
    options = [
      "nofail"
      "category.create=mfs"
      "minfreespace=100G"
      "moveonenospc=true"
      # NFS reopens files by handle after FUSE forgets them and needs
      # inodes that stay stable when a file lands on another branch.
      "noforget"
      "inodecalc=path-hash"
      "fsname=media"
      "x-systemd.requires-mounts-for=/mnt/media-tank"
      "x-systemd.requires-mounts-for=/mnt/media-2tb"
    ];
  };
  system.fsPackages = [ pkgs.mergerfs ];

  fileSystems."/mnt/backup-local" = {
    device = "/dev/disk/by-id/ata-WDC_WD1003FBYZ-010FB0_WD-WCAW37790774-part1";
    fsType = "xfs";
    options = [
      "nofail"
      "x-systemd.device-timeout=30s"
    ];
  };

  # Samba passwords live outside Nix; set with smbpasswd once
  # (docs/runbooks/nas-game-share.md).
  users.users.sam = {
    isNormalUser = true;
    group = "users";
    description = "Sam (Game Streaming)";
  };

  users.users.aditya = {
    isNormalUser = true;
    group = "users";
    description = "Aditya (Game Downloads)";
  };

  services.samba = {
    enable = true;
    openFirewall = true;
    nmbd.enable = false;
    winbindd.enable = false;
    settings = {
      global = {
        "workgroup" = "WORKGROUP";
        "server string" = "nas-01";
        "netbios name" = "nas-01";
        "security" = "user";
        # Allow connections from clients LAN (VLAN 20), servers (VLAN 30), and management (VLAN 10)
        "hosts allow" = "10.0.20. 10.0.30. 10.0.10. 127.0.0.1 localhost";
        "hosts deny" = "0.0.0.0/0";
      };
      games = {
        path = "/tank/media/games";
        browseable = "yes";
        "read only" = "no";
        "guest ok" = "no";
        "valid users" = "sam rupan aditya";
        "create mask" = "0664";
        "directory mask" = "0775";
        "force user" = "rupan";
        "force group" = "users";
      };
    };
  };

  services.samba-wsdd = {
    enable = true;
    openFirewall = true;
  };

  systemd.services.samba-smbd.preStart = "mkdir -p -m 0700 /var/lib/samba/private";

  services.nfs.server = {
    enable = true;
    exports =
      let
        # Only Kubernetes nodes mount shared application state. AUTH_SYS still
        # trusts those nodes: the provisioner and existing root-owned service
        # data require UID 0. Do not broaden this exception to the whole VLAN.
        nodes = lib.filter (host: host.k3s or false) (lib.attrValues fleetInventory.hosts);
        nodeExports =
          extra:
          lib.concatMapStringsSep " " (
            host: "${host.address}(rw,sync,no_subtree_check,no_root_squash,mountpoint${extra})"
          ) nodes;
        # FUSE filesystems have no stable device id, so the mergerfs export
        # needs a fixed fsid or client handles go stale across remounts.
        mediaFsid = ",fsid=a04d0ea3-d3a5-4665-afb5-8543d0dcd404";
      in
      ''
        /tank/media ${nodeExports mediaFsid} 10.0.10.0/24(ro,sync,no_subtree_check,root_squash,mountpoint${mediaFsid})
        /tank/cluster ${nodeExports ""}
      '';
  };

  systemd.services.nfs-server = {
    wants = [
      "tank-media.mount"
      "tank-backups.mount"
      "tank-cluster.mount"
    ];
    after = [
      "tank-media.mount"
      "tank-backups.mount"
      "tank-cluster.mount"
    ];
  };

  services.sanoid = {
    enable = true;
    templates.personal = {
      hourly = 48;
      daily = 30;
      monthly = 12;
      autosnap = true;
      autoprune = true;
    };
    templates.operational = {
      daily = 30;
      autosnap = true;
      autoprune = true;
    };
    # Bulk media is replaceable; snapshots only cover recent mistakes. Unset
    # periods fall back to sanoid's defaults (48 hourly, 6 monthly), which
    # pinned months of replaced files and, with a full-library beets retag,
    # nearly filled tank on 2026-11-15.
    templates.media = {
      hourly = 0;
      daily = 7;
      monthly = 0;
      autosnap = true;
      autoprune = true;
    };
    datasets = {
      "tank/photos".useTemplate = [ "personal" ];
      "tank/documents".useTemplate = [ "personal" ];
      "tank/backups".useTemplate = [ "operational" ];
      "tank/backups/syncthing".useTemplate = [ "operational" ];
      "tank/cluster".useTemplate = [ "operational" ];
      "tank/forgejo".useTemplate = [ "operational" ];
      "tank/media".useTemplate = [ "media" ];
      "media2/media".useTemplate = [ "media" ];
      "tank/registry".useTemplate = [ "operational" ];
    };
  };

  systemd.services.nas-backup-local = {
    description = "Encrypted backup of irreplaceable tank data to the local backup disk";
    after = [
      "mnt-backup\\x2dlocal.mount"
      "tank-cluster.mount"
    ];
    requires = [
      "mnt-backup\\x2dlocal.mount"
      "tank-cluster.mount"
    ];
    unitConfig.RequiresMountsFor = map (dataset: "/${dataset}") localBackupDatasets ++ [
      "/mnt/backup-local"
    ];
    path = [
      pkgs.restic
      pkgs.zfs
      pkgs.coreutils
      pkgs.util-linux
    ];
    serviceConfig = {
      Type = "oneshot";
      PrivateTmp = true;
      CacheDirectory = "nas-backup-local";
      ExecStartPre = pkgs.writeShellScript "check-nas-backup-mounts" (
        builtins.readFile ../../scripts/nas/check-backup-mounts.sh
      );
    };
    script = ''
      export RESTIC_REPOSITORY=/mnt/backup-local/nas-restic
      export RESTIC_CACHE_DIR=$CACHE_DIRECTORY
      export RESTIC_PASSWORD_FILE=${config.sops.secrets.registry-restic-password.path}
      if [ ! -f "$RESTIC_REPOSITORY/config" ]; then
        restic init
      fi
      restic snapshots >/dev/null

      # A fixed snapshot name keeps restic source paths stable, so each run
      # finds its parent snapshot and skips unchanged files.
      snapshots=()
      sources=()
      for dataset in ${lib.escapeShellArgs localBackupDatasets}; do
        snapshots+=("$dataset@local-backup")
        sources+=("/$dataset/.zfs/snapshot/local-backup")
      done
      for snapshot in "''${snapshots[@]}"; do
        if zfs list -t snapshot "$snapshot" >/dev/null 2>&1; then
          zfs destroy "$snapshot"
        fi
      done
      zfs snapshot "''${snapshots[@]}"
      cleanup() {
        for snapshot in "''${snapshots[@]}"; do
          zfs destroy "$snapshot" || true
        done
      }
      trap cleanup EXIT

      restic backup --host nas-01 --tag nas-data --group-by host,tags \
        ${
          lib.concatMapStringsSep " " (pattern: "--exclude ${lib.escapeShellArg pattern}") localBackupExcludes
        } \
        "''${sources[@]}"
      restic forget --host nas-01 --tag nas-data --group-by host,tags \
        --prune --keep-daily 7 --keep-weekly 5 --keep-monthly 6
      restic check --read-data-subset=5%
    '';
  };
  systemd.timers.nas-backup-local = {
    description = "Daily encrypted backup of tank data to the local backup disk";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "daily";
      Persistent = true;
      RandomizedDelaySec = "30m";
    };
  };

  sops.secrets.registry-restic-password = { };

  systemd.services.registry-backup = {
    description = "Encrypted backup of the local container registry";
    after = [
      "tank-registry.mount"
      "mnt-backup\\x2dlocal.mount"
    ];
    requires = [
      "tank-registry.mount"
      "mnt-backup\\x2dlocal.mount"
    ];
    serviceConfig = {
      Type = "oneshot";
      User = "root";
      Group = "root";
      PrivateTmp = true;
      ProtectSystem = "strict";
      ReadWritePaths = [
        "/mnt/backup-local/registry-restic"
        "/persist/zot/status"
      ];
    };
    path = [
      pkgs.restic
      pkgs.zfs
      pkgs.coreutils
    ];
    script = ''
      export RESTIC_REPOSITORY=/mnt/backup-local/registry-restic
      export RESTIC_PASSWORD_FILE=${config.sops.secrets.registry-restic-password.path}
      if [ ! -f "$RESTIC_REPOSITORY/config" ]; then
        restic init
      fi
      restic snapshots >/dev/null

      snapshot="tank/registry@registry-backup-$(date +%Y%m%dT%H%M%S)"
      zfs snapshot "$snapshot"
      cleanup() {
        zfs destroy "$snapshot" || true
      }
      trap cleanup EXIT

      # Timestamped snapshot paths are not a stable retention identity. Tag new
      # backups and leave legacy snapshots outside this policy for audited cleanup.
      restic backup --host nas-01 --tag registry-v2 --group-by host,tags \
        "/tank/registry/.zfs/snapshot/''${snapshot#*@}" /persist/zot /var/lib/acme
      restic forget --host nas-01 --tag registry-v2 --group-by host,tags \
        --prune --keep-daily 7 --keep-weekly 5 --keep-monthly 6
      restic check --read-data-subset=5%
      date +%s > /persist/zot/status/backup-last-success.tmp
      sync -f /persist/zot/status/backup-last-success.tmp
      mv /persist/zot/status/backup-last-success.tmp /persist/zot/status/backup-last-success
      sync -f /persist/zot/status
    '';
  };
  systemd.timers.registry-backup = {
    description = "Daily encrypted registry backup";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "daily";
      Persistent = true;
      RandomizedDelaySec = "30m";
    };
  };

  systemd.tmpfiles.rules = [
    "d /persist/keys 0700 root root -"
    "d /mnt/backup-local/registry-restic 0700 zot zot -"
    "d /mnt/backup-local/nas-restic 0700 root root -"
    "d /tank/media/games 0775 rupan users -"
  ];

  environment.persistence."/persist".directories = [
    "/var/lib/nfs"
    "/var/lib/samba"
  ];

  environment.systemPackages = with pkgs; [
    restic
    rsync
  ];
}
