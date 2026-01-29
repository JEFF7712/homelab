{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    moltbot.url = "github:moltbot/nix-moltbot";
  };

  outputs = { self, nixpkgs, moltbot, ... }: let
    system = "x86_64-linux";
    pkgs = nixpkgs.legacyPackages.${system};
    
    myMoltbot = moltbot.packages.${system}.default;
  in {
    packages.${system}.dockerImage = pkgs.dockerTools.buildLayeredImage {
      name = "moltbot-hive-node";
      tag = "latest";
      
      contents = [ 
        myMoltbot
        pkgs.cacert
        pkgs.iana-etc
        pkgs.tzdata
        pkgs.bash
        pkgs.coreutils
      ];

      extraCommands = ''
        mkdir -m 1777 tmp
        mkdir -m 755 data
      '';

      config = {
        Cmd = [ "${myMoltbot}/bin/moltbot" ];
        
        Env = [
          "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
          "HOME=/data"
        ];
        WorkingDir = "/data";
        Volumes = { "/data" = {}; };
      };
    };
  };
}