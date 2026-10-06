import argparse
import ipaddress
import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from opnsense_reconciler.inventory import Credentials, HttpsClient


class Reader(Protocol):
    def get(self, path: str) -> object: ...


class Client(Reader, Protocol):
    def post(self, path: str, payload: object) -> object: ...


def reconcile_interfaces(
    client: Client,
    assignment_api_available: bool,
    desired_interfaces: list[dict[str, object]],
) -> None:
    if not assignment_api_available:
        raise RuntimeError("assignment API unavailable")
    resolved_interfaces = (
        resolve_vlan_devices(
            desired_interfaces,
            client.get("/api/interfaces/vlan_settings/search_item"),
        )
        if desired_interfaces
        else []
    )
    assignments = client.get("/api/interfaces/assignment/search_item")
    if not resolved_interfaces:
        return
    existing_devices = _assignment_devices(assignments)
    additions = [
        desired
        for desired in resolved_interfaces
        if desired["if"] not in existing_devices
    ]
    for desired in additions:
        client.post("/api/interfaces/assignment/add_item", {"interface": desired})
    if additions:
        client.post("/api/interfaces/assignment/reconfigure", {})
    runtime = _runtime_interfaces(
        client.get("/api/interfaces/overview/interfaces_info/true")
    )
    mismatches = [
        desired
        for desired in resolved_interfaces
        if runtime.get(desired["if"]) != (desired.get("ipaddr"), desired.get("descr"))
    ]
    if mismatches:
        devices = ", ".join(str(desired["if"]) for desired in mismatches)
        raise RuntimeError(
            "interface runtime drift for "
            f"{devices}; OPNsense 26.7 assignment API cannot configure static L3 fields"
        )


def reconcile_kea_interfaces(client: Client, desired_interfaces: set[str]) -> None:
    current = client.get("/api/kea/dhcpv4/get")
    if _kea_interfaces(current) != desired_interfaces:
        client.post(
            "/api/kea/dhcpv4/set",
            {
                "dhcpv4": {
                    "general": {
                        "enabled": "1",
                        "manual_config": "0",
                        "interfaces": ",".join(sorted(desired_interfaces)),
                        "valid_lifetime": "4000",
                        "fwrules": "1",
                        "dhcp_socket_type": "raw",
                    }
                }
            },
        )
        client.post("/api/kea/service/reconfigure", {})
    verified = client.get("/api/kea/dhcpv4/get")
    if _kea_interfaces(verified) != desired_interfaces:
        raise RuntimeError("Kea DHCPv4 interface verification failed")
    status = client.get("/api/kea/service/status")
    if not isinstance(status, dict) or status.get("status") != "running":
        raise RuntimeError("Kea DHCPv4 service is not running")


UDP_RELAY_FIELDS = (
    "enabled",
    "interfaces",
    "multicastaddress",
    "sourceaddress",
    "listenport",
    "InstanceID",
    "RevertTTL",
    "description",
)


def ensure_udp_broadcast_relay_plugin(client: Client) -> None:
    endpoint = "/api/udpbroadcastrelay/settings/search_relay"
    try:
        client.get(endpoint)
        return
    except RuntimeError as error:
        if "HTTP 404" not in str(error):
            raise
    _require_stored(client.post("/api/core/firmware/install/os-udpbroadcastrelay", {}))
    for _ in range(12):
        time.sleep(5)
        try:
            client.get(endpoint)
            return
        except RuntimeError as error:
            if "HTTP 404" not in str(error):
                raise
    raise RuntimeError("OPNsense UDP broadcast relay plugin did not become available")


