{ lib, ... }:
let
  sshKeys = import ../../modules/fleet-ssh-keys.nix;
  # This host is a remote NetBird peer, not on the LAN. CI (homelab-04) and the
  # agent VM behind it reach it only through the home-pc-01-ssh relay on
  # adguard-netbird-01 (10.0.30.10:2201), which connects from that peer's mesh
  # address; the NetBird policy homelab-to-home-pc-01 admits it on TCP 22.
  homelabMeshSource = "100.94.249.145";
in
{
  services.openssh = {
    enable = true;
    settings = {
      KbdInteractiveAuthentication = false;
      PasswordAuthentication = false;
      PermitRootLogin = "no";
    };
  };

  # CI activates with --use-remote-sudo as rupan.
  security.sudo.wheelNeedsPassword = false;
  nix.settings.trusted-users = [
    "root"
    "@wheel"
  ];

  users.users.rupan.openssh.authorizedKeys.keys = sshKeys.operatorKeys ++ [
    ''restrict,from="${homelabMeshSource}" ${lib.fileContents ../../../config/ci/fleet-deploy.pub}''
  ];

  # Read-only observer, as on the LAN fleet (see common-base.nix).
  users.groups.agent-observer = { };
  users.users.agent-observer = {
    isNormalUser = true;
    group = "agent-observer";
    extraGroups = [ "systemd-journal" ];
    openssh.authorizedKeys.keys = [
      ''restrict,pty,from="${homelabMeshSource}" ${sshKeys.observerKey}''
      ''restrict,pty,from="100.64.0.0/10" ${sshKeys.laptopObserverKey}''
    ];
  };
}
