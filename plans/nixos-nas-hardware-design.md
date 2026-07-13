# NixOS NAS Hardware Design

Date: 2026-07-12

## Purpose

Build a compact, dependable NixOS NAS for primary personal storage and Kubernetes backups. The NAS will remain separate from the future NixOS AI and ML worker so storage stays available during GPU maintenance, upgrades, and high-power workloads.

## Existing Environment

The homelab currently uses a Talos Kubernetes cluster on low-power mini PCs and thin clients, with a Proxmox-hosted control-plane VM and OPNsense VM. Persistent application storage is provided by single-replica Longhorn volumes. The 1700 GiB media volume currently resides on a USB-attached 2 TB HDD connected to the Dell Wyse worker.

Available storage hardware:

- One Seagate Exos X16 `ST10000NM002G`, 10 TB, 12 Gb/s SAS HDD.
- One USB-attached 2 TB HDD currently backing the Longhorn media volume.
- One unused 1 TB small-form-factor HDD with model and interface to be identified.

The current network uses a TP-Link TL-SG108E Gigabit managed switch. The existing mini-rack is nearly full, so the NAS will be a separate compact tower.

## Architecture Decision

Use two independent systems:

1. A compact, always-on NixOS NAS optimized for low idle power, HDD cooling, stable storage, and UPS-backed operation.
2. A separate NixOS AI and ML worker that joins the Kubernetes cluster and can be shut down when unused.

This separation prevents GPU heat, driver experimentation, compute maintenance, and future multi-GPU power requirements from affecting primary storage. The systems will initially communicate over Gigabit Ethernet, then move to 2.5 GbE. A direct high-speed link can be reconsidered if measured ML dataset transfers justify 10 GbE.

## NAS Hardware

### Chassis

Use a Jonsbo N2:

- Five hot-swap 3.5-inch bays.
- Mini-ITX motherboard support.
- One low-profile PCIe expansion slot.
- SFX power-supply support.
- Approximately 222.5 mm by 222.5 mm footprint.

Five bays leave one spare position beyond the expected four-drive ceiling without paying the size penalty of an eight-bay chassis.

### Platform

Preferred used platform:

- AMD Ryzen 5 Pro 5650G or 5650GE.
- ASRock B550 Phantom Gaming-ITX/ax.
- 32 GB ECC UDIMM, preferably two matched 16 GB modules.

This combination provides integrated graphics, official ECC support with a Ryzen Pro APU, onboard Intel 2.5 GbE, two M.2 slots, and a full-length PCIe slot for the SAS HBA. Used Ryzen Pro CPUs must be confirmed free of OEM Platform Secure Boot vendor locking.

### Storage Connectivity

Use a genuine Broadcom or LSI 9300-8i SAS HBA:

- SAS3008 controller.
- IT firmware, not hardware RAID firmware.
- Low-profile bracket.
- Active airflow across the HBA heatsink.
- Forward-breakout cabling appropriate for the N2 backplane.

Before pool creation, confirm the Exos logical and physical sector sizes, inspect SMART data, and run a destructive full-surface burn-in. Reformat unsupported 520-byte or 528-byte sectors to a standard 512-byte or 4096-byte format if necessary.

### System Storage

Use two small NVMe SSDs as a mirrored NixOS system pool. The data pool must remain independent of the operating-system pool so NixOS can be rebuilt without altering primary data.

### Power and Cooling

- Quality 450 W to 600 W SFX power supply.
- Replace or supplement the stock drive fan with a durable slim 120 mm PWM fan.
- Monitor HDD, NVMe, CPU, and HBA temperatures.
- Keep enterprise HDD temperatures in the 30s to low 40s Celsius under sustained load.

## Data-Pool Design

Create a ZFS pool on the Exos with `ashift=12`. The pool begins as a single-disk pool and later gains a second 10 TB-or-larger disk using `zpool attach`, converting the vdev into a mirror without recreating the pool.

The initial 10 TB disk provides approximately 9.1 TiB of raw usable capacity. Adding the mirror improves availability but does not increase usable capacity.

Create independent datasets for:

- Photos and Immich originals.
- Documents and the Obsidian vault.
- Kubernetes and Longhorn backups.
- Replaceable media.
- ML datasets and retained checkpoints.
- Temporary and scratch data.

Use ZFS native encryption for personal datasets. Set compression, record size, snapshot retention, and backup policy per dataset rather than applying one policy to the entire pool.

## Migration and Backup Safety

The single Exos disk must not become the only copy of irreplaceable data.