def reconcile_udp_broadcast_relays(
    client: Client, desired_relays: list[dict[str, object]]
) -> None:
    current = client.get("/api/udpbroadcastrelay/settings/search_relay")
    rows = current.get("rows") if isinstance(current, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("UDP broadcast relay search returned no rows")
    live = {
        row["description"]: row
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("description"), str)
    }
    desired_by_description = {
        _required_string(relay, "description"): relay for relay in desired_relays
    }
    changed: list[str] = []
    unexpected = sorted(set(live) - set(desired_by_description))
    if unexpected:
        raise RuntimeError(
            "unexpected UDP broadcast relays require human-directed removal: "
            + ", ".join(unexpected)
        )
    for description, desired in desired_by_description.items():
        payload = {key: desired[key] for key in UDP_RELAY_FIELDS}
        existing = live.get(description)
        if existing is None:
            result = client.post(
                "/api/udpbroadcastrelay/settings/add_relay",
                {"udpbroadcastrelay": payload},
            )
        else:
            uuid = existing.get("uuid")
            if not isinstance(uuid, str) or not uuid:
                raise RuntimeError(f"UDP broadcast relay {description} has no UUID")
            current_values = {key: existing.get(key) for key in UDP_RELAY_FIELDS}
            if current_values == payload:
                continue
            result = client.post(
                f"/api/udpbroadcastrelay/settings/set_relay/{uuid}",
                {"udpbroadcastrelay": payload},
            )
        _require_stored(result)
        changed.append(description)
    verified = client.get("/api/udpbroadcastrelay/settings/search_relay")
    verified_rows = verified.get("rows") if isinstance(verified, dict) else None
    verified_by_description = {
        row["description"]: row
        for row in verified_rows or []
        if isinstance(row, dict) and isinstance(row.get("description"), str)
    }
    mismatches = [
        description
        for description, desired in desired_by_description.items()
        if {
            key: verified_by_description.get(description, {}).get(key)
            for key in UDP_RELAY_FIELDS
        }
        != {key: desired[key] for key in UDP_RELAY_FIELDS}
    ]
    if mismatches:
        raise RuntimeError(
            "UDP broadcast relay verification failed for " + ", ".join(mismatches)
        )
    # Settings writes do not start the daemon: restart each changed instance
    # so adds and edits take effect without waiting for a reboot.
    for description in changed:
        row = verified_by_description.get(description)
        uuid = row.get("uuid") if isinstance(row, dict) else None
        if not isinstance(uuid, str) or not uuid:
            raise RuntimeError(f"UDP broadcast relay {description} has no UUID")
        client.get(f"/api/udpbroadcastrelay/service/restart/{uuid}")
    for description, row in verified_by_description.items():
        uuid = row.get("uuid")
        if not isinstance(uuid, str) or not uuid:
            raise RuntimeError(f"UDP broadcast relay {description} has no UUID")
        status = client.get(f"/api/udpbroadcastrelay/service/status/{uuid}")
        if not isinstance(status, dict) or status.get("result") != "OK":
            raise RuntimeError(
                f"UDP broadcast relay {description} is not running: {status}. "
                "Check for a conflicting listener on the same port "
                "(e.g. mdns-repeater also binds UDP 5353)."
            )


def _required_string(item: dict[str, object], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"UDP broadcast relay requires {key}")
    return value


def resolve_vlan_devices(
    desired_interfaces: list[dict[str, object]],
    live_vlans: object,
) -> list[dict[str, object]]:
    rows = live_vlans.get("rows") if isinstance(live_vlans, dict) else None
    if not isinstance(rows, list):
        rows = []
    live_devices = {
        (parent, tag): device.split(" ", maxsplit=1)[0]
        for row in rows
        if isinstance(row, dict)
        for parent in [row.get("if")]
        for raw_tag in [row.get("tag")]
        for device in [row.get("vlanif")]
        if isinstance(parent, str)
        and isinstance(raw_tag, (str, int))
        and str(raw_tag).isdigit()
        and isinstance(device, str)
        and device
        for tag in [int(raw_tag)]
    }
    resolved: list[dict[str, object]] = []
    for desired in desired_interfaces:
        parent = desired.get("parent")
        tag = desired.get("tag")
        if not isinstance(parent, str) or not isinstance(tag, int):
            raise ValueError("desired VLAN assignment requires parent and integer tag")
        device = live_devices.get((parent, tag))
        if device is None:
            raise RuntimeError(f"VLAN device unavailable for {parent} tag {tag}")
        assignment = {
            key: value for key, value in desired.items() if key not in {"parent", "tag"}
        }
        assignment["if"] = device
        resolved.append(assignment)
    return resolved


@dataclass(frozen=True)
class BgpProof:
    missing_peers: set[str]
    missing_routes: set[str]
    ready: bool
    service_running: bool


BGP_NEIGHBOR_FIELDS = (
    "address",
    "description",
    "remoteas",
    "updatesource",
    "nexthopself",
    "enabled",
)


