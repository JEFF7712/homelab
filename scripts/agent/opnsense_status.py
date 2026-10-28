from __future__ import annotations

import argparse
import base64
import http.client
import ipaddress
import json
import os
import ssl
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

VARIABLES = ("OPNSENSE_READONLY_API_KEY", "OPNSENSE_READONLY_API_SECRET")
ENDPOINTS = (
    "/api/quagga/diagnostics/bgpsummary",
    "/api/quagga/diagnostics/search_bgproute4",
)


class Unavailable(RuntimeError):
    pass


def _operator_file(root: Path, filename: str) -> Path:
    local = root / "secrets/keys" / filename
    if local.is_file():
        return local
    result = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    if result.returncode == 0:
        shared = Path(result.stdout.strip()).parent / "secrets/keys" / filename
        if shared.is_file():
            return shared
    return local


def credentials(root: Path) -> tuple[str, str]:
    supplied = [os.environ.get(name, "") for name in VARIABLES]
    if any(name in os.environ for name in VARIABLES):
        if not all(supplied):
            raise Unavailable(
                "both read-only OPNsense credential variables are required"
            )
        return supplied[0], supplied[1]
    source = root / "gitops/secrets/homelab-values.sops.yaml"
    vm_identity = Path.home() / ".config/sops/age/agent-rupan.key"
    vm_source = root / "secrets/agent-rupan-opnsense-readonly.sops.json"
    use_vm_source = vm_identity.is_file()
    if use_vm_source:
        source = vm_source
    if not source.is_file():
        raise Unavailable("encrypted read-only OPNsense credentials are unavailable")
    environment = dict(os.environ)
    if "SOPS_AGE_KEY_FILE" not in environment:
        identity = (
            vm_identity if use_vm_source else _operator_file(root, "flux-age.key")
        )
        if identity.is_file():
            environment["SOPS_AGE_KEY_FILE"] = str(identity)
    values = []
    for name in VARIABLES:
        result = subprocess.run(
            [
                "sops",
                "--decrypt",
                "--extract",
                f'["{name}"]' if use_vm_source else f'["stringData"]["{name}"]',
                str(source),
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        value = result.stdout.strip()
        if result.returncode or not value:
            raise Unavailable("cannot decrypt the read-only OPNsense credentials")
        values.append(value)
    return values[0], values[1]


def diagnostic_health(
    summary: Any,
    routes: Any,
    expected_peers: dict[str, int],
    expected_routes: set[str],
) -> dict[str, Any]:
    if not expected_peers or not expected_routes:
        raise ValueError("BGP expectations must not be empty")
    response = summary.get("response") if isinstance(summary, dict) else None
    if not isinstance(response, dict):
        raise ValueError("invalid BGP summary")
    unicast = response.get("ipv4Unicast", response)
    peers = unicast.get("peers") if isinstance(unicast, dict) else None
    if not isinstance(peers, dict):
        raise ValueError("invalid BGP peers")
    rows = routes.get("rows") if isinstance(routes, dict) else None
    if not isinstance(rows, list) or routes.get("total") != len(rows):
        raise ValueError("incomplete BGP route table")
    prefixes = set()
    for row in rows:
        prefix = row.get("prefix") if isinstance(row, dict) else None
        if not isinstance(prefix, str):
            raise ValueError("invalid BGP route")
        prefixes.add(str(ipaddress.ip_network(prefix, strict=False)))
    missing_peers = []
    for address, asn in expected_peers.items():
        peer = peers.get(address)
        if (
            not isinstance(peer, dict)
            or peer.get("state") != "Established"
            or peer.get("remoteAs") != asn
        ):
            missing_peers.append(address)
    missing_routes = expected_routes - prefixes
    return {
        "status": "failed" if missing_peers or missing_routes else "pass",
        "detail": (
            f"{len(expected_peers) - len(missing_peers)}/{len(expected_peers)} "
            "expected BGP peers established; "
            f"{len(expected_routes) - len(missing_routes)}/{len(expected_routes)} "
            "expected IPv4 BGP routes present"
        ),
        "missing_peers": sorted(missing_peers),
        "missing_routes": sorted(missing_routes),
    }


def probe(root: Path, url: str) -> dict[str, Any]:
    target = urlsplit(url)
    if (
        target.scheme != "https"
        or not target.hostname
        or target.username is not None
        or target.password is not None
        or target.path not in ("", "/")
        or target.query
        or target.fragment
    ):
        raise ValueError("invalid OPNsense HTTPS target")
    ca_file = (
        Path(os.environ["OPNSENSE_CA_FILE"])
        if "OPNSENSE_CA_FILE" in os.environ
        else _operator_file(root, "opnsense-ca.pem")
    )
    if not ca_file.is_file():
        raise Unavailable("OPNsense TLS CA file is unavailable")
    context = ssl.create_default_context(cafile=str(ca_file))
    key, secret = credentials(root)
    authorization = base64.b64encode(f"{key}:{secret}".encode()).decode()
    payloads = []
    for endpoint in ENDPOINTS:
        connection = http.client.HTTPSConnection(
            target.hostname, target.port, context=context, timeout=5
        )
        try:
            connection.request(
                "GET",
                endpoint,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Basic {authorization}",
                },
            )
            response = connection.getresponse()
            if response.status != 200:
                return {
                    "status": "failed",
                    "detail": f"OPNsense returned HTTP {response.status}",
                }
            body = response.read(1_048_577)
            if len(body) > 1_048_576:
                raise ValueError("oversized OPNsense diagnostic response")
            payloads.append(json.loads(body))
        finally:
            connection.close()
    neighbors = json.loads(
        (root / "opnsense_reconciler/bgp-neighbors.json").read_text()
    )
    peers = {
        row["address"]: int(row["remoteas"])
        for row in neighbors
        if row.get("enabled") == "1"
    }
    routes = {
        str(ipaddress.ip_network(prefix))
        for prefix in json.loads(
            (root / "opnsense_reconciler/bgp-expected-routes.json").read_text()
        )
    }
    return diagnostic_health(payloads[0], payloads[1], peers, routes)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--url", required=True)
    arguments = parser.parse_args()
    try:
        result = probe(arguments.root, arguments.url)
    except Unavailable as error:
        result = {"status": "unavailable", "detail": str(error)}
    except FileNotFoundError:
        result = {
            "status": "unavailable",
            "detail": "a required OPNsense probe file or tool is missing",
        }
    except subprocess.TimeoutExpired:
        result = {"status": "failed", "detail": "OPNsense credential loading timed out"}
    except Exception:
        result = {
            "status": "failed",
            "detail": "OPNsense TLS, connection, or diagnostic validation failed",
        }
    print(json.dumps(result))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
