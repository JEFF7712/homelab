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
    ../../profiles/projectctl-pilot.nix
    ../../modules/gitlab-dr.nix
    ../../modules/platform-backup.nix
    ../../modules/repo-monitor.nix
    ../../modules/release-mirror.nix
  ];

  networking.hostName = "homelab-04";

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
    haDashboardTokenFile = "/persist/secrets/kiosk-ha-token";
    haDashboardUrl = "http://10.0.40.13:8123";
  };

  homelab.woodpecker.enable = true;
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
    nodeIp = "10.0.30.14";
    serverAddress = "https://10.0.30.11:6443";
    tokenFile = "/persist/secrets/k3s-token";
  };

  services.k3s.extraFlags = [
    "--kubelet-arg=system-reserved=cpu=1,memory=2Gi"
    "--kubelet-arg=kube-reserved=cpu=500m,memory=1Gi"
  ];
}
