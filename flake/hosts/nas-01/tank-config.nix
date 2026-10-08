{
  disko.devices = {
    disk = {
      tank = {
        type = "disk";
        device = "/dev/disk/by-id/scsi-35000c500d91a3f4f";
        content = {
          type = "gpt";
          partitions = {
            data = {
              size = "100%";
              content = {
                type = "zfs";
                pool = "tank";
              };
            };
          };
        };
      };
    };
    zpool.tank = {
      type = "zpool";
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
        media = {
          type = "zfs_fs";
          mountpoint = "/tank/media";
          options = {
            mountpoint = "legacy";
            recordsize = "1M";
          };
        };
        photos = {
          type = "zfs_fs";
          mountpoint = "/tank/photos";
          options = {
            mountpoint = "legacy";
            encryption = "aes-256-gcm";
            keyformat = "passphrase";
            keylocation = "file:///persist/keys/tank-photos.key";
          };
        };
        documents = {
          type = "zfs_fs";
          mountpoint = "/tank/documents";
          options = {
            mountpoint = "legacy";
            encryption = "aes-256-gcm";
            keyformat = "passphrase";
            keylocation = "file:///persist/keys/tank-documents.key";
          };
        };
        backups = {
          type = "zfs_fs";
          mountpoint = "/tank/backups";
          options.mountpoint = "legacy";
        };
        "backups/syncthing" = {
          type = "zfs_fs";
          mountpoint = "/tank/backups/syncthing";
          options = {
            mountpoint = "legacy";
            encryption = "aes-256-gcm";
            keyformat = "passphrase";
            keylocation = "file:///persist/keys/tank-syncthing.key";
          };
        };
        cluster = {
          type = "zfs_fs";
          mountpoint = "/tank/cluster";
          options.mountpoint = "legacy";
        };
        attic = {
          type = "zfs_fs";
          mountpoint = "/tank/attic";
          options.mountpoint = "legacy";
        };
        registry = {
          type = "zfs_fs";
          mountpoint = "/tank/registry";
          options.mountpoint = "legacy";
        };
        npm-cache = {
          type = "zfs_fs";
          mountpoint = "/tank/npm-cache";
          options.mountpoint = "legacy";
        };
        forgejo = {
          type = "zfs_fs";
          mountpoint = "/tank/forgejo";
          options.mountpoint = "legacy";
        };
        s3 = {
          type = "zfs_fs";
          mountpoint = "/tank/s3";
          options.mountpoint = "legacy";
        };
      };
    };
  };
}
