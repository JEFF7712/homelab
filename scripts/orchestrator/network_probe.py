"""Gate 4 network probe: `python -m scripts.orchestrator.network_probe`.

Run inside a pod that has a worker's identity (the worker label, service
account and DNS settings) in the persistent-agent namespace. It checks what
a compromised worker could reach and prints one JSON report; the exit status
is nonzero when any expectation fails.

Expected, from the rendered policies:
- DNS answers the provider hosts and the controller's FQDN and refuses other
  names, including the forge, the registry and names CoreDNS would forward;
- TLS to each provider host completes with its own server name, and the same
  address with another server name does not;
- private and cluster addresses (the forge, the API server, control-plane
  nodes, a public address that is not a provider) are unreachable;
- the worker API refuses a token that belongs to no live attempt.
"""

from __future__ import annotations

import json
import socket
import ssl
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from scripts.orchestrator.manifests import (
    CONTROLLER_FQDN,
    PROVIDER_HOSTS,
    WORKER_API_PORT,
)

TIMEOUT = 5.0
REFUSED_NAMES = (
    "example.com",
    "git.rupan.dev",
    "registry.rupan.dev",
    "exfiltrate.svc",
    "kubernetes.default.svc.cluster.local",
)
BLOCKED_ADDRESSES = (
    ("10.0.30.20", 443),  # forge and registry
    ("10.43.0.1", 443),  # Kubernetes API service
    ("10.0.30.11", 6443),  # a control-plane node
    ("1.1.1.1", 443),  # public, but not a provider
)


def resolve(name: str) -> list[str]:
    return sorted(
        {
            str(info[4][0])
            for info in socket.getaddrinfo(name, 443, type=socket.SOCK_STREAM)
        }
    )


def handshake(address: str, server_name: str) -> str:
    context = ssl.create_default_context()
    with socket.create_connection((address, 443), timeout=TIMEOUT) as raw:
        with context.wrap_socket(raw, server_hostname=server_name) as tls:
            return str(tls.version())


def connect(address: str, port: int) -> None:
    with socket.create_connection((address, port), timeout=TIMEOUT):
        pass


def worker_api_status() -> int:
    request = urllib.request.Request(
        f"http://{CONTROLLER_FQDN}:{WORKER_API_PORT}/v1/attempt/heartbeat",
        data=b"{}",
        method="POST",
        headers={
            "Authorization": "Bearer not-a-live-attempt",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def check(
    results: list[dict[str, Any]], name: str, expect: str, action: Callable[[], Any]
) -> Any:
    """Run one check; `expect` is "ok" or "fail"."""
    try:
        value = action()
        outcome = "ok"
    except Exception as error:  # every failure mode is an observation here
        value = f"{type(error).__name__}: {error}"[:200]
        outcome = "fail"
    results.append(
        {
            "check": name,
            "expect": expect,
            "outcome": outcome,
            "detail": value,
            "passed": outcome == expect,
        }
    )
    return value if outcome == "ok" else None


def run() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    addresses: dict[str, list[str]] = {}
    for host in (*PROVIDER_HOSTS, CONTROLLER_FQDN):
        found = check(results, f"dns {host}", "ok", lambda host=host: resolve(host))
        if found:
            addresses[host] = found
    for name in REFUSED_NAMES:
        check(results, f"dns {name}", "fail", lambda name=name: resolve(name))
    for host in PROVIDER_HOSTS:
        for address in [a for a in addresses.get(host, []) if ":" not in a][:1]:
            check(
                results,
                f"tls {host} via {address}",
                "ok",
                lambda a=address, h=host: handshake(a, h),
            )
            check(
                results,
                f"tls example.com via {host} {address}",
                "fail",
                lambda a=address: handshake(a, "example.com"),
            )
    for address, port in BLOCKED_ADDRESSES:
        check(
            results,
            f"tcp {address}:{port}",
            "fail",
            lambda a=address, p=port: connect(a, p),
        )
    status = check(results, "worker api with a foreign token", "ok", worker_api_status)
    results.append(
        {
            "check": "worker api refuses the foreign token",
            "expect": "ok",
            "outcome": "ok",
            "detail": status,
            "passed": status == 403,
        }
    )
    return results


def main() -> int:
    results = run()
    failed = [item for item in results if not item["passed"]]
    print(
        json.dumps(
            {"passed": not failed, "failed": len(failed), "results": results}, indent=2
        )
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
