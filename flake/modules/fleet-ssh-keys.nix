# Public SSH identities trusted across every managed NixOS host. Sources and
# restrictions differ per host and live with each consumer.
{
  # Operator workstations; deliberately unrestricted on `rupan`.
  operatorKeys = [
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILW7MVmIGzW4Eq1NJm4+gsGwQ+iL44bIfyAa/wdQ1srQ"
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFTXsL6q+O1d29vTd3TtK2F0MYPggS5KSHryvlIFBS1K"
  ];
  # Agent workspace VM (agent-rupan); read-only `agent-observer` only.
  observerKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICZzDfJQDvL6axeNiY0b+iObfhjkBxzut8cGfcxr9zva developer@agent-rupan";
  # Operator workstation observer sessions; read-only `agent-observer` only.
  laptopObserverKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBGPgdA467aAFq4re73+3yM3Tws4F7GkecGh3LlN6S7/ agent-observer@laptop-nixos";
}
