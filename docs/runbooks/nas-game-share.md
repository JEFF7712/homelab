# NAS game share

nas-01 serves `/tank/media/games` over SMB as `\\nas-01\games` (`smb://10.0.30.20/games`).
Source: `services.samba` in `flake/modules/nas-data.nix`; the clients-VLAN firewall
rule `clients-allow-nas-smb` in `tofu/opnsense/inventory.nix` allows TCP 445 only.

## Access

The share admits the `sam` (and `rupan`) accounts, authenticated by password. Guest access
is disabled. Samba keeps its own password database (`/var/lib/samba`, persisted
across the tmpfs root), separate from the Unix account, and the NixOS config
does not set it. Until it is set, every login is rejected.

Set Sam's password once after deploy, or to rotate it:

```sh
ssh rupan@10.0.30.20 sudo smbpasswd -a sam
```

Check that the account exists:

```sh
ssh rupan@10.0.30.20 sudo pdbedit -L
```

Files written over SMB are owned by `rupan:users`. qBittorrent (uid 1000) writes
under `/tank/media/downloads/games` and `/tank/media/games/pc` through the
`media-library` NFS volume.