Migration sequence:

1. Assemble the NAS and validate memory, cooling, UPS communication, HBA operation, and both NVMe devices.
2. Burn in the Exos and confirm its sector format and SMART health.
3. Create the ZFS pool and datasets.
4. Copy the current Longhorn media volume to the NAS.
5. Verify the migration with end-to-end checksums before deleting or repurposing the source.
6. Repurpose the freed 2 TB USB HDD as an independent backup target for photos, documents, the vault, secrets, and Kubernetes backups.
7. Identify and test the unused 1 TB HDD, then use it as an offline or off-site copy of the most critical subset.
8. Add the second large HDD as a ZFS mirror as soon as practical.

The USB-attached 2 TB disk must not become a member of the primary ZFS pool. USB bridge resets are acceptable for a retryable backup job but not for primary-pool availability. Verify that the bridge provides stable device identity, UASP, SMART passthrough, and temperature reporting.

Backup policy by data class:

| Data | ZFS mirror | Snapshots | Independent backup |
|---|---|---|---|
| Photos | Required when second disk arrives | Frequent | 2 TB disk and selected 1 TB copy |
| Documents and Obsidian | Required when second disk arrives | Frequent | 2 TB disk and 1 TB offline copy |
| Kubernetes backups | Required when second disk arrives | Daily | 2 TB disk and off-site target where warranted |
| ML data and checkpoints | Selective | Selective | Retained results only |
| Replaceable media | For availability | Minimal | Not required |

A mirror is not a backup. The 1 TB disk should eventually be stored away from the NAS when practical to reduce the shared physical failure domain.

## UPS and Shutdown Coordination

Use a CyberPower CP1500PFCLCD, rated for 1500 VA and 1000 W with pure-sine output and USB monitoring. It will protect:

- The NAS.
- HP Mini Proxmox host.
- Talos worker systems.
- Chromebook gateway.
- TP-Link switch.
- Required modem or network handoff equipment.

The GPU worker will not use a battery-backed outlet during compute workloads.

Run Network UPS Tools on NixOS. The shutdown sequence is:

1. Ignore brief power interruptions for a configured delay.
2. Stop or quiesce stateful Kubernetes workloads.
3. Shut down Kubernetes nodes and the Proxmox host in a controlled order.
4. Keep switching and routing available while shutdown commands execute.
5. Export the NAS pool and shut down the NAS last.
6. Power off the UPS before the battery is exhausted.

The estimated combined current-homelab and NAS load is 100 W to 170 W, leaving substantial runtime and capacity headroom.

## Networking

Phase one uses the existing Gigabit switch, with expected practical throughput near 110 MB/s.

Phase two replaces or supplements it with managed 2.5 GbE networking. The NAS motherboard provides onboard 2.5 GbE, preserving the sole PCIe slot for the HBA. Expected practical 2.5 GbE throughput is approximately 250 MB/s to 280 MB/s, close to the sequential throughput of one Exos disk.

Do not force 10 GbE into the Mini-ITX NAS through unsupported M.2 adapters. Reconsider 10 GbE only after measuring actual ML data-transfer bottlenecks.

## Budget

| Item | Expected cost |
|---|---:|
| Used CPU, Mini-ITX motherboard, and 32 GB ECC RAM | $230 to $380 |
| Jonsbo N2 | $120 to $170 |
| SFX PSU | $80 to $130 |
| LSI 9300-8i and cabling | $70 to $120 |
| Mirrored NVMe system drives | $40 to $80 |
| Cooling upgrades and incidentals | $20 to $50 |
| CyberPower CP1500PFCLCD | $200 to $275 |
| Initial total | $800 to $1,125 |
| Future refurbished 10 TB-or-larger mirror disk | $120 to $220 |
| Future managed 2.5 GbE switch | $80 to $180 |

## Acceptance Criteria

The NAS is ready to become primary storage only when:

- ECC is detected and error reporting is available.
- Both system SSDs are healthy and the system mirror is active.
- The HBA is running IT firmware and exposes disk health data.
- The Exos passes the complete burn-in with no concerning SMART changes.
- ZFS datasets, encryption, snapshots, scrubs, and monitoring are configured.
- The UPS is visible to NUT and an orderly shutdown test succeeds.
- The first backup to the independent 2 TB disk completes and restores successfully.
- Monitoring alerts on pool degradation, failed backups, disk-health changes, and high temperatures.

The second large HDD and off-site rotation improve resilience, but the initial system remains safe only while independent copies of irreplaceable data are maintained.
