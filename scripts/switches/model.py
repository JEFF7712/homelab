"""Inventory and saved-plan checks, independent of live I/O."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

TARGETS = {
    "upstairs": ("10.0.10.2", "bc:07:1d:2e:79:82", "TL-SG108E", 8),
    "downstairs": ("10.0.10.3", "98:03:8e:34:e0:0b", "TL-SG105E", 5),
}
SPEEDS = {
    "auto",
    "10Mbps-half",
    "10Mbps-full",
    "100Mbps-half",
    "100Mbps-full",
    "1000Mbps-full",
}
PORT_TYPE = "tplinkeasysmart_port"
VLAN_TYPE = "tplinkeasysmart_vlan_8021q_group"
PVID_TYPE = "tplinkeasysmart_vlan_8021q_pvid"
MODE_TYPE = "tplinkeasysmart_vlan_mode"


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def validate_inventory(document: dict[str, Any]) -> None:
    if not isinstance(document, dict) or not isinstance(document.get("switches"), dict):
        raise ValueError("Expected a switch inventory object")
    if (
        set(document) != {"schema_version", "switches"}
        or document["schema_version"] != 1
    ):
        raise ValueError("Unrecognized switch inventory schema")
    if set(document["switches"]) != set(TARGETS):
        raise ValueError("Inventory must contain exactly the two adopted switches")
    for name, (host, mac, model, count) in TARGETS.items():
        switch = document["switches"][name]
        if not isinstance(switch, dict) or set(switch) != {
            "host",
            "mac",
            "model",
            "hardware",
            "firmware",
            "ports",
            "vlans",
        }:
            raise ValueError(f"Unexpected inventory fields for {name}")
        if (switch["host"], switch["mac"], switch["model"]) != (host, mac, model):
            raise ValueError(f"Identity differs from the adopted target: {name}")
        if (
            switch["hardware"]
            != {"upstairs": "TL-SG108E 6.0", "downstairs": "TL-SG105E 5.0"}[name]
            or switch["firmware"]
            != {
                "upstairs": "1.0.0 Build 20250710 Rel.71093",
                "downstairs": "1.0.0 Build 20250710 Rel.71066",
            }[name]
        ):
            raise ValueError(f"Unverified switch hardware or firmware: {name}")
        if not isinstance(switch["ports"], dict) or set(switch["ports"]) != {
            str(port) for port in range(1, count + 1)
        }:
            raise ValueError(f"Physical port inventory differs for {name}")
        if (
            not isinstance(switch["vlans"], dict)
            or not 1 <= len(switch["vlans"]) <= 32
            or "1" not in switch["vlans"]
        ):
            raise ValueError(
                "VLAN 1 must remain present and the table must fit the hardware"
            )
        memberships: dict[int, set[int]] = {port: set() for port in range(1, count + 1)}
        for key, vlan in switch["vlans"].items():
            if (
                not isinstance(key, str)
                or not key.isdecimal()
                or str(int(key)) != key
                or not 1 <= int(key) <= 4094
            ):
                raise ValueError("Invalid VLAN ID")
            if (
                not isinstance(vlan, dict)
                or set(vlan) != {"name", "tagged_ports", "untagged_ports"}
                or not isinstance(vlan["name"], str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{0,10}", vlan["name"])
            ):
                raise ValueError("Invalid VLAN attributes or name")
            for members in [vlan["tagged_ports"], vlan["untagged_ports"]]:
                if not isinstance(members, list):
                    raise ValueError("VLAN port lists must contain unique port numbers")
                if any(
                    type(port) is not int or not 1 <= port <= count for port in members
                ):
                    raise ValueError("VLAN refers to a port outside the hardware")
                if len(set(members)) != len(members):
                    raise ValueError("VLAN port lists must contain unique port numbers")
                for port in members:
                    memberships[port].add(int(key))
            if set(vlan["tagged_ports"]) & set(vlan["untagged_ports"]):
                raise ValueError(
                    "A port cannot be both tagged and untagged in one VLAN"
                )
        for key, port in switch["ports"].items():
            if not isinstance(port, dict) or set(port) != {
                "enabled",
                "speed",
                "flow_control",
                "pvid",
            }:
                raise ValueError("Unexpected port settings")
            if (
                type(port["enabled"]) is not bool
                or type(port["flow_control"]) is not bool
                or not isinstance(port["speed"], str)
                or port["speed"] not in SPEEDS
            ):
                raise ValueError("Invalid port settings")
            if port["flow_control"] and port["speed"] not in {"auto", "1000Mbps-full"}:
                raise ValueError("Flow control requires auto or gigabit speed")
            if (
                type(port["pvid"]) is not int
                or port["pvid"] not in memberships[int(key)]
            ):
                raise ValueError("Every PVID must name a VLAN containing that port")


def addresses(inventory: dict[str, Any], name: str) -> set[str]:
    switch = inventory["switches"][name]
    return (
        {MODE_TYPE + ".switch"}
        | {f'{PORT_TYPE}.ports["{port}"]' for port in switch["ports"]}
        | {f'{PVID_TYPE}.pvids["{port}"]' for port in switch["ports"]}
        | {f'{VLAN_TYPE}.vlans["{vlan}"]' for vlan in switch["vlans"]}
    )


def review_plan(
    plan: dict[str, Any],
    inventory: dict[str, Any],
    name: str,
    *,
    adoption: bool = False,
) -> dict[str, Any]:
    validate_inventory(inventory)
    if name not in TARGETS:
        raise ValueError("Unrecognized switch")
    if (
        not str(plan.get("format_version", "")).startswith("1.")
        or plan.get("errored")
        or plan.get("complete") is False
        or plan.get("deferred_changes")
    ):
        raise ValueError("Unrecognized or incomplete OpenTofu plan")
    expected = addresses(inventory, name)
    resources = (
        plan.get("planned_values", {}).get("root_module", {}).get("resources", [])
    )
    if {
        item["address"] for item in resources if item.get("mode") == "managed"
    } != expected:
        raise ValueError("Plan resource ownership differs from the switch inventory")
    changes = plan.get("resource_changes")
    if not isinstance(changes, list):
        raise ValueError("Plan lacks resource changes")
    managed = [item["address"] for item in changes if item.get("mode") == "managed"]
    if len(managed) != len(expected) or set(managed) != expected:
        raise ValueError("Plan changes do not cover exactly the owned resources")
    reviewed, imports = [], 0
    for item in changes:
        actions = item["change"]["actions"]
        if item.get("mode") == "data" and actions in [["read"], ["no-op"]]:
            continue
        if item.get("mode") != "managed" or item["address"] not in expected:
            raise ValueError("Plan contains an unmanaged target")
        if actions not in [["no-op"], ["update"], ["create"]]:
            raise ValueError("Deleting or replacing adopted resources is prohibited")
        if adoption and actions != ["no-op"]:
            raise ValueError(
                "Adoption may import state but cannot change switch settings"
            )
        if item["type"] == MODE_TYPE and actions != ["no-op"]:
            raise ValueError(
                "VLAN mode changes require a separate maintenance procedure"
            )
        if actions == ["create"] and item["type"] != VLAN_TYPE:
            raise ValueError("Existing ports and PVIDs must be imported, not created")
        if item["change"].get("after", {}).get("allow_disruptive"):
            raise ValueError("Disruptive changes are not enabled by this workflow")
        if item["change"].get("importing"):
            imports += 1
        if actions != ["no-op"]:
            reviewed.append({"address": item["address"], "actions": actions})
    if adoption and imports != len(expected):
        raise ValueError("Initial adoption must import every existing resource")
    return {
        "switch": name,
        "resources": len(expected),
        "imports": imports,
        "changes": reviewed,
        "zero_change": not reviewed and imports == 0,
    }


def encryption_configuration(passphrase: str) -> str:
    if not isinstance(passphrase, str) or len(passphrase) < 32:
        raise ValueError("A separate strong state key is required for each switch")
    return (
        'key_provider "pbkdf2" "switch" {\n  passphrase = '
        + json.dumps(passphrase)
        + "\n}\n"
        'method "aes_gcm" "switch" {\n  keys = key_provider.pbkdf2.switch\n}\n'
        "state {\n  method = method.aes_gcm.switch\n  enforced = true\n}\n"
        "plan {\n  method = method.aes_gcm.switch\n  enforced = true\n}\n"
    )
