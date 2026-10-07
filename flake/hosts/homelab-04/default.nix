{
  imports = [
    ./hardware-configuration.nix
    ../../modules/common-base.nix
    ../../modules/disko-single-disk.nix
    ../../modules/k3s-server.nix
    ../../modules/k3s-registry-client.nix
    ../../modules/monero.nix
    ../../modules/nvidia.nix
    ../../modules/github-runner-nixos.nix
    ../../modules/woodpecker.nix
    ../../modules/gitlab-dr.nix
    ../../modules/platform-backup.nix
  ];

  networking.hostName = "homelab-04";

  homelab.woodpecker.enable = true;
  homelab.platformBackup.enable = true;

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
