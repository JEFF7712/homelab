{
  # Host secrets decrypt with the persisted SSH host key; / is a tmpfs.
  sops.age.sshKeyPaths = [ "/persist/etc/ssh/ssh_host_ed25519_key" ];
  sops.gnupg.sshKeyPaths = [ ];
}
