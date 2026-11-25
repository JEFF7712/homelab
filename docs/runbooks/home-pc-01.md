# home-pc-01 (remote gaming desktop)

`home-pc-01` is an HP Pavilion Gaming Desktop 690 (Ryzen 7 1700, Radeon RX 550,
16 GB, one 1 TB HDD) at another house, running Plasma for the family. It is a
NetBird peer only (`100.94.53.129`, `home-pc-01.netbird.cloud`); it has no LAN
address, so it is not an inventory host. `flake/flake.nix` adds it to
`nixosConfigurations` through `remoteHosts`.

## Access

LAN hosts cannot open connections into the mesh directly (see
[NetBird](netbird.md)). CI and the agent VM (both seen as `homelab-04`) use the
`home-pc-01-ssh` relay on `adguard-netbird-01`, `10.0.30.10:2201`, which
reconnects from `100.94.249.145`: the source `flake/hosts/home-pc-01/access.nix`
admits for the CI deploy key (`rupan`) and the agent VM observer key. The
NetBird policy `homelab-to-home-pc-01` admits `adguard-netbird-01` on TCP 22;
it is dashboard state, managed through the NetBird API. Operator keys stay
unrestricted on `rupan` and work directly from NetBird devices. Users are
mutable: family members manage their own passwords with `passwd`.

```sh
ssh -p 2201 agent-observer@10.0.30.10   # read-only, from the agent VM
```

The peer has login expiration disabled so it never drops off the mesh.

## Deploy

`just ship` routes `flake/hosts/home-pc-01/`, `flake/modules/fleet-ssh-keys.nix`
and `config/ci/remote-hosts.known_hosts` to `deploy-home-pc-01`, which builds
on the PC (`--build-host`) through the relay and checks that the system is
`running` with NetBird, sshd, the display manager and `/home` up. The host key
is pinned for the relay endpoint in `config/ci/remote-hosts.known_hosts`. The PC may be off or in use; it is not in
`deploy-fleet`. Resume a failed run with `just ship --resume <sha> --deploy
deploy-home-pc-01`.

## Desktop

`flake/hosts/home-pc-01/desktop.nix` gives the family a finished desktop: a
Plymouth splash with the HP firmware logo (`bgrt`) and a silent boot, the
Breeze SDDM login, Noto fonts (CJK and emoji), Chrome, LibreOffice, VLC, Zoom,
Spotify and Discord, network printing (CUPS with Avahi), Bluetooth, fwupd
firmware updates, KDE Connect, and Flatpak with Flathub in Discover for
user-installed apps.

The boot menu is hidden. To boot an older generation (for example after a
bad deploy), hold **Space** while the PC powers on and pick it from the
systemd-boot menu. `tests/test_home_pc_01.py` keeps the remote-safety settings
(C-state limit, watchdog, panics, no sleep, `nofail` `/home`) in place.

## Uptime monitor

Gatus on port 8080 (NetBird `wt0` only) probes every public hostname from
outside the LAN; see [uptime monitor](uptime-monitor.md).

## Offsite backup

The PC holds the homelab's offsite backup copy: an append-only restic
`rest-server` on port 8000 (NetBird `wt0` only) storing
`/home/.offsite-backup`. `adguard-netbird-01` pushes to it weekly (Mondays); see
[external backup recovery](external-backup-recovery.md#offsite-copy-home-pc-01).
The service sees an empty `/home` with only that directory bound in.

## Hardware quirks

- **CPU idle freeze:** first-gen Ryzen can hang in C6, exposed as `C2` by
  `acpi_idle`. `processor.max_cstate=1` keeps C1 only. A boot froze 15 s into
  userspace with no log before this.
- **Watchdog:** the SP5100 TCO watchdog resets the PC 30 s after systemd stops
  responding, and lockups panic and reboot after 10 s, so a hang never strands
  the remote host.
- **No sleep:** the sleep targets are disabled. Plasma's idle suspend took the
  PC off NetBird for three hours on 2026-11-23; screens still blank.
- **TPM:** the HP ACPI TPM2 table is broken (`tpm_crb_acpi` `-EBUSY`);
  `systemd.tpm2.enable = false` avoids two 90 s waits for `/dev/tpm0`.
- **GPU:** the installer kernel hung in `amdgpu` flip-done timeouts; the latest
  kernel is used.

## Disk

| Partition | Size | Mount |
|---|---|---|
| `sda1` (`home`) | 622 GB | `/home`, `nofail` so a failed mount still boots |
| `sda6` (`NIXBOOT`) | 2 GB | `/boot` (systemd-boot; fallback `\EFI\BOOT\BOOTX64.EFI` is second in the firmware order) |
| `sda7` (`nixos`) | 308 GB | `/` |

Windows was removed on 2026-11-23; the pre-removal partition table is in
`/root/sda-partition-table-before-windows-removal.sfdisk`. A store damaged by an
interrupted install was repaired with `nix-store --verify --check-contents
--repair`; run the same if units fail on unreadable or empty store files.
