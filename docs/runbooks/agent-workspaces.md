# Agent workspace operations

The versioned inventory is `config/agent-workspaces/workspaces.json`. An enabled entry must contain real enrollment identities, keys, collision-checked addresses, pinned image metadata, and paths below its workspace-specific storage and credential roots. Host limits are hard manifest validation limits, not evidence that the host can safely sustain them.

Run `just workspace-validate` before rendering or inspecting anything. `just workspace-plan` is offline and deterministic. `just workspace-status <id>` reads libvirt and disk/receipt state without changing it.

Provisioning is an authorized host operation:

```sh
just workspace-provision <id> --authorized
```

It must run as root on the manifest-selected host. It verifies storage ancestry, ownership and modes, refuses unknown domains/disks/receipts, checks that nftables and the dedicated bridge are active, downloads the pinned guest image with SHA-256 verification, creates separate system/data disks and a cloud-init seed, then defines and starts the domain. A crash that leaves assets without a receipt fails closed and requires inspection. It never adopts an unknown domain or disk.

Deprovisioning requires the same explicit flag:

```sh
just workspace-deprovision <id> --authorized
```

It accepts only a domain whose UUID, manifest hash, receipt, and disks agree. It destroys and undefines the domain but preserves both disks, seed material, domain XML, and receipt. Re-provisioning a matching deprovisioned workspace reuses those verified assets.

`flake/modules/agent-workspace-network.nix` derives one host-owned bridge and one interface-bound nftables policy per enabled workspace. The host must use systemd-networkd. Libvirt requires nftables and networkd. Guest IPv6, spoofed IPv4, other workspace segments, host input, private/special destinations, and unsolicited ingress are denied. Explicit HA capability API, DNS, NTP, public Internet egress, and declared SSH/HTTPS ingress sources are allowed. `services.agent-workspace-network.tcpEgress` adds typed, host-owned destination IPv4/TCP grants for named local workspaces before the private-network deny rule. Rupan's personal development VM has only NAS Git (2222) and signed-cache (8080) access; Sam's grants are unchanged. Filtering occurs before Internet masquerade. Stopping nftables first lowers each workspace tap interface, then destroys a running domain before removing policy.

The host modules are enabled on `homelab-04`. On top of its usual 1 CPU and 2 GiB system reservation, Kubernetes reserves the workspace slice's CPU and memory limits (2 CPUs and 14 GiB), so the guests never compete with pod requests. The workspace slice limits all workspace VMs together to 200 percent CPU, uses a 13 GiB memory pressure threshold, and has a 14 GiB hard memory limit. The limit covers Rupan's 8 GiB guest, Sam's 4 GiB guest, and QEMU overhead. The versioned enrollment inventory remains empty; the existing guests have separately maintained libvirt registrations and network-only recovery definitions.

For the `agent-rupan` and `agent-sam` guests, `flake/hosts/homelab-04/default.nix` contains only their existing network settings under `recoveryManifest`. This input creates host bridges and filtering rules but does not provision guests, add enrollment records, or grant HA access. Sam's saved XML and disks under `/persist/agent-workspaces/sam/` remain unchanged. Rupan's NixOS guest is described below. Keep this input limited to those existing networks and remove it only after an independently accepted replacement or decommissioning; the backup restore and HA capability gates remain open.

## Rupan's NixOS guest

Rupan's replacement guest is built by `nixosConfigurations.agent-rupan` in the
`JEFF7712/nixos-config` repository, sharing the laptop's lock and headless CLI
package definitions. Its source is `hosts/agent-rupan/configuration.nix` and
`home/rupan/agent-vm.nix` there. Its hypervisor definition is
[`config/agent-workspaces/rupan-nixos.xml`](../../config/agent-workspaces/rupan-nixos.xml).
This guest is outside the Ubuntu cloud-image provisioning lifecycle. Do not
add a guessed enrollment record or run `workspace-provision` against its disks.

The domain keeps UUID `a73b5b41-f1b1-5719-a045-94a0fd2cce92`, 2 vCPUs, 8 GiB RAM,
the 100 percent whole-domain CPU limit, and the existing bridge, tap, MAC and
address. Its single fresh 96 GiB disk is
`/persist/agent-workspaces/rupan/disks/nixos.qcow2`. SSH remains
`developer@172.31.10.2` through `homelab-04`. The guest has no shared laptop
filesystem, provider credentials, or infrastructure credentials in its image.

Installation is a Rupan-only authorized replacement:

1. Build `.#nixosConfigurations.agent-rupan.config.system.build.qcow2` in
   `nixos-config`; validate the compact 8 GiB image and copy `nixos.qcow2` to the
   disk path above, owned by `qemu-libvirtd:agent-workspace-storage` with mode
   `0660`. Expand only that installed copy with `qemu-img resize` to `96G`
   before boot. NixOS grows the root partition and filesystem on first boot.
   Refuse an occupied destination unless its destruction is explicitly authorized.
2. Record Sam's inactive XML, domain UUID, and QEMU PID, and Akshath's service
   configuration hash, PID, and start timestamp. Retain Rupan's prior inactive
   XML until replacement boot acceptance.
