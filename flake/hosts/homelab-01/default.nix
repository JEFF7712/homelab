{
  imports = [
    ./disk-config.nix
    ./hardware-configuration.nix
    ../../modules/common-base.nix
    ../../modules/k3s-server.nix
    ../../modules/agent-workspaces.nix
    ../../modules/agent-workspace-network.nix
  ];

  networking.hostName = "homelab-01";

  homelab.k3s = {
    enable = true;
    primaryInterface = "eno1";
    nodeIp = "10.0.30.11";
    clusterInit = true;
    bootstrapCilium = true;
  };

  services.k3s.extraFlags = [
    "--kubelet-arg=system-reserved=cpu=2,memory=10Gi"
    "--kubelet-arg=kube-reserved=cpu=500m,memory=1Gi"
  ];

  services.agent-workspaces.enable = true;
  services.agent-workspace-network = {
    enable = true;
    recoveryManifest = {
      schema_version = 1;
      workspaces = [
        {
          id = "rupan";
          enabled = true;
          host = "homelab-01";
          network = {
            segment = "172.31.10.0/30";
            address = "172.31.10.2/30";
            gateway = "172.31.10.1";
            bridge = "aw-rupan-br";
            tap = "aw-rupan-tap";
            mac = "02:00:00:10:00:02";
            uplink = "eno1";
            ha_api = null;
            dns = [ "1.1.1.1" ];
            ntp = [ ];
            ingress_sources = [
              "10.0.10.0/24"
              "10.0.20.0/24"
              "10.0.30.10/32"
              "100.94.191.33/32"
            ];
          };
        }
        {
          id = "sam";
          enabled = true;
          host = "homelab-01";
          network = {
            segment = "172.31.10.4/30";
            address = "172.31.10.6/30";
            gateway = "172.31.10.5";
            bridge = "aw-sam-br";
            tap = "aw-sam-tap";
            mac = "02:00:00:10:00:06";
            uplink = "eno1";
            ha_api = null;
            dns = [ "1.1.1.1" ];
            ntp = [ ];
            ingress_sources = [
              "10.0.10.0/24"
              "10.0.20.0/24"
              "10.0.30.10/32"
              "100.94.191.33/32"
            ];
          };
        }
      ];
    };
  };
}
