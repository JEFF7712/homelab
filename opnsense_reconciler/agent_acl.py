from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ACL = ROOT / "opnsense_reconciler/acl"
PRIVILEGE = "page-homelab-agent-bgp-readonly"
NAME = "Homelab: Read-only troubleshooting"
PATTERNS = {
    "api/core/service/search",
    "api/diagnostics/firewall/list_rule_ids",
    "api/diagnostics/firewall/log",
    "api/diagnostics/firewall/pf_states",
    "api/diagnostics/firewall/pf_statistics",
    "api/diagnostics/firewall/query_pf_top",
    "api/diagnostics/firewall/query_states",
    "api/diagnostics/firewall/stats",
    "api/diagnostics/interface/get_arp",
    "api/diagnostics/interface/get_interface_config",
    "api/diagnostics/interface/get_interface_names",
    "api/diagnostics/interface/get_interface_statistics",
    "api/diagnostics/interface/get_ndp",
    "api/diagnostics/interface/get_routes",
    "api/diagnostics/interface/get_vip_status",
    "api/diagnostics/interface/search_arp",
    "api/diagnostics/interface/search_ndp",
    "api/diagnostics/system/memory",
    "api/diagnostics/system/system_disk",
    "api/diagnostics/system/system_information",
    "api/diagnostics/system/system_resources",
    "api/dnsmasq/leases/search",
    "api/dnsmasq/service/status",
    "api/firewall/alias/get_table_size",
    "api/firewall/alias/search_item",
    "api/firewall/d_nat/search_rule",
    "api/firewall/filter/search_rule",
    "api/firewall/filter_util/rule_stats",
    "api/firewall/npt/search_rule",
    "api/firewall/one_to_one/search_rule",
    "api/firewall/source_nat/search_rule",
    "api/kea/leases4/search",
    "api/kea/leases6/search",
    "api/kea/service/status",
    "api/quagga/diagnostics/bgpsummary",
    "api/quagga/diagnostics/search_bgproute4",
    "api/quagga/service/status",
    "api/routes/gateway/status",
    "api/routes/routes/searchroute",
    "api/unbound/diagnostics/stats",
    "api/unbound/overview/is_enabled",
    "api/unbound/overview/search_queries",
    "api/unbound/service/status",
}
Request = Callable[[dict[str, Any]], dict[str, Any]]


def validate_source(content: bytes) -> str:
    if len(content) > 65536 or b"<!DOCTYPE" in content or b"<!ENTITY" in content:
        raise ValueError("ACL source is oversized or declares XML entities")
    root = ET.fromstring(content)
    if root.tag != "acl" or root.attrib or len(root) != 1:
        raise ValueError("ACL source must define exactly one privilege")
    privilege = root[0]
    patterns = privilege.findall("patterns/pattern")
    pattern_node = privilege.find("patterns")
    if (
        privilege.tag != PRIVILEGE
        or privilege.attrib
        or [item.tag for item in privilege] != ["name", "patterns"]
        or privilege.findtext("name") != NAME
        or len(patterns) != len(PATTERNS)
        or {pattern.text for pattern in patterns} != PATTERNS
        or any(element.attrib for element in root.iter())
        or any(len(pattern) for pattern in patterns)
        or pattern_node is None
        or len(pattern_node) != len(PATTERNS)
    ):
        raise ValueError(
            "ACL source must grant only the reviewed troubleshooting paths"
        )
    return hashlib.sha256(content).hexdigest()