def reconcile_bgp_neighbors(
    client: Client,
    desired_neighbors: list[dict[str, object]],
) -> dict[str, int]:
    desired_by_address = _desired_bgp_neighbors(desired_neighbors)
    live_by_address = _live_bgp_neighbors(client.get("/api/quagga/bgp/search_neighbor"))
    unexpected = sorted(set(live_by_address) - set(desired_by_address))
    if unexpected:
        raise RuntimeError(
            "unexpected BGP neighbors require human-directed removal: "
            + ", ".join(unexpected)
        )
    changed = False
    for address, desired in desired_by_address.items():
        live = live_by_address.get(address)
        if live is None:
            _require_stored(
                client.post("/api/quagga/bgp/add_neighbor", {"neighbor": desired})
            )
            changed = True
        elif {key: live.get(key) for key in BGP_NEIGHBOR_FIELDS} != desired:
            _require_stored(
                client.post(
                    f"/api/quagga/bgp/set_neighbor/{live['uuid']}",
                    {"neighbor": desired},
                )
            )
            changed = True
    if changed:
        client.post("/api/quagga/service/reconfigure", {})
    verified = _live_bgp_neighbors(client.get("/api/quagga/bgp/search_neighbor"))
    mismatches = sorted(
        address
        for address, desired in desired_by_address.items()
        if {key: verified.get(address, {}).get(key) for key in BGP_NEIGHBOR_FIELDS}
        != desired
    )
    if mismatches:
        raise RuntimeError(
            "BGP neighbor verification failed for " + ", ".join(mismatches)
        )
    return {
        address: int(str(desired["remoteas"]))
        for address, desired in desired_by_address.items()
    }


