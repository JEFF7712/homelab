{
  config,
  lib,
  pkgs,
  ...
}:
{
  nixpkgs.config.allowUnfree = true;

  hardware.graphics = {
    enable = true;
    extraPackages = with pkgs; [
      intel-media-driver
      intel-vaapi-driver
    ];
  };

  services.xserver.videoDrivers = [ "nvidia" ];

  hardware.nvidia = {
    modesetting.enable = true;
    # TU117 (NVIDIA T1000 / T600) does not support open GPU kernel modules
    open = false;
    nvidiaSettings = false;
    package = config.boot.kernelPackages.nvidiaPackages.stable;
    powerManagement.enable = true;
  };

  hardware.nvidia-container-toolkit = {
    enable = true;
    # The device plugin hands containers GPU UUIDs, so the spec needs UUID
    # names alongside the default index names.
    extraArgs = [
      "--device-name-strategy"
      "uuid"
    ];
  };

  environment.systemPackages = [
    pkgs.libnvidia-container
    pkgs.runc
    pkgs.nvidia-container-toolkit
    pkgs.nvidia-container-toolkit.tools
  ];

  environment.etc."nvidia-container-runtime/config.toml".text = ''
    disable-require = false
    supported-driver-capabilities = "compat32,compute,display,graphics,ngx,utility,video"

    [nvidia-container-cli]
    environment = []
    ldconfig = "@${lib.getExe' pkgs.glibc "ldconfig"}"
    load-kmods = true
    no-cgroups = false
    path = "${lib.getExe' pkgs.libnvidia-container "nvidia-container-cli"}"

    [nvidia-container-runtime]
    log-level = "info"
    # Inject GPUs from the spec the NixOS generator writes. Toolkit 1.20 turns
    # "auto" into just-in-time CDI, whose driver library search cannot see
    # /run/opengl-driver and fails every GPU container.
    mode = "cdi"
    runtimes = ["runc", "crun"]

    [nvidia-container-runtime.modes.cdi]
    annotation-prefixes = ["cdi.k8s.io/"]
    default-kind = "nvidia.com/gpu"
    spec-dirs = ["/etc/cdi", "/var/run/cdi"]

    [nvidia-container-runtime.modes.csv]
    mount-spec-path = "/etc/nvidia-container-runtime/host-files-for-container.d"

    [nvidia-container-runtime.modes.legacy]
    cuda-compat-mode = "ldconfig"

    [nvidia-container-runtime-hook]
    path = "${lib.getOutput "tools" config.hardware.nvidia-container-toolkit.package}/bin/nvidia-container-runtime-hook"
    skip-mode-detection = false

    [nvidia-ctk]
    path = "${lib.getExe' config.hardware.nvidia-container-toolkit.package "nvidia-ctk"}"
  '';

  systemd.services.k3s.path = [
    pkgs.libnvidia-container
    pkgs.nvidia-container-toolkit
    pkgs.nvidia-container-toolkit.tools
    pkgs.runc
  ];

  systemd.tmpfiles.rules = [
    "d /var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.d 0755 root root -"
    "L+ /var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.d/nvidia.toml - - - - ${pkgs.writeText "nvidia.toml" ''
      [plugins."io.containerd.cri.v1.runtime".containerd.runtimes.nvidia]
        runtime_type = "io.containerd.runc.v2"

      [plugins."io.containerd.cri.v1.runtime".containerd.runtimes.nvidia.options]
        BinaryName = "${pkgs.nvidia-container-toolkit.tools}/bin/nvidia-container-runtime"
        SystemdCgroup = true
    ''}"
    "L+ /usr/bin/nvidia-ctk - - - - ${lib.getExe' config.hardware.nvidia-container-toolkit.package "nvidia-ctk"}"
    "L+ /usr/bin/nvidia-container-runtime - - - - ${pkgs.nvidia-container-toolkit.tools}/bin/nvidia-container-runtime"
  ];

  systemd.services.nvidia-gpu-exporter = {
    description = "NVIDIA GPU Prometheus Exporter";
    wantedBy = [ "multi-user.target" ];
    after = [ "network.target" ];
    path = [
      config.boot.kernelPackages.nvidiaPackages.stable
      pkgs.coreutils
    ];
    serviceConfig = {
      ExecStart = "${pkgs.python3}/bin/python3 ${./nvidia_gpu_exporter.py}";
      Restart = "always";
      RestartSec = "5s";
      DynamicUser = true;
      SupplementaryGroups = [ "video" ];
    };
  };

  networking.firewall.extraInputRules = ''
    ip saddr { 10.0.0.0/16, 10.42.0.0/16, 100.64.0.0/10 } tcp dport 9835 accept
  '';
}