3. Shut down only `agent-rupan`, verify its UUID, then define only the
   Rupan-specific XML with `virsh define`. Start that domain and enable its
   libvirt autostart. Do not reload the shared firewall or restart libvirt.
4. Verify the new SSH host key through the host's QEMU guest agent before
   replacing the old known-hosts entry. Verify NixOS, provider CLI versions,
   guest-agent connectivity, public egress, and tmux progress across disconnect.
   Reboot this guest alone and verify disk boot and SSH return.
5. Compare the other VMs with their recorded state. Retire Rupan's old Ubuntu
   disks and cloud-init control files only after inspecting their exact paths
   and accepting the new guest. Never remove the workspace storage root or
   another owner's directory.

Running agent processes survive laptop disconnects while the guest stays on.
They do not survive a VM reboot; conversation files survive and can be resumed.

## Access and acceptance

Each recovery guest is narrowly routed: `adguard-netbird-01` has host routes to `172.31.10.2/32` and `172.31.10.6/32` via `homelab-04`, and the host permits that router as an ingress source. NetBird grants each workspace only to its owner's specific peer on TCP 22 and 443. The account configuration is reconciled from `config/netbird/agent-workspaces.json`.

For LAN access, use the host as an SSH jump:

```sh
ssh -J rupan@10.0.30.14 developer@172.31.10.2
```

Use the workspace owner's existing SSH key for each guest. A different owner's key must remain rejected. NetBird access uses per-peer resource policies; do not grant the VM subnet to a broad peer group.

Reconcile the NetBird resource and peer policies with `just netbird-agent-workspaces` to preview or `just netbird-agent-workspaces --apply` to apply. The command reads `/run/user/<uid>/netbird-api-token`; keep that file mode `0600` and never pass the token as an argument.

Live acceptance is recorded in [the acceptance record](../agent-tasks/agent-workspace-live-acceptance.md). The single-guest run proves boot, whole-domain CPU limiting, guest memory enforcement, public and container egress, selected deny rules, active and persistent XML verification, and bounded fail-closed firewall shutdown behavior. The hardened two-guest run proves bidirectional cross-workspace denial and sustained CPU, disk, and network pressure while etcd, PostgreSQL, and Home Assistant remained healthy. Both runs exercised deployed revision `914629e8ffd1df8516c9661c6f12476fcb288e53`. The remaining identity, browser, credential, restore, and HA capability gates still block enrollment.

Before enrollment, complete and record these checks against one deployed revision:

1. Accepted 2026-09-14: two disposable guests passed bidirectional denial and simultaneous CPU, disk, and network pressure while etcd, PostgreSQL, and Home Assistant remained healthy. Retain the raw evidence linked from the acceptance record.
2. Enroll the actual pilot identities only after their collision-checked addresses and credentials are available. Prove LAN SSH and HTTPS with NetBird stopped, remote access from an external network through NetBird, and cross-user login denial.
3. Prove authenticated browser access, each user's own agent provider credentials, session continuity after disconnect and reboot, and restore of a quiesced encrypted backup into an isolated VM with networking disabled.
4. Deploy and accept the HA capability API and trusted deployment worker before granting a guest automation deployment access. Guests must never receive the HA administrator credential, shared repository writer credential, infrastructure kubeconfig, or privileged runner access. Rupan's explicitly authorized personal coding pilot instead uses its own repository-scoped Git deploy key, generated inside that VM and subject to the repository's protected branches. This does not enroll other guests or grant CI/deployment API credentials.

Retain separate evidence for spoofing, tagged frames, IPv6, DNS rebinding, alternate private paths, firewall reload/reboot behavior, host-service denial, and measured throughput. Libvirt I/O weight is relative priority, not a throughput cap.

Bedroom isolation is a separate milestone. Do not claim it from workspace isolation, HA entity visibility, or dashboard permissions. It remains unverified until the private control boundary and physical device denial tests pass.

For the two-guest gate, create a temporary validated manifest containing exactly two enabled IDs prefixed with `acceptance-`. Provision both guests and wait for cloud-init, then run:

```sh
python -m scripts.agent_workspaces \
  --manifest /run/agent-workspace-acceptance/manifest.json \
  acceptance-two-guest \
  --authorized \
  --ssh-key /run/agent-workspace-acceptance/id_ed25519 \
  --network-url "$PRESSURE_URL" \
  --duration 60 \
  --evidence /run/agent-workspace-acceptance/evidence.json
```

The HTTPS target must be operator controlled. Acceptance requires at least 90 percent of the requested duration, 1 GiB written and 64 MiB received by each guest, aggregate CPU time of at least 1.5 CPU-seconds per wall second, a verified SSH listener on each peer, and an increased host firewall drop counter in both directions. The runner samples the workspace slice and service health throughout the load. It lowers both taps, destroys both domains, and attempts verified deprovisioning independently of health and evidence-write failures. Inspect and remove the preserved disposable disks only after the evidence and domain state agree.

Emergency isolation stops the workspace domain before removing or changing its deny policy. Preserve its disks and receipt. Never disable the firewall while a guest interface remains active.
