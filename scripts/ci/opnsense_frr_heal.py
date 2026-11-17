"""Detect and repair an FRR FIB dropout on OPNsense, with bounded authority.

Symptom (docs/runbooks/opnsense-bgp-proof.md): every BGP peer is established
and FRR holds the expected VIP /32 routes, but the kernel FIB lacks some of
them, so those VIPs blackhole for every LAN client. Restarting FRR
reinstalls them; it was an operator UI step until this module.

`check` (read-only, scheduled) fails when that exact condition holds.
`heal` (a deployment) repairs it under rules decided purely from probes:

- act only on a pure FIB dropout; peer or RIB problems are left alone;
- confirm the dropout again after CONFIRM_SECONDS, so reconvergence is not
  mistaken for it;
- restart only when every peer session is older than MIN_PEER_UPTIME. Peer
  sessions reset whenever FRR restarts, so this is a stateless limit of one
  restart per six hours, whoever restarted it;
- after the restart, require every expected route installed within
  VERIFY_SECONDS, otherwise fail so a human or the controller takes over.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opnsense_reconciler.inventory import Credentials, HttpsClient
from scripts.agent.opnsense_status import diagnostic_health, expectations

SUMMARY = "/api/quagga/diagnostics/bgpsummary"
ROUTES = "/api/quagga/diagnostics/search_bgproute4"
KERNEL = "/api/diagnostics/interface/get_routes"
RESTART = "/api/quagga/service/restart"
MIN_PEER_UPTIME = 6 * 3600
CONFIRM_SECONDS = 60
VERIFY_SECONDS = 180
VERIFY_INTERVAL = 15


@dataclass(frozen=True)
class Decision:
    action: str  # healthy | ignore | restart | hold
    reason: str


def peer_uptimes(summary: Any) -> dict[str, float]:
    response = summary.get("response") if isinstance(summary, dict) else None
    unicast = (
        response.get("ipv4Unicast", response) if isinstance(response, dict) else None
    )
    peers = unicast.get("peers") if isinstance(unicast, dict) else None
    if not isinstance(peers, dict):
        raise ValueError("invalid BGP peers")
    return {
        address: float(peer.get("peerUptimeMsec", 0)) / 1000
        for address, peer in peers.items()
        if isinstance(peer, dict)
    }


def decide(health: dict[str, Any], uptimes: dict[str, float]) -> Decision:
    """What the probes allow, without side effects."""
    if health["status"] == "pass":
        return Decision("healthy", health["detail"])
    if health["missing_peers"] or health["missing_routes"]:
        return Decision(
            "ignore",
            "not a FIB dropout (peers or FRR routes are missing); "
            "left to opnsense-dataplane: " + health["detail"],
        )
    youngest = min(uptimes.values(), default=0.0)
    if youngest < MIN_PEER_UPTIME:
        return Decision(
            "hold",
            f"FIB dropout of {', '.join(health['uninstalled_routes'])}, but FRR "
            f"restarted {int(youngest // 60)} minutes ago; a second restart "
            "within six hours needs a human",
        )
    return Decision(
        "restart",
        "FIB dropout of " + ", ".join(health["uninstalled_routes"]),
    )


class Router:
    def __init__(self, client: Any, root: Path) -> None:
        self.client = client
        self.peers, self.routes = expectations(root)

    def probe(self) -> tuple[dict[str, Any], dict[str, float]]:
        summary = self.client.get(SUMMARY)
        health = diagnostic_health(
            summary,
            self.client.get(ROUTES),
            self.client.get(KERNEL),
            self.peers,
            self.routes,
        )
        return health, peer_uptimes(summary)

    def restart(self) -> None:
        result = self.client.post(RESTART, {})
        if not isinstance(result, dict) or result.get("response", "OK").strip() not in (
            "OK",
            "",
        ):
            raise RuntimeError(f"FRR restart was refused: {result}")


def check(router: Router) -> int:
    health, uptimes = router.probe()
    decision = decide(health, uptimes)
    print(json.dumps({"action": decision.action, "reason": decision.reason}))
    if decision.action in ("restart", "hold"):
        print(
            "FIB dropout: run `just ship --resume <main> --deploy opnsense-frr-heal`",
            file=sys.stderr,
        )
        return 1
    return 0


def heal(router: Router, sleep: Callable[[float], None] = time.sleep) -> int:
    health, uptimes = router.probe()
    decision = decide(health, uptimes)
    if decision.action == "restart":
        sleep(CONFIRM_SECONDS)
        health, uptimes = router.probe()
        decision = decide(health, uptimes)
    print(json.dumps({"action": decision.action, "reason": decision.reason}))
    if decision.action in ("healthy", "ignore"):
        return 0
    if decision.action == "hold":
        return 1
    router.restart()
    print("restarted FRR; waiting for the kernel FIB")
    waited = 0
    while waited < VERIFY_SECONDS:
        sleep(VERIFY_INTERVAL)
        waited += VERIFY_INTERVAL
        try:
            health, _ = router.probe()
        except (OSError, ValueError) as error:
            print(f"probe after restart failed: {error}")
            continue
        if health["status"] == "pass":
            print(json.dumps({"action": "healed", "reason": health["detail"]}))
            return 0
    print(json.dumps({"action": "failed", "reason": health["detail"]}))
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["check", "heal"])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    arguments = parser.parse_args(argv)
    missing = [
        name
        for name in (
            "OPNSENSE_URL",
            "OPNSENSE_API_KEY",
            "OPNSENSE_API_SECRET",
            "OPNSENSE_CA_FILE",
        )
        if not os.environ.get(name)
    ]
    if missing:
        raise SystemExit(f"missing environment variables: {', '.join(missing)}")
    client = HttpsClient(
        os.environ["OPNSENSE_URL"],
        Credentials(os.environ["OPNSENSE_API_KEY"], os.environ["OPNSENSE_API_SECRET"]),
        os.environ["OPNSENSE_CA_FILE"],
        os.environ.get("OPNSENSE_TLS_SERVER_NAME"),
    )
    router = Router(client, arguments.root)
    return check(router) if arguments.mode == "check" else heal(router)


if __name__ == "__main__":
    raise SystemExit(main())
