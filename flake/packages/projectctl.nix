{
  pkgs,
  lib ? pkgs.lib,
}:
let
  python = pkgs.python313.withPackages (p: [
    p.bcrypt
    p.cryptography
    p.pyyaml
  ]);
  source = lib.cleanSourceWith {
    src = ../../scripts/projectctl;
    filter =
      path: type: (type == "directory" && baseNameOf path != "__pycache__") || lib.hasSuffix ".py" path;
  };
in
pkgs.runCommand "projectctl" { nativeBuildInputs = [ pkgs.makeWrapper ]; } ''
  mkdir -p $out/bin $out/lib/projectctl/scripts/projectctl
  touch $out/lib/projectctl/scripts/__init__.py
  cp ${source}/*.py $out/lib/projectctl/scripts/projectctl/
  for command in projectctl projectctl-operator projectctl-gateway; do
    case "$command" in
      projectctl) module=scripts.projectctl ;;
      projectctl-operator) module=scripts.projectctl.operator ;;
      projectctl-gateway) module=scripts.projectctl.gateway ;;
    esac
    makeWrapper ${python}/bin/python $out/bin/$command \
      --add-flags "-I -c \"import sys,runpy;sys.path.insert(0,'$out/lib/projectctl');runpy.run_module('$module',run_name='__main__')\"" \
      --prefix PATH : ${
        lib.makeBinPath [
          pkgs.git
          pkgs.openssh
          pkgs.sops
        ]
      }
  done
''
