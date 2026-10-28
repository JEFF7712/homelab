# Akshath Omarchy VM

The owning configuration is `flake/modules/omarchy-vm.nix`, enabled by
`flake/hosts/homelab-05/default.nix`. The guest is `omarchy-akshath`, username
`akshath`, at `10.250.60.2` behind `br-akshath`. Resources are 4 vCPUs,
4096 MiB guest RAM (down from 8192 MiB on 2026-10-28, when homelab-05's
pods ran at 191% of allocatable) and a 100 GiB qcow2 disk. Cloud model inference is external;
the host's NVIDIA GPU remains with Kubernetes.

## Lifecycle and isolation

`omarchy-akshath.service` runs QEMU as the unprivileged `omarchy-vm` user and
starts on boot. Persistent assets live under `/persist/omarchy-vm/akshath`.
The guest has no host filesystem shares, container sockets, infrastructure
credentials or host administrative access. Host nftables permits public IPv4
egress, denies private/special destinations and IPv6, checks the guest MAC/IP,
and limits incoming SSH to management, client and NetBird sources. Stopping
nftables stops QEMU first. The bridge has one DHCP lease, `10.250.60.2`.
The ingress list includes `10.0.30.10/32`, the observed source after the NetBird
appliance performs SNAT, as well as preserved NetBird client addresses.

The service allows 400% aggregate CPU for the initial four vCPUs, a 9 GiB hard
memory ceiling including emulator overhead, and no host swap. The Kubernetes
system reservation is derived from the VM configuration: one existing host CPU
plus guest vCPUs, and 2 GiB existing host RAM plus guest RAM plus 1 GiB emulator
headroom. Kubernetes resource requests use the reduced allocatable budget;
pod limits can still exceed that budget, so monitor host pressure as well.

VM startup requires an existing disk of exactly the configured virtual size
and existing UEFI state. It never formats, recreates or resizes a disk.
The `installing` marker attaches the pinned installation and cidata ISOs.
Remove that marker after verifying an installed-disk boot, then restart QEMU.
Never reattach installation media to an occupied disk without reviewing its
wipe configuration. Disk boot precedes ISO boot during installation.

Service stop requests powerdown through the guest agent, falling back to ACPI
when the agent is unavailable. The desktop can ignore ACPI power-button events,
so the enabled guest agent is necessary for normal clean shutdown. The service
has 120 seconds to finish.
Take quiesced backups of `system.qcow2` and `OVMF_VARS.fd` before resizing or
guest upgrades. The VM does not add an offsite backup policy.

## Install source

The official ISO is `https://iso.omarchy.org/omarchy-4.0.4.iso`.
SHA-256: `ddeded2758c48318d201dfdac905ecb28f570441883f0c052ea3cd5d05acf92d`.
The official [unattended install protocol](https://omarchy.org/manual/unattended-installs/)
uses a second drive labeled `cidata`. `python -m scripts.omarchy_vm.seed` writes
the inputs for the guest's `/dev/vda` only, with no disk encryption so an
unattended reboot can finish. The protected host directory contains the guest
disk. The seed contains a password hash and an operator provisioning public key,
never a private SSH key or provider token. Keep seed files private.

Seed generation requires explicit `--output`, `--admin-public-key`, and
`--password-hash-file` inputs. It refuses an existing output directory. Build
the cidata ISO using `mkisofs -volid cidata -joliet -rock`. The host configuration
exposes the pinned OVMF code and variable template under `/etc/omarchy-vm/`;
copy the variable template once into the persistent directory. Create the
100 GiB qcow2 once after checking that the target path is absent.

## Access and deferred enrollment

SSH is forwarded from `10.0.30.15:2222` to the guest. The Omarchy desktop is
the actual virtual display, served by QEMU VNC on `10.250.60.1:5906`; the host
firewall allows it only from this guest. After SSH enrollment, tunnel VNC through
the guest SSH connection, forwarding a client loopback port to
`10.250.60.1:5906`. Never expose VNC directly to the public Internet. This
console has no separate authentication and relies on the enforced network
boundary and SSH authentication. It also exposes the guest boot console.

Akshath's SSH public key and provider credentials are separate enrollment steps.
Append only his verified public key to his guest `authorized_keys`, verify a new
connection, then remove the temporary provisioning key and verify its revocation.
Let him choose a desktop password and supply his own model credentials through
Hermes setup. Keep password SSH and root SSH disabled. The provisioning secret
and key are local mode-0600 task assets, not repository content or handoff text.
Remote access outside the LAN requires an authorized VPN route/source; VPN
enrollment is separate from creating the guest.

`scripts/omarchy_vm/guest-setup.sh` uses Omarchy's updater, installs the guest
exporter and QEMU agent, disables password/root SSH and sleep, and installs the
first-party Hermes runtime. It removes Omarchy's marked older mise-backed Hermes
copy before publishing the managed runtime on PATH. The system gateway service
runs as `akshath` and is prepared without starting or enabling it. Configure
Akshath's provider and messaging credentials before enabling that service.
The guest disables Plymouth, encryption and hibernation initramfs hooks for this
unencrypted always-on VM. Plymouth stalled a cold boot during live validation;
the Limine override and rebuilt initramfs keep subsequent boots independent of it.

## Monitoring and capacity decisions

The host exporter is `omarchy-akshath-metrics.service`, listening on
`10.0.30.15:9126`, allowlisted to cluster hosts and pods. `/metrics` reports
host-enforced CPU usage, throttling, cgroup memory, OOM events, pressure stalls,
allocated resources and qcow2 host allocation. `/guest-metrics` proxies the
guest's `prometheus-node-exporter.service` at `10.250.60.2:9100`; it returns 503
when the guest is unavailable instead of serving stale data. Guest node exporter
must enable its pressure collector and permit port 9100 from the bridge gateway.

Flux owns the two Prometheus scrape jobs, rules and Grafana dashboard under
`gitops/observability/`. The dashboard UID is `omarchy-akshath`. Rules record
guest CPU and memory utilization once per minute and alert on VM/exporter loss,
sustained CPU or memory saturation, host-side OOM kills and a filling guest disk.
Existing Alertmanager routing applies. Deployment acceptance requires both
targets UP, evaluated recording/alert rules and the dashboard loaded in Grafana;
a source check or responding exporter alone does not prove that monitoring is live.

Review a representative seven days including real agent tasks, browser work and
builds. The dashboard shows p95, peak and data coverage. Prometheus currently
retains seven days, additionally constrained by its size cap, so partial history
must not be interpreted as a complete low-use window. Consider increasing RAM
after sustained available RAM below 15%, swap activity or memory stalls, and CPU
after sustained utilization above 85% with contention. Downsize only after a
representative week shows substantial headroom even at peaks. Idle time alone
does not establish that a smaller VM can run its active jobs.

Resizing is manual. Change `homelab.omarchyVm.vcpus` or `memoryMiB`, build and
review the host closure, then schedule a guest shutdown and activation. The
Kubernetes reservation tracks those values. Disk growth requires an offline
qcow2 resize, an updated `diskGiB`, and enlargement of the guest partition and
filesystem, followed by read-back verification. Do not shrink the disk. No
automatic resource changes are enabled.
