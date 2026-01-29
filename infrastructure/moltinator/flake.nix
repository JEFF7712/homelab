{
  description = "Moltbot Hive: Secure & Immutable";

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
          auth = { mode = "token"; token = "hive-secret-internal"; };
        };
        skills = { entries = { "memory-core" = { enabled = false; }; }; };
      };

      managerJson = pkgs.writeText "manager.json" (builtins.toJSON (baseConfig // {
        identity = { name = "Hive Manager"; theme = "boss"; emoji = "👔"; };
        channels = {
          telegram = {
            enabled = true;
            botTokenFile = "/etc/secrets/telegram-token"; 
            allowFrom = [ "12345678" ]; 
          };
          anthropic = { enabled = true; };
        };
        agents = {
          list = [{
            id = "manager";
            model = { primary = "anthropic/claude-3-5-sonnet-20241022"; };
            workspace = "/data/workspace";
          }];
        };
      }));

      workerJson = pkgs.writeText "worker.json" (builtins.toJSON (baseConfig // {
        identity = { name = "Hive Worker"; theme = "robot"; emoji = "🤖"; };
        channels = { anthropic = { enabled = true; }; }; 
        agents = {
          list = [{
            id = "worker";
            model = { primary = "anthropic/claude-3-haiku-20240307"; };
            workspace = "/data/workspace";
          }];
        };
      }));

      watcherScript = pkgs.writeScriptBin "watcher" ''
        #!${pkgs.bash}/bin/bash
        echo "Starting Immutable Watcher..."
        mkdir -p /exchange
        
        export MOLTBOT_GATEWAY_TOKEN="hive-secret-internal"
        export MOLTBOT_GATEWAY_URL="http://127.0.0.1:18789"
        
        while true; do
          if [ -f /exchange/inbox.txt ]; then
            echo "[$(date)] Job received."
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
          contents = [ pkgs.cacert pkgs.bash moltbotPkg ];
          config = {
            User = "1000:1000";
            Env = [
              "MOLTBOT_CONFIG=${managerJson}"
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
          contents = [ pkgs.cacert pkgs.bash moltbotPkg watcherScript ];
          config = {
            User = "1000:1000";
            Env = [
              "MOLTBOT_CONFIG=${workerJson}"
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