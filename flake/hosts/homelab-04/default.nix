{
  config,
  fleetInventory,
  pkgs,
  ...
}:
let
  workspaces = config.services.agent-workspaces;
  reservedCpu = 1 + workspaces.aggregateCpuPercent / 100;
  reservedMemoryMiB = 2048 + workspaces.aggregateMemoryMaxMiB;
in
{
  imports = [
    ./hardware-configuration.nix
    ../../modules/common-base.nix
    ../../modules/disko-single-disk.nix
    ../../modules/k3s-server.nix
    ../../modules/k3s-registry-client.nix
    ../../modules/kiosk.nix
    ../../modules/monero.nix
    ../../modules/nvidia.nix
    ../../modules/github-runner-nixos.nix
    ../../modules/woodpecker.nix
    ../../modules/approvals.nix
    ../../profiles/projectctl-pilot.nix
    ../../modules/gitlab-dr.nix
    ../../modules/platform-backup.nix
    ../../modules/repo-monitor.nix
    ../../modules/release-mirror.nix
    ../../modules/agent-workspaces.nix
    ../../modules/agent-workspace-network.nix
  ];

  networking.hostName = "homelab-04";

  # A boot service avoids restarting networkd and its dependent guests.
  systemd.services.onboard-ethernet-offloads = {
    description = "Disable segmentation offloads after the e1000e transmit hang";
    wantedBy = [ "multi-user.target" ];
    requires = [ "sys-subsystem-net-devices-enp0s31f6.device" ];
    after = [
      "local-fs.target"
      "sys-subsystem-net-devices-enp0s31f6.device"
    ];
    before = [
      "network-pre.target"
      "shutdown.target"
    ];
    conflicts = [ "shutdown.target" ];
    unitConfig = {
      DefaultDependencies = false;
      ConditionPathExists = "/sys/bus/pci/drivers/e1000e/0000:00:1f.6/net/enp0s31f6";
    };
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = "${pkgs.ethtool}/bin/ethtool -K enp0s31f6 tso off gso off";
    };
  };

  sops.defaultSopsFile = ../../../secrets/hosts/homelab-04.sops.yaml;
  sops.secrets.kiosk-ha-token.restartUnits = [ "kiosk-rotate.service" ];

  homelab.kiosk = {
    enable = true;
    url = "http://10.0.40.12/d/homelab-fleet-overview?kiosk";
    drmDevice = "/dev/dri/card1";
    scaleFactor = "1.0";
    views = [
      {
        url = "http://10.0.40.12/d/homelab-fleet-overview?kiosk";
        duration = 60;
      }
      {
        url = "http://10.0.40.13:8123/3d-printing";
        duration = 60;
        # Home Assistant renders its own sidebar and top bar; strip them so the
        # dashboard fills the panel like the Grafana view does.
        css = ''
          ha-sidebar, .header, ha-top-app-bar, ha-menu-button { display: none !important; }
          #view { padding-top: 0 !important; margin-left: 0 !important; }
        '';
      }
    ];
    # Home Assistant is behind a trusted-proxy config, so the dashboard cannot
    # borrow the Jarvis trusted-network bypass. Instead seed the browser's own
    # session from a long-lived access token, re-applied after each boot.
    haDashboardTokenFile = config.sops.secrets.kiosk-ha-token.path;
    haDashboardUrl = "http://10.0.40.13:8123";
  };

  homelab.woodpecker.enable = true;
  homelab.approvals.enable = true;
  homelab.platformBackup.enable = true;

  homelab.releaseMirror = {
    enable = true;
    repository = "JEFF7712/nix-agent";
    minimumVersion = "0.13.0";
  };

  # CI-driven mirror: the homelab GitHub branch carries GitHub-only commits,
  # so only ancestry of the Forgejo head is verified, not SHA equality.
  homelab.repoMonitor = {
    enable = true;
    specialMirrors = [
      {
        repository = "JEFF7712/homelab";
        githubRepository = "JEFF7712/homelab";
        defaultBranch = "main";
      }
    ];
  };

  homelab.monero.enable = true;

  # System partition (ESP is part1) holding the btrfs /persist subvolume.
  homelab.monero.ioDevice = "/dev/disk/by-id/nvme-PC_SN810_NVMe_WDC_1024GB_230907801780-part2";

  homelab.disk.device = "/dev/disk/by-id/nvme-PC_SN810_NVMe_WDC_1024GB_230907801780";

  homelab.k3s = {
    enable = true;
    role = "agent";
    primaryInterface = "enp0s31f6";
    nodeIp = fleetInventory.hosts.homelab-04.address;
    serverAddress = fleetInventory.k3sEndpoint;
  };

  services.k3s.extraFlags = [
    "--kubelet-arg=system-reserved=cpu=${toString reservedCpu},memory=${toString reservedMemoryMiB}Mi"
    "--kubelet-arg=kube-reserved=cpu=500m,memory=1Gi"
  ];

  services.agent-workspaces = {
    enable = true;
    aggregateMemoryMaxMiB = 14336;
  };
  services.agent-workspace-network = {
    enable = true;
    # Rupan's personal VM is a full development environment: Forgejo Git and
    # API, the signed cache, Woodpecker, the Kubernetes API (kubernetes_api
    # below), and SSH to every fleet host, as `just doctor --live` and
    # `just ship` require.
    tcpEgress.rupan = [
      {
        address = fleetInventory.hosts.nas-01.address;
        ports = [
          22
          2222
          3000
          8080
        ];
      }
      {
        address = fleetInventory.hosts.homelab-04.address;
        ports = [
          22
          8000
        ];
      }
    ]
    ++
      map
        (name: {
          address = fleetInventory.hosts.${name}.address;
          ports = [ 22 ];
        })
        (
          builtins.filter (name: name != "nas-01" && name != "homelab-04") (
            builtins.attrNames fleetInventory.hosts
          )
        );
    recoveryManifest = {
      schema_version = 1;
      workspaces = [
        {
          id = "rupan";
          enabled = true;
          host = "homelab-04";
          network = {
            segment = "172.31.10.0/30";
            address = "172.31.10.2/30";
            gateway = "172.31.10.1";
            bridge = "aw-rupan-br";
            tap = "aw-rupan-tap";
            mac = "02:00:00:10:00:02";
            uplink = "enp0s31f6";
            ha_api = null;
            kubernetes_api = fleetInventory.hosts.${fleetInventory.bootstrapHost}.address;
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
          host = "homelab-04";
          network = {
            segment = "172.31.10.4/30";
            address = "172.31.10.6/30";
            gateway = "172.31.10.5";
            bridge = "aw-sam-br";
            tap = "aw-sam-tap";
            mac = "02:00:00:10:00:06";
            uplink = "enp0s31f6";
            ha_api = null;
            kubernetes_api = fleetInventory.hosts.${fleetInventory.bootstrapHost}.address;
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
