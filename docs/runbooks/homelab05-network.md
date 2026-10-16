# homelab-05 Ethernet transmit hang

On 2026-10-16 at 02:24:21 CDT, the onboard Intel NIC (`8086:15f9`,
`e1000e`, PCI `0000:00:1f.6`, interface `enp0s31f6`) began repeatedly logging
`Detected Hardware Unit Hang`. The host continued logging until its manual
shutdown at 13:17. Akshath's guest had rebooted at 02:17 and resumed exporting
metrics before the NIC hang. A causal connection to that reboot is unproven.

`flake/hosts/homelab-05/default.nix` defines `10-onboard-ethernet.link` to
disable IPv4/IPv6 TCP segmentation offload and generic segmentation offload.
Intel's [driver maintainer recommends disabling TSO for this failure](https://lists.osuosl.org/pipermail/intel-wired-lan/Week-of-Mon-20190513/016087.html),
including configurations forwarding NAT traffic. Energy Efficient Ethernet is
also disabled as a precaution, not as a confirmed cause. Software segmentation
may increase CPU use under traffic, and disabling EEE increases idle power.
The link retains gigabit autonegotiation and its existing interface name.

## Deployment and verification

The source mitigation is not evidence of activation. After authorized deployment
through the fleet workflow, apply the corresponding offload/EEE settings with
`ethtool` or schedule a reboot. Installing a `.link` file alone does not establish
that an already configured device adopted its settings. Do not retrigger all
network devices remotely.

Read back `ethtool -k enp0s31f6` and `ethtool --show-eee enp0s31f6`: TCP and
TCP6 segmentation, generic segmentation, and EEE must be off. Verify gigabit
full duplex, node Ready, VM access, and no new hardware hangs in the journal.
Repeat read-back after a scheduled host reboot to establish persistence. Observe
normal VM and cluster traffic over several days; short checks cannot prove the
failure will never recur. Coordinate any guest reboot test with Akshath.

If the failure recurs, capture the kernel journal locally. Review Dell BIOS/NIC
firmware and kernel driver fixes against this exact hardware. A replacement NIC
is the next option if mitigation does not hold. Do not automatically reboot the
host or reload its NIC driver while workloads are running.

Recovery: revert the host-specific link configuration and redeploy the reviewed
generation. Runtime rollback restores TSO/GSO and EEE to their recorded prechange
enabled states. Changing EEE can briefly renegotiate the link, so retain local
console access or a recovery operator during activation.

## 2026-10-16 activation

Authorized fleet deployment activated generation 59 at 13:39 CDT; generation 58
is the rollback generation. Explicit `ethtool` application disabled TSO, TCP6
TSO, GSO and EEE. Read-back confirmed 1000 Mb/s full duplex, no failed systemd
units, node Ready, guest metrics and the forwarded guest SSH banner. The timed
runtime rollback was cancelled after connectivity was verified. No host reboot
was performed, so boot persistence and long-term nonrecurrence remain untested.

The networkd restart also cleanly stopped and restarted the VM through its
service dependency. A dry-activation unit summary did not list that dependent
restart; account for guest downtime in future networkd changes. Fleet activation
also included already-committed kubelet persistence/eviction settings absent
from the previous host generation. The Immich replica was unready before this
deployment and remains unable to rewind its divergent PostgreSQL timeline.
