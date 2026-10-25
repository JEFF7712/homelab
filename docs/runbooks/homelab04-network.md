# homelab-04 Ethernet transmit hang

On 2026-10-24, the Intel onboard NIC (`8086:1a1c`, `e1000e`, PCI
`0000:00:1f.6`, interface `enp0s31f6`) logged 4,371 instances of
`Detected Hardware Unit Hang`, from 13:08:08 through 15:33:48 CDT.
The host continued logging until shutdown and recovered after the operator
used the power button. Its previous-boot kernel journal contained no OOM or
kernel panic. The precise trigger is unproven.

`flake/hosts/homelab-04/default.nix` defines
`onboard-ethernet-offloads.service` to disable TCP segmentation offload for
IPv4 and IPv6, plus generic segmentation offload. Intel's
[driver maintainer recommends disabling TSO for this failure](https://lists.osuosl.org/pipermail/intel-wired-lan/Week-of-Mon-20190513/016087.html),
including configurations forwarding NAT traffic. Software segmentation can
increase CPU use under traffic. Energy Efficient Ethernet is unchanged.

The oneshot runs after local filesystems and the NIC device are ready, before
`network-pre.target`. It matches the existing NIC's PCI/driver sysfs path.
The service uses no default dependencies so its early boot ordering does not
depend on `basic.target`. It remains active after applying the settings.

A `systemd.network.links` entry would add a networkd restart trigger in the
pinned NixOS module. Libvirt depends on networkd, so restarting it could
interrupt both agent guests. This host uses the standalone boot service to
apply the workaround without changing networkd's configuration or triggers.

## Apply and verify

Deliver the host configuration with the scoped CI-host deployment. Confirm
that dry activation does not restart networkd, nftables, libvirt, or the
workspace domains. If unrelated deployed-source drift would restart them,
resolve that drift before activation. Do not reboot the host, reload the NIC
driver, or retrigger all devices while the guests are running.

After activation, verify `onboard-ethernet-offloads.service` is active and
enabled. Read back `ethtool -k enp0s31f6`: TCP segmentation, TCP6
segmentation, and generic segmentation must be off. Verify 1000 Mb/s full
duplex, link detected, node Ready, SSH to the Rupan guest, unchanged guest
QEMU PIDs, unchanged persisted domain XML hashes, and no new hardware hangs.

The live command is `ethtool -K enp0s31f6 tso off gso off`; it does not
require a link restart. Check the settings again after the next scheduled
host reboot to establish boot persistence. Short checks do not prove the
failure cannot recur; observe normal CI, VM, and cluster traffic over time.

Recovery: revert the host service in source and redeploy, then restore the
recorded prechange runtime settings with
`ethtool -K enp0s31f6 tso on gso on`. Removing the service alone does not
restore hardware feature flags already applied. If hangs recur, capture the
kernel journal locally and review BIOS/NIC firmware, driver fixes, and a
replacement NIC against this exact hardware.
