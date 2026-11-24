# Offsite copy of the homelab's external backup repository. adguard-netbird-01
# runs `restic copy` into this append-only REST server (immich-offsite-copy in
# flake/modules/adguard-netbird/backups.nix): the homelab can add snapshots but
# never delete or rewrite them, and the data stays encrypted with the homelab
# repository key, so this host never sees plaintext.
{ lib, ... }:
let
  dataDir = "/home/.offsite-backup";
in
{
  services.restic.server = {
    enable = true;
    appendOnly = true;
    inherit dataDir;
    # Reachable only over NetBird (wt0 below), where the policy
    # homelab-to-home-pc-01 admits adguard-netbird-01 alone.
    extraFlags = [ "--no-auth" ];
  };
  networking.firewall.interfaces.wt0.allowedTCPPorts = [ 8000 ];

  systemd.services.restic-rest-server = {
    # /home is nofail: never write the repository to the root filesystem.
    unitConfig.RequiresMountsFor = [ dataDir ];
    # The upstream unit hides /home entirely; show the service an empty /home
    # with only the repository bound in, never the family's home directories.
    serviceConfig = {
      ProtectHome = lib.mkForce "tmpfs";
      BindPaths = [ dataDir ];
    };
  };
  systemd.tmpfiles.rules = [ "d ${dataDir} 0700 restic restic -" ];
}