class SshRouter:
    def __init__(self, key_file: Path | None, known_hosts: Path = ACL / "known_hosts"):
        self.key_file = key_file
        self.known_hosts = known_hosts

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.known_hosts.is_file():
            raise ValueError("Pinned OPNsense known_hosts file is missing")
        argv = [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={self.known_hosts}",
            "-o",
            "ConnectTimeout=5",
        ]
        if self.key_file is not None:
            if not self.key_file.is_file() or self.key_file.stat().st_mode & 0o077:
                raise ValueError("OPNsense ACL deployment key must be a private file")
            argv.extend(["-o", "IdentitiesOnly=yes", "-i", str(self.key_file)])
        argv.extend(["root@192.168.1.1", "php"])
        template = (ACL / "reconcile.php").read_text()
        if template.count("__REQUEST_BASE64__") != 1:
            raise ValueError("Invalid ACL reconciliation worker")
        request = base64.b64encode(json.dumps(payload).encode()).decode()
        result = subprocess.run(
            argv,
            input=template.replace("__REQUEST_BASE64__", request),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        try:
            response = json.loads(result.stdout)
        except ValueError:
            raise RuntimeError(
                "OPNsense ACL SSH worker failed; no remote output disclosed"
            ) from None
        if not isinstance(response, dict):
            raise RuntimeError("Invalid ACL worker response")
        if result.returncode:
            raise RuntimeError(
                "OPNsense ACL reconciliation failed: "
                + str(response.get("error", "invalid response"))
            )
        return response


def make_plan(content: bytes, request: Request) -> dict[str, Any]:
    digest = validate_source(content)
    observed = request(
        {"action": "inspect", "content": base64.b64encode(content).decode()}
    )
    if not isinstance(observed.get("fingerprint"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", observed["fingerprint"]
    ):
        raise ValueError("Router returned an invalid state fingerprint")
    if not isinstance(observed.get("drift"), list) or not isinstance(
        observed.get("state"), dict
    ):
        raise ValueError("Router returned an invalid ACL observation")
    return {"schema_version": 1, "desired_sha256": digest, "before": observed}


def apply_plan(
    content: bytes, plan: dict[str, Any], request: Request
) -> dict[str, Any]:
    digest = validate_source(content)
    if plan.get("schema_version") != 1 or plan.get("desired_sha256") != digest:
        raise ValueError("ACL plan does not match the current source")
    before = plan.get("before")
    if (
        not isinstance(before, dict)
        or not isinstance(before.get("fingerprint"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", before["fingerprint"])
    ):
        raise ValueError("ACL plan has no valid prior state fingerprint")
    result = request(
        {
            "action": "apply",
            "content": base64.b64encode(content).decode(),
            "expected_fingerprint": before["fingerprint"],
        }
    )
    if result.get("status") != "pass" or result.get("drift") != []:
        raise RuntimeError("ACL apply did not verify convergence")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("check", "plan", "apply"))
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--ssh-key-file", type=Path, default=os.environ.get("OPNSENSE_ACL_SSH_KEY")
    )
    args = parser.parse_args()
    try:
        content = (ACL / "ACL.xml").read_bytes()
        request = SshRouter(args.ssh_key_file)
        if args.action == "apply":
            if args.plan is None or args.ssh_key_file is None:
                raise ValueError(
                    "ACL apply requires a saved plan and explicit deployment key file"
                )
            result = apply_plan(content, json.loads(args.plan.read_text()), request)
        else:
            plan = make_plan(content, request)
            result = (
                plan
                if args.action == "plan"
                else {
                    "status": "drift" if plan["before"]["drift"] else "pass",
                    "desired_sha256": plan["desired_sha256"],
                    **plan["before"],
                }
            )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as output:
            os.fchmod(output.fileno(), 0o600)
            json.dump(result, output, indent=2)
            output.write("\n")
        drift = result.get("drift", result.get("before", {}).get("drift", []))
        print(
            json.dumps(
                {
                    "action": args.action,
                    "status": result.get("status", "planned"),
                    "drift": drift,
                    "output": str(args.output),
                }
            )
        )
        return 2 if args.action == "check" and drift else 0
    except (
        OSError,
        ValueError,
        RuntimeError,
        ET.ParseError,
        subprocess.TimeoutExpired,
    ) as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
