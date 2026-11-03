# OCI images for the persistent-agent controller and its workers.
#
# Both images carry the orchestrator package under /app and run as 10001,
# matching the restricted Pod Security context the manifests render. The
# worker image adds the three worker runtimes from `agentPkgs`, a nixpkgs
# input pinned separately from the fleet so the runtimes can move without
# rebuilding hosts. Only claude-code is allowed as unfree.
{
  pkgs,
  agentPkgs,
  lib ? pkgs.lib,
}:
let
  source = lib.cleanSourceWith {
    src = ../../scripts/orchestrator;
    filter =
      path: type: (type == "directory" && baseNameOf path != "__pycache__") || lib.hasSuffix ".py" path;
  };
  app = pkgs.runCommand "orchestrator-app" { } ''
    mkdir -p $out/app/scripts/orchestrator
    touch $out/app/scripts/__init__.py
    cp ${source}/*.py $out/app/scripts/orchestrator/
  '';
  # The publisher (controller) and worker workspaces both need git; the
  # orchestrator itself uses only the standard library.
  runtime = with agentPkgs; [
    python313
    gitMinimal
    bashInteractive
    coreutils
    cacert
  ];
  image =
    name: extra:
    agentPkgs.dockerTools.streamLayeredImage {
      inherit name;
      tag = "latest";
      contents = [ app ] ++ runtime ++ extra;
      extraCommands = "mkdir -m 1777 tmp";
      config = {
        User = "10001:10001";
        WorkingDir = "/app";
        Env = [
          "PATH=/bin"
          "PYTHONPATH=/app"
          "PYTHONUNBUFFERED=1"
          "HOME=/tmp"
          "SSL_CERT_FILE=${agentPkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
        ];
      };
    };
in
{
  controller = image "orchestrator" [ ];
  worker = image "orchestrator-worker" (
    with agentPkgs;
    [
      pi-coding-agent
      codex
      claude-code
    ]
  );
}
