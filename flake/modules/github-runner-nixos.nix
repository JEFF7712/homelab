# GitHub Actions self-hosted runners for the nixos-config repo CI.
#
# Two ephemeral runners on homelab-04 for trusted main-branch artifact builds.
# PR validation runs on GitHub-hosted machines. Ephemeral means every job starts from a clean work dir and
# re-registers; with `replace` a reboot reuses the same runner names instead
# of orphaning entries in the GitHub UI.
#
# Why a NixOS host instead of the k8s actions-runner pods: CI is Nix
# evaluation and builds. Here the daemon, a warm /nix/store on NVMe, and the
# LAN Attic cache are already present, so jobs start in seconds with no Nix
# installer step. Resource caps keep two concurrent jobs from crowding out
# GPU and k3s duties on this 14C/20T, 32G box.
#
# Provision once (classic PAT with `repo` scope on JEFF7712/nixos-config, or a
# fine-grained PAT with Administration read/write on that repo; the file must
# hold exactly the token, no trailing newline):
#   install -d -m 0750 -o root -g github-runner-nixos /persist/github-runner-nixos
#   printf '%s' "$TOKEN" > /persist/github-runner-nixos/token
#   chmod 600 /persist/github-runner-nixos/token
{ pkgs, lib, ... }:
let
  user = "github-runner-nixos";
  baseDir = "/persist/github-runner-nixos";
  jobGuard = pkgs.writeShellScript "nixos-ci-job-guard.sh" (
    builtins.readFile ./github-runner-nixos/job-guard.sh
  );
  mkRunner = n: {
    enable = true;
    url = "https://github.com/JEFF7712/nixos-config";
    name = "homelab-nixos-${toString n}";
    tokenFile = "${baseDir}/token";
    extraLabels = [
      "homelab"
      "nixos"
    ];
    ephemeral = true;
    replace = true;
    inherit user;
    group = user;
    extraEnvironment.ACTIONS_RUNNER_HOOK_JOB_STARTED = toString jobGuard;
    # Distinct work dirs: the module cleans workDir on every service start,
    # so the two instances must not share one.
    workDir = "${baseDir}/work-${toString n}";
    # git/nix/bash/coreutils/tar come from the module defaults; these are the
    # CI-specific extras (attic for the ISO push step, plus the standard
    # script userland the checks assume: Ubuntu runners had all of these).
    extraPackages = with pkgs; [
      attic-client
      bc
      curl
      diffutils
      file
      findutils
      gawk
      gnugrep
      gnused
      jq
      just
      perl
      procps
      util-linux
    ];
    serviceOverrides = {
      MemoryMax = "8G";
      CPUQuota = "400%";
      CPUWeight = 50;
    };
  };
in
{
  users.users.${user} = {
    isSystemUser = true;
    group = user;
    description = "nixos-config CI runner";
  };
  users.groups.${user} = { };

  systemd.tmpfiles.rules = [
    "d ${baseDir} 0750 root ${user} -"
    "d ${baseDir}/work-1 0750 ${user} ${user} -"
    "d ${baseDir}/work-2 0750 ${user} ${user} -"
  ];

  services.github-runners = {
    nixos-ci-1 = mkRunner 1;
    nixos-ci-2 = mkRunner 2;
  };
}
