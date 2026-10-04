{ pkgs, lib, ... }:
{
  networking.firewall.allowedTCPPorts = [
    2049
    8080
  ];

  # The OPNsense API cert only carries DNS:OPNsense.internal (no IP SANs), so
  # CI jobs on this host must resolve that name to reach the firewall by TLS.
  networking.hosts = {
    "192.168.1.1" = [ "OPNsense.internal" ];
  };

  fileSystems."/mnt/backup-2tb" = {
    device = "/dev/disk/by-id/ata-ST2000DM008-2FR102_ZFL60NJG-part1";
    fsType = "xfs";
    options = [
      "nofail"
      "x-systemd.device-timeout=30s"
    ];
  };

  services.nfs.server = {
    enable = true;
    exports = ''
      /tank/media     10.0.30.0/24(rw,sync,no_subtree_check,no_root_squash,mountpoint) 10.0.10.0/24(ro,sync,no_subtree_check,mountpoint)
      /tank/backups   10.0.30.0/24(rw,sync,no_subtree_check,mountpoint)
      /tank/cluster   10.0.30.0/24(rw,sync,no_subtree_check,no_root_squash,mountpoint)
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
    templates.weekly = {
      daily = 7;
      autosnap = true;
      autoprune = true;
    };
    datasets = {
      "tank/photos".useTemplate = [ "personal" ];
      "tank/documents".useTemplate = [ "personal" ];
      "tank/backups".useTemplate = [ "operational" ];
      "tank/backups/syncthing".useTemplate = [ "operational" ];
      "tank/cluster".useTemplate = [ "operational" ];
      "tank/media".useTemplate = [ "weekly" ];
      "tank/attic".useTemplate = [ "weekly" ];
      "tank/registry".useTemplate = [ "operational" ];
    };
  };

  users.users.atticd = {
    isSystemUser = true;
    group = "atticd";
  };
  users.groups.atticd = { };

  systemd.services.atticd = {
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    unitConfig.RequiresMountsFor = [
      "/tank/attic"
      "/persist/attic"
    ];
    serviceConfig = {
      DynamicUser = lib.mkForce false;
      User = "atticd";
      Group = "atticd";
      TimeoutStartSec = "15min";
      ReadWritePaths = [ "/persist/attic" ];
      ExecStartPre = [
        "+${pkgs.writeShellScript "ensure-attic-storage-permissions" ''
          set -eu
          ${pkgs.coreutils}/bin/install -d -m 0750 -o atticd -g atticd /tank/attic
          ${pkgs.coreutils}/bin/chown -R atticd:atticd /tank/attic
          ${pkgs.coreutils}/bin/install -d -m 0700 -o atticd -g atticd /persist/attic
          if [ -f /persist/attic/server.db ]; then
            ${pkgs.coreutils}/bin/chown atticd:atticd /persist/attic/server.db
            ${pkgs.coreutils}/bin/chmod 0600 /persist/attic/server.db
          fi
        ''}"
      ];
    };
  };

  services.atticd = {
    enable = true;
    environmentFile = "/persist/attic/env";
    settings = {
      listen = "[::]:8080";
      storage = {
        type = "local";
        path = "/tank/attic";
      };
      database = {
        url = "sqlite:///persist/attic/server.db?mode=rwc";
      };
      # Weekly sweep; objects neither created nor accessed within 90 days are
      # collected. 90d comfortably exceeds the consumers' GC windows (laptop
      # nh clean 7d, nas nix GC 30d). Monolithic mode runs this on interval;
      # garbage-collector-once covers one-shot runs.
      garbage-collection = {
        interval = "7 days";
        default-retention-period = "90 days";
      };
    };
  };

  systemd.services.nas-backup-2tb = {
    description = "Copy photos and documents to the independent 2 TB disk";
    after = [
      "mnt-backup\\x2d2tb.mount"
      "tank-cluster.mount"
    ];
    requires = [
      "mnt-backup\\x2d2tb.mount"
      "tank-cluster.mount"
    ];
    unitConfig.RequiresMountsFor = [
      "/tank/photos"
      "/tank/documents"
      "/mnt/backup-2tb"
    ];
    path = [ pkgs.util-linux ];
    serviceConfig = {
      Type = "oneshot";
      ExecStartPre = pkgs.writeShellScript "check-nas-backup-mounts" (
        builtins.readFile ../../scripts/nas/check-backup-mounts.sh
      );
      ExecStart = pkgs.writeShellScript "nas-backup-2tb" ''
        set -eu
        immich_dir=$(${pkgs.findutils}/bin/find /tank/cluster -maxdepth 1 -name "immich-immich-library-pvc-*" | head -n 1)
        if [ -n "$immich_dir" ] && [ -d "$immich_dir" ]; then
          ${pkgs.rsync}/bin/rsync -a --delete "$immich_dir/" /mnt/backup-2tb/photos/
        elif [ -d /tank/photos ]; then
          ${pkgs.rsync}/bin/rsync -a --delete /tank/photos/ /mnt/backup-2tb/photos/
        fi
        if [ -d /tank/documents ]; then
          ${pkgs.rsync}/bin/rsync -a --delete /tank/documents/ /mnt/backup-2tb/documents/
        fi
      '';
    };
  };
  systemd.timers.nas-backup-2tb = {
    description = "Daily copy of photos and documents to the 2 TB disk";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "daily";
      Persistent = true;
    };
  };

  systemd.services.registry-backup = {
    description = "Encrypted backup of the local container registry";
    after = [
      "tank-registry.mount"
      "mnt-backup\\x2d2tb.mount"
    ];
    requires = [
      "tank-registry.mount"
      "mnt-backup\\x2d2tb.mount"
    ];
    unitConfig.ConditionPathExists = "/persist/zot/restic-password";
    serviceConfig = {
      Type = "oneshot";
      User = "root";
      Group = "root";
      PrivateTmp = true;
      ProtectSystem = "strict";
      ReadWritePaths = [
        "/mnt/backup-2tb/registry-restic"
        "/persist/zot/status"
      ];
    };
    path = [ pkgs.restic ];
    script = ''
      export RESTIC_REPOSITORY=/mnt/backup-2tb/registry-restic
      export RESTIC_PASSWORD_FILE=/persist/zot/restic-password
      if [ ! -f "$RESTIC_REPOSITORY/config" ]; then
        restic init
      fi
      restic snapshots >/dev/null
      restic backup --exclude=/persist/zot/restic-password /tank/registry /persist/zot /var/lib/acme
      restic forget --prune --keep-daily 7 --keep-weekly 5 --keep-monthly 6
      restic check --read-data-subset=1/20
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
    # atticd (not root) owns this dir: the atticd ExecStartPre ensure script
    # enforces the same ownership, and sqlite needs directory write access
    # for its WAL files. A root-owned rule here revokes access on every
    # switch without an atticd restart, which surfaces as HTTP 500s with
    # "unable to open database file".
    "d /persist/attic 0700 atticd atticd -"
    "d /persist/keys 0700 root root -"
    "d /mnt/backup-2tb/registry-restic 0700 zot zot -"
    "d /mnt/backup-2tb/photos 0755 root root -"
    "d /mnt/backup-2tb/documents 0755 root root -"
  ];

  environment.persistence."/persist".directories = [
    "/var/lib/nfs"
  ];

  environment.systemPackages = with pkgs; [
    restic
    rsync
  ];
}
