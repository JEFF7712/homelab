# Workflow slots and container CPUs per Woodpecker agent. Agents tune this
# file without the operator's passkey; flake/modules/woodpecker.nix and
# woodpecker-sandbox.nix keep the trust settings (labels, devices, networks,
# secrets), which stay enforcement code. Every job keeps the 8 GiB container
# memory limit, so a slot count is also a memory budget.
{
  homelab-04 = {
    # One sandbox slot: a second 8G validation overcommitted the host's
    # memory; homelab-05 carries the other sandbox slot. Six CPUs: at two,
    # parallel gate lanes spent more time throttled than running (#12452:
    # 1,098 s throttled in a 348 s gate), and at four full gates still spent
    # a median 400 s throttled. The ci slice caps all slots at ten CPUs and
    # CPUWeight keeps pods ahead under contention.
    sandbox = {
      slots = 1;
      cpus = 6;
    };
    # Two trusted slots: scheduled read-only work holds at most one (its
    # shared concurrency group), so pushes and operator plans keep the other.
    trusted = {
      slots = 2;
      cpus = 2;
    };
    # Operations serialize per resource group, so a second deploy slot lets
    # unrelated mutations (a cache push and a Home Assistant apply) overlap.
    deploy = {
      slots = 2;
      cpus = 2;
    };
  };
  # The kubelet reserves three CPUs for CI (hosts/homelab-05/default.nix); the
  # slice and its one job may burst to this many while the host is idle, with
  # CPUWeight keeping pods ahead. At a hard three, full gates ran CPU-bound
  # (1,114 s throttled in a 445 s Python lane, pipeline #19844).
  homelab-05.sandbox = {
    slots = 1;
    cpus = 6;
  };
}
