from __future__ import annotations

import argparse
import json
from pathlib import Path


def installer_config(disk_gib: int) -> dict[str, object]:
    mib, gib = 1024**2, 1024**3
    partitions = []
    for name, start, size, filesystem, mount, flags, subvolumes in [
        ("boot", mib, 2 * gib, "fat32", "/boot", ["boot", "esp"], []),
        (
            "root",
            mib + 2 * gib,
            disk_gib * gib - 2 * gib - 2 * mib,
            "btrfs",
            None,
            [],
            [
                {"mountpoint": "/", "name": "@"},
                {"mountpoint": "/home", "name": "@home"},
                {"mountpoint": "/var/log", "name": "@log"},
                {"mountpoint": "/var/cache/pacman/pkg", "name": "@pkg"},
            ],
        ),
    ]:
        partitions.append(
            {
                "btrfs": subvolumes,
                "dev_path": None,
                "flags": flags,
                "fs_type": filesystem,
                "mount_options": ["compress=zstd"] if name == "root" else [],
                "mountpoint": mount,
                "obj_id": "ea21d3f2-82bb-49cc-ab5d-6f81ae94e18d"
                if name == "boot"
                else "8c2c2b92-1070-455d-b76a-56263bab24aa",
                "size": {
                    "sector_size": {"unit": "B", "value": 512},
                    "unit": "B",
                    "value": size,
                },
                "start": {
                    "sector_size": {"unit": "B", "value": 512},
                    "unit": "B",
                    "value": start,
                },
                "status": "create",
                "type": "primary",
            }
        )
    return {
        "app_config": None,
        "archinstall-language": "English",
        "auth_config": {},
        "audio_config": {"audio": "pipewire"},
        "bootloader_config": {"bootloader": "Limine", "uki": False, "removable": False},
        "custom_commands": [],
        "omarchy_install": {
            "input_method": "none",
            "input_xkb_layout": "us",
            "mode": "full_disk",
            "defer_provisioning": False,
            "target_mount": "/mnt",
            "boot": {
                "esp_mount": "/boot",
                "esp_path": "/EFI/limine",
                "efi_binary": "limine_x64.efi",
                "enable_fallback": True,
            },
            "storage": {"kernel": "linux-omarchy"},
        },
        "disk_config": {
            "config_type": "default_layout",
            "device_modifications": [
                {"device": "/dev/vda", "partitions": partitions, "wipe": True}
            ],
        },
        "hostname": "omarchy-akshath",
        "kernels": ["linux-omarchy"],
        "network_config": {"type": "iso"},
        "ntp": True,
        "parallel_downloads": 8,
        "script": None,
        "services": [],
        "swap": True,
        "timezone": "America/Chicago",
        "locale_config": {
            "kb_layout": "us",
            "sys_enc": "UTF-8",
            "sys_lang": "en_US.UTF-8",
        },
        "mirror_config": {
            "custom_repositories": [],
            "custom_servers": [{"url": "https://mirror.omarchy.org/$repo/os/$arch"}],
            "mirror_regions": {},
            "optional_repositories": [],
        },
        "packages": [
            "base-devel",
            "git",
            "omarchy-keyring",
            "omarchy-settings",
            "omarchy",
        ],
        "profile_config": {"gfx_driver": None, "greeter": None, "profile": {}},
        "version": "3.0.9",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare Omarchy's official cidata installer inputs; never create or modify a VM disk."
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--admin-public-key", type=Path, required=True)
    parser.add_argument("--password-hash-file", type=Path, required=True)
    parser.add_argument("--disk-gib", type=int, default=100)
    args = parser.parse_args()
    if not 40 <= args.disk_gib <= 200:
        parser.error("disk size must be 40 through 200 GiB")
    key = args.admin_public_key.read_text().strip()
    password_hash = args.password_hash_file.read_text().strip()
    if not key.startswith("ssh-ed25519 ") or len(key.split()) < 2:
        parser.error("expected an administrator Ed25519 public key")
    if not password_hash.startswith("$6$"):
        parser.error("expected a SHA-512 password hash")
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    files = {
        "user_configuration.json": json.dumps(
            installer_config(args.disk_gib), indent=2
        ),
        "user_credentials.json": json.dumps(
            {
                "root_enc_password": "!",
                "users": [
                    {
                        "enc_password": password_hash,
                        "groups": [],
                        "sudo": True,
                        "username": "akshath",
                    }
                ],
            },
            indent=2,
        ),
        "user_full_name.txt": "Akshath\n",
        "user_encrypt_installation.txt": "false\n",
        "authorized_keys": key + "\n",
    }
    for name, content in files.items():
        path = args.output / name
        path.write_text(content)
        path.chmod(0o600)


if __name__ == "__main__":
    main()
