from __future__ import annotations

import grp
import hashlib
import json
import os
import pathlib
import pwd
import socket
import subprocess
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from typing import Any

import yaml

from .core import (
    Workspace,
    WorkspaceError,
    deterministic_uuid,
    render_domain,
    validate_provisioning_paths,
)

Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run(runner: Runner, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    result = runner(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise WorkspaceError(f"command failed ({' '.join(command)}): {detail}")
    return result


def _manifest_hash(workspace: Workspace) -> str:
    raw = json.dumps(workspace.raw, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _file_hash(path: pathlib.Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def _emulator_projection(element: ET.Element | None) -> str:
    expected = "qemu-system-x86_64"
    if element is None:
        return expected
    value = element.text or ""
    path = pathlib.PurePosixPath(value)
    normalized = os.path.normpath(value)
    in_runtime_directory = path.parent == pathlib.PurePosixPath(
        "/run/libvirt/nix-emulators"
    )
    in_nix_store = (
        len(path.parts) >= 5
        and path.parts[1:3] == ("nix", "store")
        and path.parent.name == "bin"
    )
    if (
        not path.is_absolute()
        or value != normalized
        or path.name != expected
        or not (in_runtime_directory or in_nix_store)
    ):
        raise WorkspaceError(f"domain XML has an unsafe emulator path: {value}")
    return expected


def _is_safe_implicit_device(device: ET.Element) -> bool:
    if device.tag in {"controller", "input", "memballoon", "panic", "video"}:
        return True
    if device.tag == "audio":
        return device.get("type") == "none"
    if device.tag == "watchdog":
        return device.get("model") == "itco" and device.get("action") == "reset"
    return False


def _domain_projection(xml: str) -> dict[str, Any]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as error:
        raise WorkspaceError(f"invalid domain XML: {error}") from error
    disks = []
    for disk in root.findall("./devices/disk"):
        source = disk.find("source")
        target = disk.find("target")
        driver = disk.find("driver")
        disks.append(
            {
                "device": disk.get("device"),
                "source": source.get("file") if source is not None else None,
                "target": target.get("dev") if target is not None else None,
                "driver": driver.get("type") if driver is not None else None,
            }
        )
    interfaces = []
    for interface in root.findall("./devices/interface"):
        source = interface.find("source")
        mac = interface.find("mac")
        target = interface.find("target")
        model = interface.find("model")
        interfaces.append(
            {
                "type": interface.get("type"),
                "bridge": source.get("bridge") if source is not None else None,
                "mac": mac.get("address") if mac is not None else None,
                "tap": target.get("dev") if target is not None else None,
                "model": model.get("type") if model is not None else None,
            }
        )
    if not interfaces:
        raise WorkspaceError("domain XML has no workspace interface")
    devices = root.find("devices")
    unexpected_devices = []
    if devices is not None:
        unexpected_devices = sorted(
            ET.tostring(device, encoding="unicode")
            for device in devices
            if device.tag not in {"disk", "emulator", "interface"}
            and not _is_safe_implicit_device(device)
        )
    return {
        "name": root.findtext("name"),
        "uuid": root.findtext("uuid"),
        "memory_kib": _memory_kib(root.find("memory")),
        "vcpus": root.findtext("vcpu"),
        "global_period": root.findtext("./cputune/global_period"),
        "global_quota": root.findtext("./cputune/global_quota"),
        "io_weight": root.findtext("./blkiotune/weight"),
        "disks": disks,
        "interfaces": interfaces,
        "unexpected_devices": unexpected_devices,
        "emulator": _emulator_projection(root.find("./devices/emulator")),
    }


def _memory_kib(element: ET.Element | None) -> int | None:
    if element is None or element.text is None:
        return None
    multipliers = {"KiB": 1, "MiB": 1024, "GiB": 1024 * 1024}
    unit = element.get("unit", "KiB")
    if unit not in multipliers:
        raise WorkspaceError(f"unsupported domain memory unit: {unit}")
    return int(element.text) * multipliers[unit]


def _domain_uuid(workspace: Workspace, runner: Runner) -> str | None:
    result = runner(
        ["virsh", "domuuid", workspace.domain_name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    if (
        "failed to get domain" in result.stderr.lower()
        or "not found" in result.stderr.lower()
    ):
        return None
    raise WorkspaceError(
        f"cannot inspect domain {workspace.domain_name}: {result.stderr.strip()}"
    )


def provision_status(
    workspace: Workspace, runner: Runner = subprocess.run
) -> dict[str, Any]:
    workspace_root = pathlib.Path(workspace.raw["storage"]["system_disk"]).parent.parent
    control_directory = workspace_root / "control"
    receipt_path = control_directory / "provisioning.json"
    domain_uuid = _domain_uuid(workspace, runner)
    disks = {
        field: pathlib.Path(workspace.raw["storage"][field]).exists()
        for field in ("system_disk", "data_disk")
    }
    receipt: dict[str, Any] | None = None
    if receipt_path.exists():
        try:
            loaded = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise WorkspaceError(f"invalid provisioning receipt: {error}") from error
        if not isinstance(loaded, dict):
            raise WorkspaceError("invalid provisioning receipt: expected an object")
        receipt = loaded
    expected_uuid = deterministic_uuid(workspace.id)
    domain_path = control_directory / "domain.xml"
    saved_xml_matches = False
    saved_projection: dict[str, Any] | None = None
    if domain_path.is_file() and not domain_path.is_symlink() and receipt is not None:
        saved_xml_matches = receipt.get("domain_xml_sha256") == _file_hash(domain_path)
        if saved_xml_matches:
            saved_projection = _domain_projection(
                domain_path.read_text(encoding="utf-8")
            )
    persistent_projection: dict[str, Any] | None = None
    active_projection: dict[str, Any] | None = None
    domain_state: str | None = None
    if domain_uuid is not None:
        persistent_xml = _run(
            runner, ["virsh", "dumpxml", "--inactive", workspace.domain_name]
        ).stdout
        persistent_projection = _domain_projection(persistent_xml)
        domain_state = (
            _run(runner, ["virsh", "domstate", workspace.domain_name])
            .stdout.strip()
            .lower()
        )
        if domain_state != "shut off":
            active_xml = _run(
                runner, ["virsh", "dumpxml", workspace.domain_name]
            ).stdout
            active_projection = _domain_projection(active_xml)
    expected_projection = _domain_projection(render_domain(workspace))
    configuration_match = (
        saved_projection == expected_projection
        and (
            persistent_projection is None
            or persistent_projection == expected_projection
        )
        and (active_projection is None or active_projection == expected_projection)
    )
    assets_match = (
        receipt is not None
        and receipt.get("manifest_sha256") == _manifest_hash(workspace)
        and receipt.get("domain_uuid") == expected_uuid
        and all(disks.values())
        and saved_xml_matches
        and configuration_match
    )
    consistent = assets_match and domain_uuid == expected_uuid
    return {
        "id": workspace.id,
        "domain": workspace.domain_name,
        "domain_uuid": domain_uuid,
        "domain_state": domain_state,
        "expected_uuid": expected_uuid,
        "disks": disks,
        "receipt": receipt is not None,
        "assets_match": assets_match,
        "configuration_match": configuration_match,
        "consistent": consistent,
    }


def _download_verified(url: str, digest: str, destination: pathlib.Path) -> None:
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
        temporary_path = pathlib.Path(temporary.name)
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                while chunk := response.read(1024 * 1024):
                    temporary.write(chunk)
            temporary.flush()
            os.fsync(temporary.fileno())
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
    hasher = hashlib.sha256()
    with temporary_path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            hasher.update(chunk)
    actual = hasher.hexdigest()
    if actual != digest:
        temporary_path.unlink(missing_ok=True)
        raise WorkspaceError(f"image digest mismatch: expected {digest}, got {actual}")
    os.chmod(temporary_path, 0o440)
    temporary_path.replace(destination)


def _cloud_init(workspace: Workspace) -> tuple[str, str, str]:
    user_data = {
        "users": [
            {
                "name": workspace.raw["guest_username"],
                "groups": ["sudo"],
                "sudo": "ALL=(ALL) NOPASSWD:ALL",
                "shell": "/bin/bash",
                "ssh_authorized_keys": workspace.raw["ssh_authorized_keys"],
            }
        ],
        "ssh_pwauth": False,
        "disable_root": True,
        "package_update": True,
        "packages": ["openssh-server", "tmux", "podman", "uidmap"],
        "runcmd": [
            ["loginctl", "enable-linger", workspace.raw["guest_username"]],
            ["systemctl", "enable", "--now", "ssh"],
            # Weekly TRIM returns freed blocks to the host (disks use discard).
            ["systemctl", "enable", "--now", "fstrim.timer"],
        ],
    }
    metadata = {
        "instance-id": deterministic_uuid(workspace.id),
        "local-hostname": workspace.domain_name,
    }
    network = workspace.raw["network"]
    network_data = {
        "version": 2,
        "ethernets": {
            "workspace": {
                "match": {"macaddress": network["mac"]},
                "set-name": "workspace0",
                "addresses": [network["address"]],
                "routes": [{"to": "0.0.0.0/0", "via": network["gateway"]}],
                "nameservers": {"addresses": network["dns"]},
                "dhcp4": False,
                "dhcp6": False,
                "accept-ra": False,
            }
        },
    }
    return (
        "#cloud-config\n" + yaml.safe_dump(user_data, sort_keys=True),
        yaml.safe_dump(metadata, sort_keys=True),
        yaml.safe_dump(network_data, sort_keys=True),
    )


def provision_workspace(
    workspace: Workspace,
    *,
    authorized: bool,
    runner: Runner = subprocess.run,
    storage_root: pathlib.Path = pathlib.Path("/persist/agent-workspaces"),
) -> dict[str, Any]:
    if not authorized:
        raise WorkspaceError("provisioning requires the explicit --authorized flag")
    if not workspace.raw["enabled"]:
        raise WorkspaceError(f"workspace {workspace.id} is disabled")
    if os.geteuid() != 0:
        raise WorkspaceError("provisioning must run as root on the selected host")
    if socket.gethostname() != workspace.raw["host"]:
        raise WorkspaceError(
            f"workspace targets {workspace.raw['host']}, current host is {socket.gethostname()}"
        )
    qemu_user = pwd.getpwnam("qemu-libvirtd")
    storage_group = grp.getgrnam("agent-workspace-storage")
    validate_provisioning_paths(
        workspace,
        expected_uid=qemu_user.pw_uid,
        expected_gid=storage_group.gr_gid,
        trusted_root_uid=0,
        trusted_root_gid=storage_group.gr_gid,
        trusted_root=storage_root,
    )
    disk_directory = pathlib.Path(workspace.raw["storage"]["system_disk"]).parent
    workspace_root = disk_directory.parent
    control_directory = workspace_root / "control"
    status = provision_status(workspace, runner)
    if status["consistent"]:
        return {"status": "unchanged", **status}
    if status["assets_match"] and status["domain_uuid"] is None:
        _run(runner, ["systemctl", "is-active", "nftables.service"])
        _run(runner, ["networkctl", "status", workspace.raw["network"]["bridge"]])
        domain_path = control_directory / "domain.xml"
        if not domain_path.is_file() or domain_path.is_symlink():
            raise WorkspaceError("saved domain XML is missing or unsafe")
        _run(runner, ["virsh", "define", str(domain_path)])
        _run(runner, ["virsh", "autostart", workspace.domain_name])
        _run(runner, ["virsh", "start", workspace.domain_name])
        return {"status": "reprovisioned", **provision_status(workspace, runner)}
    if (
        status["domain_uuid"] is not None
        or any(status["disks"].values())
        or status["receipt"]
    ):
        raise WorkspaceError(
            "refusing to adopt or replace an existing domain, disk, or receipt"
        )
    _run(runner, ["systemctl", "is-active", "nftables.service"])
    _run(runner, ["networkctl", "status", workspace.raw["network"]["bridge"]])

    workspace_root.mkdir(mode=0o710)
    disk_directory.mkdir(mode=0o700)
    control_directory.mkdir(mode=0o700)
    for directory in (workspace_root, disk_directory, control_directory):
        os.chown(directory, qemu_user.pw_uid, storage_group.gr_gid)
    image = workspace.raw["image"]
    image_cache = control_directory / "base-image.qcow2"
    if not image_cache.exists():
        _download_verified(image["url"], image["sha256"], image_cache)
    system_disk = pathlib.Path(workspace.raw["storage"]["system_disk"])
    data_disk = pathlib.Path(workspace.raw["storage"]["data_disk"])
    _run(
        runner,
        [
            "qemu-img",
            "create",
            "-f",
            "qcow2",
            "-F",
            "qcow2",
            "-b",
            str(image_cache),
            str(system_disk),
            f"{workspace.raw['storage']['system_disk_gib']}G",
        ],
    )
    _run(
        runner,
        [
            "qemu-img",
            "create",
            "-f",
            "qcow2",
            str(data_disk),
            f"{workspace.raw['storage']['data_disk_gib']}G",
        ],
    )
    user_data, metadata, network_data = _cloud_init(workspace)
    user_path = control_directory / "user-data"
    metadata_path = control_directory / "meta-data"
    network_path = control_directory / "network-config"
    user_path.write_text(user_data, encoding="utf-8")
    metadata_path.write_text(metadata, encoding="utf-8")
    network_path.write_text(network_data, encoding="utf-8")
    os.chmod(user_path, 0o600)
    os.chmod(metadata_path, 0o600)
    os.chmod(network_path, 0o600)
    seed_path = control_directory / "seed.iso"
    _run(
        runner,
        [
            "cloud-localds",
            f"--network-config={network_path}",
            str(seed_path),
            str(user_path),
            str(metadata_path),
        ],
    )
    for path in (
        image_cache,
        system_disk,
        data_disk,
        seed_path,
        user_path,
        metadata_path,
        network_path,
    ):
        os.chown(path, qemu_user.pw_uid, storage_group.gr_gid)
    domain_path = control_directory / "domain.xml"
    domain_path.write_text(render_domain(workspace), encoding="utf-8")
    os.chmod(domain_path, 0o600)
    os.chown(domain_path, qemu_user.pw_uid, storage_group.gr_gid)
    _run(runner, ["virsh", "define", str(domain_path)])
    _run(runner, ["virsh", "autostart", workspace.domain_name])
    _run(runner, ["virsh", "start", workspace.domain_name])
    receipt = {
        "schema_version": 1,
        "workspace_id": workspace.id,
        "domain_uuid": deterministic_uuid(workspace.id),
        "manifest_sha256": _manifest_hash(workspace),
        "image_sha256": image["sha256"],
        "domain_xml_sha256": _file_hash(domain_path),
    }
    receipt_path = control_directory / "provisioning.json"
    temporary = control_directory / ".provisioning.json.tmp"
    temporary.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.chown(temporary, qemu_user.pw_uid, storage_group.gr_gid)
    temporary.replace(receipt_path)
    return {"status": "provisioned", **provision_status(workspace, runner)}


def deprovision_workspace(
    workspace: Workspace,
    *,
    authorized: bool,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    if not authorized:
        raise WorkspaceError("deprovisioning requires the explicit --authorized flag")
    status = provision_status(workspace, runner)
    if not status["consistent"]:
        raise WorkspaceError(
            "refusing to deprovision an unknown or inconsistent domain"
        )
    state = _run(runner, ["virsh", "domstate", workspace.domain_name]).stdout.strip()
    if state != "shut off":
        _run(runner, ["virsh", "destroy", workspace.domain_name])
    _run(runner, ["virsh", "undefine", workspace.domain_name])
    result = provision_status(workspace, runner)
    if result["domain_uuid"] is not None or not result["assets_match"]:
        raise WorkspaceError(
            "domain removal did not preserve the verified workspace assets"
        )
    return {"status": "deprovisioned", **result}
