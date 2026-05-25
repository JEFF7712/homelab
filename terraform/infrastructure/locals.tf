locals {
  ssh_private_key = coalesce(var.ssh_private_key, try(file(pathexpand("~/.ssh/id_ed25519")), ""))
  ssh_public_key  = coalesce(var.ssh_public_key, try(file(pathexpand("~/.ssh/id_ed25519.pub")), ""))
}
