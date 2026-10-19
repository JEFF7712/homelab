{
  disko.devices = {
    disk = {
      system-a = {
        type = "disk";
        device = "/dev/disk/by-id/nvme-WDC_PC_SN520_SDAPNUW-256G-1006_2022C1800396";
        content = {
          type = "gpt";
          partitions = {
            ESP = {
              size = "1G";
              type = "EF00";
              content = {
                type = "filesystem";
                format = "vfat";
                mountpoint = "/boot";
                mountOptions = [ "umask=0077" ];
              };
            };
            luks = {
              size = "100%";
              content = {
                type = "luks";
                name = "nas-system-a";
                passwordFile = "/tmp/nas-01-system.key";
                settings = {
                  allowDiscards = true;
                  crypttabExtraOpts = [ "tpm2-device=auto" ];
                };
                content = {
                  type = "zfs";
                  pool = "zroot";
                };
              };
            };
          };
        };
      };
      system-b = {
        type = "disk";
        device = "/dev/disk/by-id/nvme-WDC_PC_SN520_SDAPNUW-256G-1006_2022BA804857";
        content = {
          type = "gpt";
          partitions = {
            ESP = {
              size = "1G";
              type = "EF00";
              content = {
                type = "filesystem";
                format = "vfat";
                mountpoint = "/boot-fallback";
                mountOptions = [ "umask=0077" ];
              };
            };
            luks = {
              size = "100%";
              content = {
                type = "luks";
                name = "nas-system-b";
                passwordFile = "/tmp/nas-01-system.key";
                settings = {
                  allowDiscards = true;
                  crypttabExtraOpts = [ "tpm2-device=auto" ];
                };
                content = {
                  type = "zfs";
                  pool = "zroot";
                };
              };
            };
          };
        };
      };
    };
    zpool.zroot = {
      type = "zpool";
      mode = "mirror";
      options = {
        ashift = "12";
        autotrim = "on";
      };
      rootFsOptions = {
        acltype = "posixacl";
        atime = "off";
        compression = "zstd";
        mountpoint = "none";
        xattr = "sa";
      };
      datasets = {
        nix = {
          type = "zfs_fs";
          mountpoint = "/nix";
          options.mountpoint = "legacy";
        };
        persist = {
          type = "zfs_fs";
          mountpoint = "/persist";
          options.mountpoint = "legacy";
        };
        log = {
          type = "zfs_fs";
          mountpoint = "/var/log";
          options.mountpoint = "legacy";
        };
        # The Nix binary cache serves many small random reads to every CI
        # container. On the single tank HDD, shared with media and NFS, it
        # saturated the disk (IO pressure above 80% full) and turned a one
        # minute CI step into fifteen. The quota keeps the cache from
        # crowding the system pool; Attic's own GC bounds its growth.
        attic = {
          type = "zfs_fs";
          mountpoint = "/srv/attic";
          options = {
            mountpoint = "legacy";
            quota = "120G";
          };
        };
      };
    };
  };
}
