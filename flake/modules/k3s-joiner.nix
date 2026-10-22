{ config, lib, ... }:
let
  cfg = config.homelab.joiner;
in
{
  imports = [
    ./common-base.nix
    ./k3s-server.nix
    ./power-tuning.nix
  ];

  options.homelab.joiner = {
    enable = lib.mkEnableOption "k3s server joiner with small-node defaults";

    hostName = lib.mkOption {
      type = lib.types.str;
      description = "Hostname and Kubernetes node name for this joiner.";
    };

    primaryInterface = lib.mkOption {
      type = lib.types.str;
      description = "Physical interface attached to infrastructure VLAN 30.";
    };

    nodeIp = lib.mkOption {
      type = lib.types.str;
      description = "Stable VLAN 30 address used by this k3s server.";
    };

    serverAddress = lib.mkOption {
      type = lib.types.str;
      default = "https://10.0.30.11:6443";
      description = "Existing k3s API endpoint for joining this server.";
    };
  };

  config = lib.mkIf cfg.enable {
    networking.hostName = cfg.hostName;

    homelab.k3s = {
      enable = true;
      primaryInterface = cfg.primaryInterface;
      nodeIp = cfg.nodeIp;
      serverAddress = cfg.serverAddress;
    };

    services.k3s.extraFlags = [
      "--kubelet-arg=system-reserved=cpu=500m,memory=1Gi"
      "--kubelet-arg=kube-reserved=cpu=500m,memory=512Mi"
    ];
  };
}
