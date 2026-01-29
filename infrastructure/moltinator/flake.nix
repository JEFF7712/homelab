{
  description = "Moltbot Hive";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    moltbot.url = "github:moltbot/nix-moltbot";
  };

  outputs = { self, nixpkgs, moltbot, ... }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      moltbotPkg = moltbot.packages.${system}.default;

      baseConfig = {
        gateway = {
          mode = "local";
          port = 18789;
          auth = {
            token = "\${MOLTBOT_GATEWAY_TOKEN}";
          };
        };
        plugins = {
          slots = {
            memory = "none";
          };
        };
        logging = { file = "/data/moltbot.log"; };
      };

      managerJson = pkgs.writeText "manager.json" (builtins.toJSON (baseConfig // {
        channels = {
          telegram = {
            botToken = "\${TELEGRAM_BOT_TOKEN}";
            dmPolicy = "allowlist";
            allowFrom = [ 7542293680 ];
          };
        };
        session = { store = "/data/sessions/manager.json"; };
        agents = {
          list = [{
            id = "manager";
            identity = { name = "Hive Manager"; theme = "boss"; emoji = "👔"; };
            model = { primary = "anthropic/claude-3-5-sonnet-20241022"; };
            workspace = "/data/workspace";
          }];
        };
      }));

      workerJson = pkgs.writeText "worker.json" (builtins.toJSON (baseConfig // {
        channels = {}; 
        session = { store = "/data/sessions/worker.json"; };
        agents = {
          list = [{
            id = "worker";
            identity = { name = "Hive Worker"; theme = "robot"; emoji = "🤖"; };
            model = { primary = "anthropic/claude-3-haiku-20240307"; };
            workspace = "/data/workspace";
          }];
        };
      }));

      mkFakeHome = name: configFile: pkgs.runCommand "${name}-home" {} ''
        mkdir -p $out/home/moltbot/.moltbot
        ln -s ${configFile} $out/home/moltbot/.moltbot/moltbot.json
      '';

      managerHome = mkFakeHome "manager" managerJson;
      workerHome = mkFakeHome "worker" workerJson;

      watcherScript = pkgs.writeScriptBin "watcher" ''
        #!${pkgs.bash}/bin/bash
        echo "Starting Watcher..."
        mkdir -p /exchange
        export MOLTBOT_GATEWAY_URL="http://127.0.0.1:18789"
        while true; do
          if [ -f /exchange/inbox.txt ]; then
            TASK=$(cat /exchange/inbox.txt)
            RESPONSE=$(moltbot agent --agent worker --message "$TASK" --text)
            echo "$RESPONSE" > /exchange/outbox.txt
            rm /exchange/inbox.txt
          fi
          sleep 2
        done
      '';

    in {
      packages.${system} = {
        managerImage = pkgs.dockerTools.buildLayeredImage {
          name = "jeff7712/moltbot-manager";
          tag = "latest";
          contents = [ pkgs.cacert pkgs.bash moltbotPkg managerHome ];
          config = {
            User = "1000:1000";
            Env = [
              "HOME=/home/moltbot"
              "MOLTBOT_GATEWAY_MODE=local"
              "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
            ];
            Cmd = [ "moltbot" "gateway" ];
            WorkingDir = "/data";
            Volumes = { "/data" = {}; };
          };
        };

        workerImage = pkgs.dockerTools.buildLayeredImage {
          name = "jeff7712/moltbot-worker";
          tag = "latest";
          contents = [ pkgs.cacert pkgs.bash moltbotPkg watcherScript workerHome ];
          config = {
            User = "1000:1000";
            Env = [
              "HOME=/home/moltbot"
              "MOLTBOT_GATEWAY_MODE=local"
              "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
            ];
            Cmd = [ "moltbot" "gateway" ];
            WorkingDir = "/data";
            Volumes = { "/data" = {}; };
          };
        };
      };
    };
}