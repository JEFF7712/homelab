{ pkgs }:
let
  providers = builtins.filter (entry: entry.system == pkgs.stdenv.hostPlatform.system) (
    builtins.fromJSON (builtins.readFile ./ci-providers.json)
  );
  mirror = pkgs.runCommand "ci-provider-mirror" { } (
    ''
      mkdir -p "$out"
    ''
    + pkgs.lib.concatMapStrings (entry: ''
      mkdir -p "$out/${entry.address}"
      ln -s ${pkgs.fetchurl { inherit (entry) url hash; }} "$out/${entry.address}/${entry.filename}"
    '') providers
  );
  configuration = pkgs.writeText "ci-opentofu-config" ''
    provider_installation {
      filesystem_mirror {
        path = "${mirror}"
      }
    }
  '';
in
pkgs.symlinkJoin {
  name = "opentofu-local-providers";
  paths = [ pkgs.opentofu ];
  nativeBuildInputs = [ pkgs.makeWrapper ];
  postBuild = ''
    wrapProgram "$out/bin/tofu" --set TF_CLI_CONFIG_FILE ${configuration}
  '';
}
