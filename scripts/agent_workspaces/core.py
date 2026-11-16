from __future__ import annotations

import hashlib
import ipaddress
import json
import pathlib
import posixpath
import re
import stat
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any


class WorkspaceError(ValueError):
    pass


_TOP_LEVEL_KEYS = {"schema_version", "hosts", "workspaces"}
_HOST_LIMIT_KEYS = {
    "max_enabled_workspaces",
    "max_memory_mib",
    "max_cpu_percent",
    "max_disk_gib",
}
_WORKSPACE_KEYS = {
    "id",
    "owner",
    "trust_class",
    "host",
    "enabled",
    "image",
    "resources",
    "network",
    "storage",
    "ssh_authorized_keys",
    "browser_identity",
    "guest_username",
    "secret_references",
}
_IMAGE_KEYS = {"url", "sha256"}
_RESOURCE_KEYS = {"vcpus", "memory_mib", "cpu_limit_percent", "io_weight"}
_NETWORK_KEYS = {
    "segment",
    "address",
    "gateway",
    "mac",
    "bridge",
    "tap",
    "uplink",
    "dns",
    "ntp",
    "ha_api",
    "ingress_sources",
}
_STORAGE_KEYS = {"system_disk", "system_disk_gib", "data_disk", "data_disk_gib"}
_TRUST_CLASSES = {"owner", "invited"}
_ID_RE = re.compile(r"[a-z][a-z0-9-]{1,30}[a-z0-9]\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_MAC_RE = re.compile(
    r"02:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}\Z"
)
_IFACE_RE = re.compile(r"[a-zA-Z0-9_.-]{1,15}\Z")


@dataclass(frozen=True)
class Workspace:
    raw: dict[str, Any]

    @property
    def id(self) -> str:
        return self.raw["id"]

    @property
    def domain_name(self) -> str:
        return f"agent-{self.id}"


