{
  config,
  lib,
  pkgs,
  ...
}:
{
  options.homelab.gitlabDR.enable = lib.mkEnableOption "Explicit Docker-only GitLab disaster recovery executor";
  config = lib.mkIf config.homelab.gitlabDR.enable {
    services.woodpecker-agents.agents.sandbox.enable = lib.mkForce false;
    services.woodpecker-agents.agents.trusted.enable = lib.mkForce false;
    services.woodpecker-agents.agents.deploy.enable = lib.mkForce false;
    systemd.tmpfiles.rules = [ "d /persist/gitlab-dr 0700 root root -" ];
    systemd.services.gitlab-dr-runner = {
      wantedBy = [ "multi-user.target" ];
      after = [
        "docker.service"
        "network-online.target"
      ];
      requires = [ "docker.service" ];
      wants = [ "network-online.target" ];
      serviceConfig = {
        ExecStart = "${pkgs.gitlab-runner}/bin/gitlab-runner run --config /persist/gitlab-dr/config.toml --working-directory /persist/gitlab-dr";
        Restart = "on-failure";
        UMask = "0077";
        Slice = "ci.slice";
        ProtectHome = true;
        ProtectSystem = "strict";
        ReadWritePaths = [
          "/persist/gitlab-dr"
          "/run/docker.sock"
        ];
      };
    };
  };
}