def _desired_bgp_neighbors(
    desired_neighbors: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    desired_by_address: dict[str, dict[str, object]] = {}
    for desired in desired_neighbors:
        if not isinstance(desired, dict):
            raise ValueError("BGP neighbors must contain objects")
        neighbor = {key: desired.get(key) for key in BGP_NEIGHBOR_FIELDS}
        address, remoteas, enabled = (
            neighbor["address"],
            neighbor["remoteas"],
            neighbor["enabled"],
        )
        if not isinstance(address, str) or not address:
            raise ValueError("BGP neighbor requires an address")
        if address in desired_by_address:
            raise ValueError(f"duplicate BGP neighbor address: {address}")
        if (
            not isinstance(remoteas, str)
            or not remoteas.isdigit()
            or not all(
                isinstance(neighbor[key], str) and neighbor[key]
                for key in BGP_NEIGHBOR_FIELDS
            )
            or enabled not in ("0", "1")
        ):
            raise ValueError(f"BGP neighbor {address} has invalid managed fields")
        desired_by_address[address] = neighbor
    if not desired_by_address:
        raise ValueError("BGP neighbors must not be empty")
    return desired_by_address


def _live_bgp_neighbors(response: object) -> dict[str, dict[str, object]]:
    rows = response.get("rows") if isinstance(response, dict) else None
    if not isinstance(rows, list):
        raise ValueError("BGP neighbor search response contains no rows")
    live_by_address: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        address, uuid = row.get("address"), row.get("uuid")
        if (
            not isinstance(address, str)
            or not address
            or not isinstance(uuid, str)
            or not uuid
        ):
            continue
        entry: dict[str, object] = {key: row.get(key) for key in BGP_NEIGHBOR_FIELDS}
        entry["uuid"] = uuid
        live_by_address[address] = entry
    return live_by_address


def _require_stored(response: object) -> None:
    if (
        not isinstance(response, dict)
        or response.get("result") == "failed"
        or "validations" in response
    ):
        raise RuntimeError(f"BGP neighbor store failed: {response}")


def verify_bgp(
    client: Reader,
    expected_peers: dict[str, int],
    expected_routes: set[str],
) -> BgpProof:
    status = client.get("/api/quagga/service/status")
    summary = client.get("/api/quagga/diagnostics/bgpsummary")
    routes = client.get("/api/quagga/diagnostics/search_bgproute4")
    service_running = isinstance(status, dict) and status.get("status") == "running"
    peers = _peers(summary)
    missing_peers = {
        address
        for address, asn in expected_peers.items()
        if not _peer_established(peers.get(address), asn)
    }
    available_routes = _route_prefixes(routes)
    missing_routes = expected_routes - available_routes
    return BgpProof(
        missing_peers=missing_peers,
        missing_routes=missing_routes,
        ready=service_running and not missing_peers and not missing_routes,
        service_running=service_running,
    )


def _peers(summary: object) -> dict[str, object]:
    if not isinstance(summary, dict):
        return {}
    response = summary.get("response")
    if not isinstance(response, dict):
        return {}
    unicast = response.get("ipv4Unicast")
    if isinstance(unicast, dict) and isinstance(unicast.get("peers"), dict):
        return unicast["peers"]
    peers = response.get("peers")
    return peers if isinstance(peers, dict) else {}


def _peer_established(peer: object, expected_asn: int) -> bool:
    if not isinstance(peer, dict):
        return False
    return peer.get("state") == "Established" and peer.get("remoteAs") == expected_asn


def _route_prefixes(routes: object) -> set[str]:
    if not isinstance(routes, dict):
        return set()
    rows = routes.get("rows")
    if not isinstance(rows, list):
        return set()
    return {
        prefix if "/" in prefix else f"{prefix}/32"
        for row in rows
        if isinstance(row, dict)
        for prefix in [row.get("prefix")]
        if isinstance(prefix, str) and prefix
    }


def _assignment_devices(assignments: object) -> set[str]:
    if not isinstance(assignments, dict):
        return set()
    rows = assignments.get("rows")
    if not isinstance(rows, list):
        return set()
    return {
        device
        for row in rows
        if isinstance(row, dict)
        for device in [row.get("if")]
        if isinstance(device, str) and device
    }


def _runtime_interfaces(interfaces: object) -> dict[object, tuple[object, object]]:
    rows = interfaces.get("rows") if isinstance(interfaces, dict) else None
    if not isinstance(rows, list):
        return {}
    return {
        row.get("device"): (row.get("addr4"), row.get("description"))
        for row in rows
        if isinstance(row, dict) and row.get("device")
    }


def _kea_interfaces(configuration: object) -> set[str]:
    if not isinstance(configuration, dict):
        return set()
    dhcpv4 = configuration.get("dhcpv4")
    general = dhcpv4.get("general") if isinstance(dhcpv4, dict) else None
    interfaces = general.get("interfaces") if isinstance(general, dict) else None
    if not isinstance(interfaces, dict):
        return set()
    return {
        name
        for name, value in interfaces.items()
        if isinstance(name, str)
        and isinstance(value, dict)
        and value.get("selected") in (1, "1", True)
    }


OUTBOUND_NAT_FIELDS: tuple[str, ...] = (
    "interface",
    "ipprotocol",
    "protocol",
    "source_net",
    "source_not",
    "source_port",
    "destination_net",
    "destination_not",
    "destination_port",
    "target",
    "target_port",
    "poolopts",
    "staticnatport",
    "log",
    "nonat",
    "nosync",
    "endpoint_independent",
    "description",
    "enabled",
    "sequence",
)

OUTBOUND_NAT_VERIFY_FIELDS: tuple[str, ...] = (
    "interface",
    "ipprotocol",
    "protocol",
    "source_net",
    "destination_net",
    "target",
    "description",
    "enabled",
    "sequence",
)


def reconcile_outbound_nat(
    client: Client,
    desired_rules: list[dict[str, object]],
) -> None:
    desired_by_description = _desired_outbound_nat(desired_rules)
    live_by_description = _live_outbound_nat(
        client.get("/api/firewall/source_nat/search_rule")
    )
    unexpected = sorted(set(live_by_description) - set(desired_by_description))
    if unexpected:
        raise RuntimeError(
            "unexpected outbound NAT rules require human-directed removal: "
            + ", ".join(unexpected)
        )
    changed = False
    for description, desired in desired_by_description.items():
        live = live_by_description.get(description)
        if live is None:
            _require_stored(
                client.post("/api/firewall/source_nat/add_rule", {"rule": desired})
            )
            changed = True
        elif _normalize_outbound_nat(
            _outbound_nat_diff_fields(live)
        ) != _normalize_outbound_nat(_outbound_nat_diff_fields(desired)):
            _require_stored(
                client.post(
                    f"/api/firewall/source_nat/set_rule/{live['uuid']}",
                    {"rule": desired},
                )
            )
            changed = True
    if changed:
        client.post("/api/firewall/filter/apply", {})
    verified = _live_outbound_nat(client.get("/api/firewall/source_nat/search_rule"))
    mismatches = sorted(
        description
        for description, desired in desired_by_description.items()
        if _normalize_outbound_nat(
            _outbound_nat_verify_fields(verified.get(description, {}))
        )
        != _normalize_outbound_nat(_outbound_nat_verify_fields(desired))
    )
    if mismatches:
        raise RuntimeError(
            "outbound NAT verification failed for " + ", ".join(mismatches)
        )


def _normalize_outbound_nat(rule: dict[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key, value in rule.items():
        if key in ("enabled", "source_not", "destination_not", "nonat", "nosync"):
            normalized[key] = "1" if value in (1, "1", True, "true") else "0"
        elif key == "sequence":
            normalized[key] = (
                int(value) if isinstance(value, str) and value.isdigit() else value
            )
        else:
            normalized[key] = value
    return normalized


def _desired_outbound_nat(
    desired_rules: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    desired_by_description: dict[str, dict[str, object]] = {}
    for desired in desired_rules:
        if not isinstance(desired, dict):
            raise ValueError("outbound NAT rules must contain objects")
        rule = {key: desired.get(key) for key in OUTBOUND_NAT_FIELDS}
        description = rule["description"]
        if not isinstance(description, str) or not description:
            raise ValueError("outbound NAT rule requires a description")
        if description in desired_by_description:
            raise ValueError(f"duplicate outbound NAT description: {description}")
        if not isinstance(rule["interface"], str) or not rule["interface"]:
            raise ValueError(f"outbound NAT {description} requires interface")
        if rule["enabled"] not in ("0", "1"):
            raise ValueError(f"outbound NAT {description} enabled must be 0 or 1")
        if not isinstance(rule["sequence"], int):
            raise ValueError(f"outbound NAT {description} sequence must be an integer")
        if not isinstance(rule["target"], str) or not rule["target"]:
            raise ValueError(f"outbound NAT {description} target must be a string")
        if not isinstance(rule["source_net"], str) or not rule["source_net"]:
            raise ValueError(f"outbound NAT {description} source_net must be a string")
        desired_by_description[description] = rule
    return desired_by_description


def _live_outbound_nat(response: object) -> dict[str, dict[str, object]]:
    rows = response.get("rows") if isinstance(response, dict) else None
    if not isinstance(rows, list):
        raise ValueError("outbound NAT search response contains no rows")
    live_by_description: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        description = row.get("description")
        uuid = row.get("uuid")
        if (
            not isinstance(description, str)
            or not description
            or not isinstance(uuid, str)
            or not uuid
            or description.startswith("Auto created rule")
        ):
            continue
        entry: dict[str, object] = {key: row.get(key) for key in OUTBOUND_NAT_FIELDS}
        entry["uuid"] = uuid
        live_by_description[description] = entry
    return live_by_description


def _outbound_nat_live_fields(
    live: dict[str, object],
) -> dict[str, object]:
    return {key: live.get(key) for key in OUTBOUND_NAT_FIELDS}


def _outbound_nat_verify_fields(
    live: dict[str, object],
) -> dict[str, object]:
    return {key: live.get(key) for key in OUTBOUND_NAT_VERIFY_FIELDS}


def _outbound_nat_diff_fields(
    rule: dict[str, object],
) -> dict[str, object]:
    return {key: rule.get(key) for key in OUTBOUND_NAT_FIELDS}


UNBOUND_ACL_FIELDS: tuple[str, ...] = (
    "name",
    "enabled",
    "action",
    "networks",
    "description",
)


def reconcile_unbound_acls(
    client: Client,
    desired_acls: list[dict[str, object]],
) -> None:
    desired_by_name = _desired_unbound_acls(desired_acls)
    live_by_name = _live_unbound_acls(client.get("/api/unbound/settings/search_acl"))
    unexpected = sorted(set(live_by_name) - set(desired_by_name))
    if unexpected:
        raise RuntimeError(
            "unexpected Unbound ACLs require human-directed removal: "
            + ", ".join(unexpected)
        )
    changed = False
    for name, desired in desired_by_name.items():
        live = live_by_name.get(name)
        if live is None:
            _require_stored(
                client.post(
                    "/api/unbound/settings/add_acl",
                    {"acl": _serialize_unbound_acl(desired)},
                )
            )
            changed = True
        elif _normalize_unbound_acl(live) != _normalize_unbound_acl(desired):
            _require_stored(
                client.post(
                    f"/api/unbound/settings/set_acl/{live['uuid']}",
                    {"acl": _serialize_unbound_acl(desired)},
                )
            )
            changed = True
    if changed:
        client.post("/api/unbound/service/reconfigure", {})
    verified = _live_unbound_acls(client.get("/api/unbound/settings/search_acl"))
    mismatches = sorted(
        name
        for name, desired in desired_by_name.items()
        if _normalize_unbound_acl(verified.get(name, {}))
        != _normalize_unbound_acl(desired)
    )
    if mismatches:
        raise RuntimeError(
            "Unbound ACL verification failed for " + ", ".join(mismatches)
        )


def _serialize_unbound_acl(acl: dict[str, object]) -> dict[str, object]:
    networks = acl.get("networks")
    if isinstance(networks, list):
        return {**acl, "networks": ",".join(str(net) for net in networks)}
    return acl


def _normalize_unbound_acl(acl: dict[str, object]) -> dict[str, object]:
    raw = acl.get("networks")
    networks: list[object] = list(raw) if isinstance(raw, list) else []
    return {
        "name": acl.get("name"),
        "enabled": _normalize_unbound_enabled(acl.get("enabled")),
        "action": acl.get("action"),
        "networks": networks,
    }


def _desired_unbound_acls(
    desired_acls: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    desired_by_name: dict[str, dict[str, object]] = {}
    for desired in desired_acls:
        if not isinstance(desired, dict):
            raise ValueError("Unbound ACLs must contain objects")
        entry = {key: desired.get(key) for key in UNBOUND_ACL_FIELDS}
        name = entry["name"]
        if not isinstance(name, str) or not name:
            raise ValueError("Unbound ACL requires a name")
        if name in desired_by_name:
            raise ValueError(f"duplicate Unbound ACL name: {name}")
        if entry["action"] not in (
            "allow",
            "deny",
            "refuse",
            "allow_snoop",
            "deny_non_local",
            "refuse_non_local",
        ):
            raise ValueError(f"Unbound ACL {name} action must be a known verb")
        if entry["enabled"] not in ("0", "1"):
            raise ValueError(f"Unbound ACL {name} enabled must be 0 or 1")
        if not isinstance(entry["networks"], list) or not all(
            isinstance(net, str) and net for net in entry["networks"]
        ):
            raise ValueError(f"Unbound ACL {name} networks must be a list of CIDRs")
        desired_by_name[name] = entry
    return desired_by_name


def _live_unbound_acls(response: object) -> dict[str, dict[str, object]]:
    rows = response.get("rows") if isinstance(response, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Unbound ACL search response contains no rows")
    live_by_name: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        uuid = row.get("uuid")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(uuid, str)
            or not uuid
        ):
            continue
        entry: dict[str, object] = {
            "name": name,
            "enabled": _normalize_unbound_enabled(row.get("enabled")),
            "action": row.get("action"),
            "networks": _split_networks(row.get("networks")),
        }
        entry["uuid"] = uuid
        live_by_name[name] = entry
    return live_by_name


def _split_networks(value: object) -> list[str]:
    if isinstance(value, str):
        return [net.strip() for net in value.split(",") if net.strip()]
    if isinstance(value, list):
        return [str(net) for net in value if isinstance(net, str) and net]
    return []


def _normalize_unbound_enabled(value: object) -> str:
    return "1" if value in (1, "1", True, "true") else "0"


def _unbound_acl_live_fields(
    live: dict[str, object],
) -> dict[str, object]:
    return {
        "name": live.get("name"),
        "enabled": live.get("enabled"),
        "action": live.get("action"),
        "networks": live.get("networks", []),
    }


UNBOUND_HOST_OVERRIDE_FIELDS: tuple[str, ...] = (
    "hostname",
    "domain",
    "rr",
    "server",
    "description",
    "enabled",
)


def reconcile_unbound_host_overrides(
    client: Client,
    desired_overrides: list[dict[str, object]],
) -> None:
    desired_by_key = _desired_unbound_host_overrides(desired_overrides)
    live_by_key = _live_unbound_host_overrides(
        client.get("/api/unbound/settings/search_host_override")
    )
    unexpected = sorted(set(live_by_key) - set(desired_by_key))
    if unexpected:
        raise RuntimeError(
            "unexpected Unbound host overrides require human-directed removal: "
            + ", ".join(unexpected)
        )
    changed = False
    for key, desired in desired_by_key.items():
        live = live_by_key.get(key)
        if live is None:
            _require_stored(
                client.post(
                    "/api/unbound/settings/add_host_override",
                    {"host": _serialize_unbound_host_override(desired)},
                )
            )
            changed = True
        elif _normalize_unbound_host_override(live) != _normalize_unbound_host_override(
            desired
        ):
            _require_stored(
                client.post(
                    f"/api/unbound/settings/set_host_override/{live['uuid']}",
                    {"host": _serialize_unbound_host_override(desired)},
                )
            )
            changed = True
    if changed:
        client.post("/api/unbound/service/reconfigure", {})
    verified = _live_unbound_host_overrides(
        client.get("/api/unbound/settings/search_host_override")
    )
    mismatches = sorted(
        key
        for key, desired in desired_by_key.items()
        if _normalize_unbound_host_override(verified.get(key, {}))
        != _normalize_unbound_host_override(desired)
    )
    if mismatches:
        raise RuntimeError(
            "Unbound host override verification failed for " + ", ".join(mismatches)
        )


def _serialize_unbound_host_override(
    override: dict[str, object],
) -> dict[str, object]:
    return {key: override.get(key) for key in UNBOUND_HOST_OVERRIDE_FIELDS}


def _normalize_unbound_host_override(
    override: Mapping[str, object],
) -> dict[str, object]:
    return {
        "hostname": override.get("hostname"),
        "domain": override.get("domain"),
        "rr": str(override.get("rr")).upper(),
        "server": override.get("server"),
        "enabled": _normalize_unbound_enabled(override.get("enabled")),
    }


def _desired_unbound_host_overrides(
    desired_overrides: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    desired_by_key: dict[str, dict[str, object]] = {}
    for desired in desired_overrides:
        if not isinstance(desired, dict):
            raise ValueError("Unbound host overrides must contain objects")
        entry = {key: desired.get(key) for key in UNBOUND_HOST_OVERRIDE_FIELDS}
        hostname = entry["hostname"]
        domain = entry["domain"]
        if not isinstance(hostname, str) or not hostname or "." in hostname:
            raise ValueError("Unbound host override hostname must be a single label")
        if not isinstance(domain, str) or not domain:
            raise ValueError("Unbound host override requires a domain")
        if entry["rr"] not in ("A", "AAAA"):
            raise ValueError(
                f"Unbound host override {hostname}.{domain} rr must be A or AAAA"
            )
        server = entry["server"]
        if not isinstance(server, str) or not server:
            raise ValueError(
                f"Unbound host override {hostname}.{domain} requires a server address"
            )
        _require_override_address(entry["rr"], server, hostname, domain)
        if entry["enabled"] not in ("0", "1"):
            raise ValueError(
                f"Unbound host override {hostname}.{domain} enabled must be 0 or 1"
            )
        key = f"{hostname}.{domain}/{entry['rr']}"
        if key in desired_by_key:
            raise ValueError(f"duplicate Unbound host override: {key}")
        desired_by_key[key] = entry
    return desired_by_key


def _require_override_address(
    rr: object, server: str, hostname: str, domain: str
) -> None:
    try:
        address = ipaddress.ip_address(server)
    except ValueError:
        raise ValueError(
            f"Unbound host override {hostname}.{domain} server is not an IP address"
        ) from None
    if (rr == "A" and address.version != 4) or (rr == "AAAA" and address.version != 6):
        raise ValueError(
            f"Unbound host override {hostname}.{domain} {rr} record "
            f"requires an IPv{address.version} server address"
        )


def _live_unbound_host_overrides(response: object) -> dict[str, dict[str, object]]:
    rows = response.get("rows") if isinstance(response, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Unbound host override search response contains no rows")
    live_by_key: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        hostname = row.get("hostname")
        domain = row.get("domain")
        uuid = row.get("uuid")
        if (
            not isinstance(hostname, str)
            or not hostname
            or not isinstance(domain, str)
            or not domain
            or not isinstance(uuid, str)
            or not uuid
        ):
            continue
        rr = str(row.get("rr")).upper()
        entry: dict[str, object] = {
            "hostname": hostname,
            "domain": domain,
            "rr": rr,
            "server": row.get("server"),
            "enabled": _normalize_unbound_enabled(row.get("enabled")),
        }
        entry["uuid"] = uuid
        live_by_key[f"{hostname}.{domain}/{rr}"] = entry
    return live_by_key


def main(
    argv: list[str] | None = None,
    environ: Mapping[str, str] | None = None,
    client_factory: Callable[[str, Credentials, str, str | None], Client] = HttpsClient,
    reconcile: Callable[
        [Client, bool, list[dict[str, object]]], None
    ] = reconcile_interfaces,
    reconcile_kea: Callable[[Client, set[str]], None] = reconcile_kea_interfaces,
    ensure_relay_plugin: Callable[[Client], None] = ensure_udp_broadcast_relay_plugin,
    reconcile_relays: Callable[
        [Client, list[dict[str, object]]], None
    ] = reconcile_udp_broadcast_relays,
    reconcile_bgp: Callable[
        [Client, list[dict[str, object]]], dict[str, int]
    ] = reconcile_bgp_neighbors,
    reconcile_outbound: Callable[
        [Client, list[dict[str, object]]], None
    ] = reconcile_outbound_nat,
    reconcile_acls: Callable[
        [Client, list[dict[str, object]]], None
    ] = reconcile_unbound_acls,
    reconcile_host_overrides: Callable[
        [Client, list[dict[str, object]]], None
    ] = reconcile_unbound_host_overrides,
    prove_bgp: Callable[[Reader, dict[str, int], set[str]], BgpProof] = verify_bgp,
) -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile OPNsense interface assignments"
    )
    parser.add_argument("--assignments", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--kea-interfaces", required=True, type=Path)
    parser.add_argument("--udp-broadcast-relays", required=True, type=Path)
    parser.add_argument("--bgp-neighbors", required=True, type=Path)
    parser.add_argument("--bgp-expected-routes", required=True, type=Path)
    parser.add_argument("--outbound-nat-rules", required=True, type=Path)
    parser.add_argument("--unbound-acls", required=True, type=Path)
    parser.add_argument("--unbound-host-overrides", required=True, type=Path)
    arguments = parser.parse_args(argv)
    environment = environ if environ is not None else os.environ
    required = (
        "OPNSENSE_URL",
        "OPNSENSE_API_KEY",
        "OPNSENSE_API_SECRET",
        "OPNSENSE_CA_FILE",
    )
    missing = [name for name in required if not environment.get(name)]
    if missing:
        raise ValueError(
            f"missing required environment variables: {', '.join(missing)}"
        )
    inventory = json.loads(arguments.inventory.read_text())
    desired = json.loads(arguments.assignments.read_text())
    desired_kea_interfaces = json.loads(arguments.kea_interfaces.read_text())
    desired_udp_broadcast_relays = json.loads(
        arguments.udp_broadcast_relays.read_text()
    )
    desired_bgp_neighbors = json.loads(arguments.bgp_neighbors.read_text())
    expected_routes = json.loads(arguments.bgp_expected_routes.read_text())
    desired_outbound_nat = json.loads(arguments.outbound_nat_rules.read_text())
    desired_unbound_acls = json.loads(arguments.unbound_acls.read_text())
    desired_host_overrides = json.loads(arguments.unbound_host_overrides.read_text())
    if not isinstance(inventory, dict) or not isinstance(
        inventory.get("assignment_api_available"), bool
    ):
        raise ValueError("inventory must contain assignment_api_available")
    if not isinstance(desired, list) or not all(
        isinstance(item, dict) for item in desired
    ):
        raise ValueError("assignments must contain a list of objects")
    if not isinstance(desired_kea_interfaces, list) or not all(
        isinstance(item, str) and item for item in desired_kea_interfaces
    ):
        raise ValueError("Kea interfaces must contain a list of interface identifiers")
    if not isinstance(desired_udp_broadcast_relays, list) or not all(
        isinstance(item, dict) for item in desired_udp_broadcast_relays
    ):
        raise ValueError("UDP broadcast relays must contain a list of objects")
    if not isinstance(desired_bgp_neighbors, list) or not all(
        isinstance(item, dict) for item in desired_bgp_neighbors
    ):
        raise ValueError("BGP neighbors must contain a list of objects")
    if not isinstance(expected_routes, list) or not all(
        isinstance(item, str) and item for item in expected_routes
    ):
        raise ValueError("BGP expected routes must contain a list of prefixes")
    if not isinstance(desired_outbound_nat, list) or not all(
        isinstance(item, dict) for item in desired_outbound_nat
    ):
        raise ValueError("outbound NAT rules must contain a list of objects")
    if not isinstance(desired_unbound_acls, list) or not all(
        isinstance(item, dict) for item in desired_unbound_acls
    ):
        raise ValueError("Unbound ACLs must contain a list of objects")
    if not isinstance(desired_host_overrides, list) or not all(
        isinstance(item, dict) for item in desired_host_overrides
    ):
        raise ValueError("Unbound host overrides must contain a list of objects")
    client = client_factory(
        environment["OPNSENSE_URL"],
        Credentials(
            environment["OPNSENSE_API_KEY"], environment["OPNSENSE_API_SECRET"]
        ),
        environment["OPNSENSE_CA_FILE"],
        environment.get("OPNSENSE_TLS_SERVER_NAME"),
    )
    reconcile(client, inventory["assignment_api_available"], desired)
    reconcile_kea(client, set(desired_kea_interfaces))
    ensure_relay_plugin(client)
    reconcile_relays(client, desired_udp_broadcast_relays)
    expected_peers = reconcile_bgp(client, desired_bgp_neighbors)
    reconcile_outbound(client, desired_outbound_nat)
    reconcile_acls(client, desired_unbound_acls)
    reconcile_host_overrides(client, desired_host_overrides)
    proof = prove_bgp(client, expected_peers, set(expected_routes))
    if not proof.ready:
        raise RuntimeError(
            "BGP proof failed; missing peers: "
            + ", ".join(sorted(proof.missing_peers) or ["none"])
            + "; missing routes: "
            + ", ".join(sorted(proof.missing_routes) or ["none"])
        )
    print(
        json.dumps(
            {
                "assignment_count": len(desired),
                "bgp_peer_count": len(expected_peers),
                "kea_interface_count": len(desired_kea_interfaces),
                "udp_broadcast_relay_count": len(desired_udp_broadcast_relays),
                "outbound_nat_count": len(desired_outbound_nat),
                "unbound_acl_count": len(desired_unbound_acls),
                "unbound_host_override_count": len(desired_host_overrides),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