def _object(value: Any, context: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkspaceError(f"{context} must be an object")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise WorkspaceError(f"{context} has unknown fields: {', '.join(unknown)}")
    return value


def _required(value: dict[str, Any], context: str, fields: set[str]) -> None:
    missing = sorted(field for field in fields if field not in value)
    if missing:
        raise WorkspaceError(f"{context} is missing fields: {', '.join(missing)}")


def _positive_int(value: Any, context: str, *, minimum: int, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise WorkspaceError(
            f"{context} must be an integer from {minimum} through {maximum}"
        )
    return value


def _path_beneath(value: Any, context: str, root: pathlib.PurePosixPath) -> str:
    if not isinstance(value, str):
        raise WorkspaceError(f"{context} must be a path beneath {root}")
    path = pathlib.PurePosixPath(value)
    canonical = posixpath.normpath(value)
    normalized = pathlib.PurePosixPath(canonical)
    if (
        not path.is_absolute()
        or value.startswith("//")
        or value != canonical
        or path != normalized
        or path == root
    ):
        raise WorkspaceError(f"{context} must be a normalized path beneath {root}")
    if not path.is_relative_to(root):
        raise WorkspaceError(f"{context} must be beneath {root}")
    return canonical


def load_manifest(path: pathlib.Path) -> list[Workspace]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkspaceError(f"cannot read workspace manifest: {error}") from error
    document = _object(document, "manifest", _TOP_LEVEL_KEYS)
    _required(document, "manifest", _TOP_LEVEL_KEYS)
    if document["schema_version"] != 1:
        raise WorkspaceError("manifest schema_version must be 1")
    records = document["workspaces"]
    if not isinstance(records, list):
        raise WorkspaceError("manifest workspaces must be an array")
    workspaces = [
        _validate_workspace(record, index) for index, record in enumerate(records)
    ]
    _validate_unique(workspaces)
    _validate_host_limits(document["hosts"], workspaces)
    return workspaces


def _validate_host_limits(value: Any, workspaces: list[Workspace]) -> None:
    hosts = _object(
        value, "manifest.hosts", set(value) if isinstance(value, dict) else set()
    )
    for host, raw_limits in hosts.items():
        limits = _object(raw_limits, f"manifest.hosts.{host}", _HOST_LIMIT_KEYS)
        _required(limits, f"manifest.hosts.{host}", _HOST_LIMIT_KEYS)
        _positive_int(
            limits["max_enabled_workspaces"],
            f"manifest.hosts.{host}.max_enabled_workspaces",
            minimum=1,
            maximum=4,
        )
        _positive_int(
            limits["max_memory_mib"],
            f"manifest.hosts.{host}.max_memory_mib",
            minimum=2048,
            maximum=65536,
        )
        _positive_int(
            limits["max_cpu_percent"],
            f"manifest.hosts.{host}.max_cpu_percent",
            minimum=1,
            maximum=3200,
        )
        _positive_int(
            limits["max_disk_gib"],
            f"manifest.hosts.{host}.max_disk_gib",
            minimum=32,
            maximum=4096,
        )
    enabled = [workspace for workspace in workspaces if workspace.raw["enabled"]]
    unknown = sorted({workspace.raw["host"] for workspace in enabled} - set(hosts))
    if unknown:
        raise WorkspaceError(
            f"enabled workspaces reference hosts without limits: {', '.join(unknown)}"
        )
    for host, limits in hosts.items():
        selected = [workspace for workspace in enabled if workspace.raw["host"] == host]
        totals = {
            "enabled workspaces": len(selected),
            "memory MiB": sum(
                workspace.raw["resources"]["memory_mib"] for workspace in selected
            ),
            "CPU percent": sum(
                workspace.raw["resources"]["cpu_limit_percent"]
                for workspace in selected
            ),
            "disk GiB": sum(
                workspace.raw["storage"]["system_disk_gib"]
                + workspace.raw["storage"]["data_disk_gib"]
                for workspace in selected
            ),
        }
        maximums = {
            "enabled workspaces": limits["max_enabled_workspaces"],
            "memory MiB": limits["max_memory_mib"],
            "CPU percent": limits["max_cpu_percent"],
            "disk GiB": limits["max_disk_gib"],
        }
        for resource, total in totals.items():
            if total > maximums[resource]:
                raise WorkspaceError(
                    f"host {host} exceeds {resource} limit: {total} > {maximums[resource]}"
                )


def _validate_workspace(value: Any, index: int) -> Workspace:
    context = f"workspaces[{index}]"
    item = _object(value, context, _WORKSPACE_KEYS)
    _required(item, context, {"id", "trust_class", "host", "enabled"})
    if not isinstance(item["id"], str) or not _ID_RE.fullmatch(item["id"]):
        raise WorkspaceError(f"{context}.id is invalid")
    if item["trust_class"] not in _TRUST_CLASSES:
        raise WorkspaceError(f"{context}.trust_class must be owner or invited")
    if not isinstance(item["host"], str) or not item["host"]:
        raise WorkspaceError(f"{context}.host must be a non-empty string")
    if not isinstance(item["enabled"], bool):
        raise WorkspaceError(f"{context}.enabled must be a boolean")
    if not item["enabled"]:
        forbidden = sorted(
            set(item) - {"id", "trust_class", "host", "enabled", "owner"}
        )
        if forbidden:
            raise WorkspaceError(
                f"{context} is disabled but has enrollment fields: {', '.join(forbidden)}"
            )
        return Workspace(item)

    _required(item, context, _WORKSPACE_KEYS)
    if not isinstance(item["owner"], str) or not item["owner"].strip():
        raise WorkspaceError(f"{context}.owner must be an enrolled identity")
    image = _object(item["image"], f"{context}.image", _IMAGE_KEYS)
    _required(image, f"{context}.image", _IMAGE_KEYS)
    if not isinstance(image["url"], str) or not image["url"].startswith("https://"):
        raise WorkspaceError(f"{context}.image.url must use HTTPS")
    if not isinstance(image["sha256"], str) or not _SHA256_RE.fullmatch(
        image["sha256"]
    ):
        raise WorkspaceError(
            f"{context}.image.sha256 must be a lowercase SHA-256 digest"
        )

    resources = _object(item["resources"], f"{context}.resources", _RESOURCE_KEYS)
    _required(resources, f"{context}.resources", _RESOURCE_KEYS)
    _positive_int(
        resources["vcpus"], f"{context}.resources.vcpus", minimum=1, maximum=8
    )
    _positive_int(
        resources["memory_mib"],
        f"{context}.resources.memory_mib",
        minimum=2048,
        maximum=16384,
    )
    _positive_int(
        resources["cpu_limit_percent"],
        f"{context}.resources.cpu_limit_percent",
        minimum=1,
        maximum=800,
    )
    _positive_int(
        resources["io_weight"],
        f"{context}.resources.io_weight",
        minimum=100,
        maximum=1000,
    )

    network = _object(item["network"], f"{context}.network", _NETWORK_KEYS)
    _required(network, f"{context}.network", _NETWORK_KEYS)
    try:
        segment = ipaddress.ip_network(network["segment"], strict=True)
        address = ipaddress.ip_interface(network["address"])
        gateway = ipaddress.ip_address(network["gateway"])
        ha_api = ipaddress.ip_address(network["ha_api"])
        dns = [ipaddress.ip_address(value) for value in network["dns"]]
        ntp = [ipaddress.ip_address(value) for value in network["ntp"]]
        ingress_sources = [
            ipaddress.ip_network(value, strict=True)
            for value in network["ingress_sources"]
        ]
    except (TypeError, ValueError) as error:
        raise WorkspaceError(
            f"{context}.network has an invalid address: {error}"
        ) from error
    if segment.version != 4 or address.version != 4 or address.ip not in segment:
        raise WorkspaceError(f"{context}.network.address must be IPv4 within segment")
    if address.network.prefixlen != segment.prefixlen:
        raise WorkspaceError(f"{context}.network.address must use the segment prefix")
    if address.ip in {segment.network_address, segment.broadcast_address}:
        raise WorkspaceError(f"{context}.network.address must be a usable host address")
    if gateway.version != 4 or gateway not in segment or gateway == address.ip:
        raise WorkspaceError(
            f"{context}.network.gateway must be another IPv4 address in segment"
        )
    if ha_api.version != 4:
        raise WorkspaceError(f"{context}.network.ha_api must be IPv4")
    if not dns or not ntp or any(value.version != 4 for value in [*dns, *ntp]):
        raise WorkspaceError(
            f"{context}.network.dns and ntp must be non-empty IPv4 arrays"
        )
    if not ingress_sources or any(value.version != 4 for value in ingress_sources):
        raise WorkspaceError(
            f"{context}.network.ingress_sources must be non-empty IPv4 networks"
        )
    if not isinstance(network["mac"], str) or not _MAC_RE.fullmatch(network["mac"]):
        raise WorkspaceError(
            f"{context}.network.mac must be a locally administered unicast MAC"
        )
    for field in ("bridge", "tap", "uplink"):
        if not isinstance(network[field], str) or not _IFACE_RE.fullmatch(
            network[field]
        ):
            raise WorkspaceError(
                f"{context}.network.{field} is not a valid interface name"
            )

    storage = _object(item["storage"], f"{context}.storage", _STORAGE_KEYS)
    _required(storage, f"{context}.storage", _STORAGE_KEYS)
    disk_root = (
        pathlib.PurePosixPath("/persist/agent-workspaces") / item["id"] / "disks"
    )
    for field in ("system_disk", "data_disk"):
        storage[field] = _path_beneath(
            storage[field], f"{context}.storage.{field}", disk_root
        )
    _positive_int(
        storage["system_disk_gib"],
        f"{context}.storage.system_disk_gib",
        minimum=16,
        maximum=512,
    )
    _positive_int(
        storage["data_disk_gib"],
        f"{context}.storage.data_disk_gib",
        minimum=16,
        maximum=2048,
    )

    keys = item["ssh_authorized_keys"]
    if (
        not isinstance(keys, list)
        or not keys
        or not all(
            isinstance(key, str)
            and key.startswith(("ssh-ed25519 ", "sk-ssh-ed25519@openssh.com "))
            for key in keys
        )
    ):
        raise WorkspaceError(
            f"{context}.ssh_authorized_keys must contain enrolled Ed25519 public keys"
        )
    if (
        not isinstance(item["browser_identity"], str)
        or "@" not in item["browser_identity"]
    ):
        raise WorkspaceError(f"{context}.browser_identity must be an enrolled identity")
    if not isinstance(item["guest_username"], str) or not re.fullmatch(
        r"[a-z_][a-z0-9_-]{0,30}", item["guest_username"]
    ):
        raise WorkspaceError(f"{context}.guest_username is invalid")
    refs = item["secret_references"]
    if not isinstance(refs, list):
        raise WorkspaceError(
            f"{context}.secret_references must be an array of runtime paths"
        )
    secret_root = (
        pathlib.PurePosixPath("/run/credentials/agent-workspaces") / item["id"]
    )
    for ref_index, ref in enumerate(refs):
        refs[ref_index] = _path_beneath(
            ref, f"{context}.secret_references[{ref_index}]", secret_root
        )
    return Workspace(item)


def _validate_unique(workspaces: list[Workspace]) -> None:
    fields = {
        "id": lambda item: item.id,
        "owner": lambda item: item.raw.get("owner"),
        "browser identity": lambda item: item.raw.get(
            "browser_identity", ""
        ).casefold(),
        "network address": lambda item: (
            item.raw.get("network", {}).get("address", "").split("/")[0]
        ),
        "network segment": lambda item: item.raw.get("network", {}).get("segment"),
        "MAC": lambda item: item.raw.get("network", {}).get("mac"),
        "bridge": lambda item: item.raw.get("network", {}).get("bridge"),
        "tap": lambda item: item.raw.get("network", {}).get("tap"),
    }
    for label, getter in fields.items():
        values = [
            getter(item) for item in workspaces if item.raw["enabled"] or label == "id"
        ]
        values = [value for value in values if value]
        if len(values) != len(set(values)):
            raise WorkspaceError(f"duplicate workspace {label}")
    enabled = [item for item in workspaces if item.raw["enabled"]]
    segments = [
        (item.id, ipaddress.ip_network(item.raw["network"]["segment"]))
        for item in enabled
    ]
    for index, (first_id, first) in enumerate(segments):
        for second_id, second in segments[index + 1 :]:
            if first.overlaps(second):
                raise WorkspaceError(
                    f"workspace network segments overlap: {first_id} and {second_id}"
                )
    disk_paths: dict[str, str] = {}
    for item in enabled:
        for role in ("system_disk", "data_disk"):
            path = item.raw["storage"][role]
            if path in disk_paths:
                raise WorkspaceError(
                    f"workspace disk path is reused by {disk_paths[path]} and {item.id}.{role}"
                )
            disk_paths[path] = f"{item.id}.{role}"


def validate_provisioning_paths(
    workspace: Workspace,
    *,
    expected_uid: int,
    expected_gid: int,
    trusted_root_gid: int,
    trusted_root_uid: int = 0,
    trusted_root: pathlib.Path = pathlib.Path("/persist/agent-workspaces"),
) -> None:
    root_metadata = trusted_root.lstat()
    if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode):
        raise WorkspaceError(
            f"trusted storage root is not a real directory: {trusted_root}"
        )
    if (root_metadata.st_uid, root_metadata.st_gid) != (
        trusted_root_uid,
        trusted_root_gid,
    ):
        raise WorkspaceError(
            f"trusted storage root has unexpected ownership: {trusted_root}"
        )
    if root_metadata.st_mode & 0o022:
        raise WorkspaceError(
            f"trusted storage root is group/world writable: {trusted_root}"
        )

    for field in ("system_disk", "data_disk"):
        path = pathlib.Path(workspace.raw["storage"][field])
        if not path.is_relative_to(trusted_root) or path == trusted_root:
            raise WorkspaceError(f"provisioning path escapes trusted root: {path}")
        relative = path.relative_to(trusted_root)
        candidates = [
            trusted_root.joinpath(*relative.parts[:index])
            for index in range(1, len(relative.parts) + 1)
        ]
        for index, candidate in enumerate(candidates):
            if not candidate.exists() and not candidate.is_symlink():
                break
            metadata = candidate.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise WorkspaceError(
                    f"provisioning path traverses symlink: {candidate}"
                )
            if (metadata.st_uid, metadata.st_gid) != (expected_uid, expected_gid):
                raise WorkspaceError(
                    f"provisioning path has unexpected ownership: {candidate}"
                )
            if metadata.st_mode & 0o022:
                raise WorkspaceError(
                    f"provisioning path is group/world writable: {candidate}"
                )
            is_disk = index == len(candidates) - 1
            if is_disk and not stat.S_ISREG(metadata.st_mode):
                raise WorkspaceError(f"existing disk is not a regular file: {path}")
            if not is_disk and not stat.S_ISDIR(metadata.st_mode):
                raise WorkspaceError(
                    f"provisioning path component is not a directory: {candidate}"
                )


def render_domain(workspace: Workspace) -> str:
    item = workspace.raw
    if not item["enabled"]:
        raise WorkspaceError(f"workspace {workspace.id} is disabled")
    resources = item["resources"]
    storage = item["storage"]
    network = item["network"]
    workspace_root = pathlib.PurePosixPath(storage["system_disk"]).parent.parent
    domain = ET.Element("domain", {"type": "kvm"})
    ET.SubElement(domain, "name").text = workspace.domain_name
    ET.SubElement(domain, "uuid").text = deterministic_uuid(workspace.id)
    ET.SubElement(domain, "memory", {"unit": "MiB"}).text = str(resources["memory_mib"])
    ET.SubElement(domain, "vcpu", {"placement": "static"}).text = str(
        resources["vcpus"]
    )
    resource = ET.SubElement(domain, "resource")
    ET.SubElement(resource, "partition").text = "/machine/agent-workspaces"
    cpu_tune = ET.SubElement(domain, "cputune")
    ET.SubElement(cpu_tune, "global_period").text = "100000"
    ET.SubElement(cpu_tune, "global_quota").text = str(
        resources["cpu_limit_percent"] * 1000
    )
    block_tune = ET.SubElement(domain, "blkiotune")
    ET.SubElement(block_tune, "weight").text = str(resources["io_weight"])
    ET.SubElement(domain, "cpu", {"mode": "host-passthrough", "check": "none"})
    os_node = ET.SubElement(domain, "os")
    ET.SubElement(os_node, "type", {"arch": "x86_64", "machine": "q35"}).text = "hvm"
    features = ET.SubElement(domain, "features")
    ET.SubElement(features, "acpi")
    ET.SubElement(features, "apic")
    devices = ET.SubElement(domain, "devices")
    for path, target in (
        (storage["system_disk"], "vda"),
        (storage["data_disk"], "vdb"),
    ):
        disk = ET.SubElement(devices, "disk", {"type": "file", "device": "disk"})
        ET.SubElement(
            disk,
            "driver",
            # Pass guest TRIM through so blocks freed inside the guest shrink
            # the qcow2 file on the host's shared /persist.
            {
                "name": "qemu",
                "type": "qcow2",
                "cache": "none",
                "io": "native",
                "discard": "unmap",
                "detect_zeroes": "unmap",
            },
        )
        ET.SubElement(disk, "source", {"file": path})
        ET.SubElement(disk, "target", {"dev": target, "bus": "virtio"})
    seed = ET.SubElement(devices, "disk", {"type": "file", "device": "cdrom"})
    ET.SubElement(seed, "driver", {"name": "qemu", "type": "raw"})
    ET.SubElement(
        seed,
        "source",
        {"file": str(workspace_root / "control" / "seed.iso")},
    )
    ET.SubElement(seed, "target", {"dev": "sda", "bus": "sata"})
    ET.SubElement(seed, "readonly")
    interface = ET.SubElement(devices, "interface", {"type": "bridge"})
    ET.SubElement(interface, "mac", {"address": network["mac"]})
    ET.SubElement(interface, "source", {"bridge": network["bridge"]})
    ET.SubElement(interface, "target", {"dev": network["tap"]})
    ET.SubElement(interface, "model", {"type": "virtio"})
    ET.indent(domain, space="  ")
    return ET.tostring(domain, encoding="unicode", short_empty_elements=True) + "\n"


def deterministic_uuid(workspace_id: str) -> str:
    digest = hashlib.sha256(f"agent-workspace:{workspace_id}".encode()).hexdigest()
    return (
        f"{digest[:8]}-{digest[8:12]}-5{digest[13:16]}-a{digest[17:20]}-{digest[20:32]}"
    )


def render_plan(workspaces: list[Workspace]) -> dict[str, Any]:
    enabled = [workspace for workspace in workspaces if workspace.raw["enabled"]]
    return {
        "schema_version": 1,
        "kind": "agent-workspace-plan",
        "workspaces": [
            {
                "id": workspace.id,
                "domain": workspace.domain_name,
                "uuid": deterministic_uuid(workspace.id),
                "host": workspace.raw["host"],
                "image_sha256": workspace.raw["image"]["sha256"],
                "system_disk": workspace.raw["storage"]["system_disk"],
                "data_disk": workspace.raw["storage"]["data_disk"],
                "domain_xml": render_domain(workspace),
            }
            for workspace in sorted(enabled, key=lambda candidate: candidate.id)
        ],
    }
