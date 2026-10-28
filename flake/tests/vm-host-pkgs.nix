{ pkgs }:
# Root builds in unprivileged CI containers lack CAP_DAC_READ_SEARCH.
# virtiofsd's default file-handle mode requests that capability; O_PATH
# descriptors serve the same VM shares without requiring extra permissions.
pkgs
// {
  virtiofsd = pkgs.writeShellScriptBin "virtiofsd" ''
    exec ${pkgs.virtiofsd}/bin/virtiofsd "$@" --inode-file-handles=never
  '';
}
