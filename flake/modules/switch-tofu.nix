{ config, pkgs, ... }:
let
  tofu = import ../../nix/ci-providers.nix { inherit pkgs; };
  source = pkgs.runCommand "switch-tofu-source" { } ''
    mkdir -p $out/scripts/switches $out/tofu/switches
    cp ${../../scripts/switches/model.py} $out/scripts/switches/model.py
    cp ${../../scripts/switches/runner.py} $out/scripts/switches/runner.py
    touch $out/scripts/__init__.py $out/scripts/switches/__init__.py
    cp ${../../tofu/switches/main.tf.json} $out/tofu/switches/main.tf.json
    cp ${../../tofu/switches/providers.tf} $out/tofu/switches/providers.tf
    cp ${../../tofu/switches/backend.tf} $out/tofu/switches/backend.tf
    cp ${../../tofu/switches/.terraform.lock.hcl} $out/tofu/switches/.terraform.lock.hcl
  '';
in
{
  sops.secrets."switch-tofu-state-keys.json" = { };
  sops.secrets."switch-tofu-credentials.json" = { };
  systemd.tmpfiles.rules = [ "d /var/lib/switch-tofu 0700 root root -" ];
  environment.systemPackages = [
    (pkgs.writeShellScriptBin "switch-tofu" ''
      export PYTHONPATH=${source}
      exec ${pkgs.python313}/bin/python -m scripts.switches.runner "$@" \
        --blueprint ${source}/tofu/switches \
        --work-root /var/lib/switch-tofu \
        --state-keys ${config.sops.secrets."switch-tofu-state-keys.json".path} \
        --write-credentials ${config.sops.secrets."switch-tofu-credentials.json".path} \
        --tofu ${tofu}/bin/tofu
    '')
  ];
}
