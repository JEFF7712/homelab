from __future__ import annotations

import unittest
from pathlib import Path

from scripts.agent.opnsense_status import expectations
from scripts.ci.opnsense_frr_heal import (
    KERNEL,
    MIN_PEER_UPTIME,
    RESTART,
    ROUTES,
    SUMMARY,
    Router,
    check,
    heal,
)

ROOT = Path(__file__).resolve().parents[1]
PEERS, EXPECTED = expectations(ROOT)


class FakeOpnsense:
    """OPNsense diagnostics whose FIB and peer uptimes the test controls."""

    def __init__(self, installed: set[str], uptime: float = MIN_PEER_UPTIME + 60):
        self.installed = set(installed)
        self.uptime = uptime
        self.peer_down = False
        self.restarts = 0
        self.heals_on_restart = True
        self.after_probes: list[set[str]] = []

    def get(self, path: str) -> object:
        if path == SUMMARY:
            return {
                "response": {
                    "ipv4Unicast": {
                        "peers": {
                            address: {
                                "state": "Idle"
                                if self.peer_down and index == 0
                                else "Established",
                                "remoteAs": asn,
                                "peerUptimeMsec": int(self.uptime * 1000),
                            }
                            for index, (address, asn) in enumerate(PEERS.items())
                        }
                    }
                }
            }
        if path == ROUTES:
            rows = [{"prefix": prefix} for prefix in sorted(EXPECTED)]
            return {"rows": rows, "total": len(rows)}
        if path == KERNEL:
            if self.after_probes:
                self.installed = self.after_probes.pop(0)
            gateway = next(iter(PEERS))
            return [
                {"proto": "ipv4", "destination": prefix, "gateway": gateway}
                for prefix in sorted(self.installed)
            ]
        raise AssertionError(path)

    def post(self, path: str, payload: object) -> object:
        assert path == RESTART, path
        self.restarts += 1
        self.uptime = 1
        if self.heals_on_restart:
            self.installed = set(EXPECTED)
        return {"response": "OK\n\n"}


def router(fake: FakeOpnsense) -> Router:
    return Router(fake, ROOT)


DROPPED = set(sorted(EXPECTED)[1:])


class FrrHealTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertGreater(len(EXPECTED), 1, "fixture needs two or more VIPs")

    def test_healthy_router_is_left_alone(self) -> None:
        fake = FakeOpnsense(set(EXPECTED))
        self.assertEqual(check(router(fake)), 0)
        self.assertEqual(heal(router(fake), sleep=lambda _: None), 0)
        self.assertEqual(fake.restarts, 0)

    def test_check_flags_a_fib_dropout_without_acting(self) -> None:
        fake = FakeOpnsense(DROPPED)
        self.assertEqual(check(router(fake)), 1)
        self.assertEqual(fake.restarts, 0)

    def test_heal_restarts_once_and_verifies_the_fib(self) -> None:
        fake = FakeOpnsense(DROPPED)
        waits: list[float] = []
        self.assertEqual(heal(router(fake), sleep=waits.append), 0)
        self.assertEqual(fake.restarts, 1)
        self.assertEqual(waits[0], 60, "the dropout is confirmed before acting")

    def test_a_transient_dropout_clears_on_confirmation(self) -> None:
        fake = FakeOpnsense(DROPPED)
        fake.after_probes = [set(DROPPED), set(EXPECTED)]
        self.assertEqual(heal(router(fake), sleep=lambda _: None), 0)
        self.assertEqual(fake.restarts, 0)

    def test_peer_problems_are_not_this_remediation(self) -> None:
        fake = FakeOpnsense(DROPPED)
        fake.peer_down = True
        self.assertEqual(check(router(fake)), 0)
        self.assertEqual(heal(router(fake), sleep=lambda _: None), 0)
        self.assertEqual(fake.restarts, 0)

    def test_a_recent_restart_holds_for_a_human(self) -> None:
        fake = FakeOpnsense(DROPPED, uptime=600)
        self.assertEqual(heal(router(fake), sleep=lambda _: None), 1)
        self.assertEqual(fake.restarts, 0)

    def test_a_restart_that_does_not_heal_fails_loudly(self) -> None:
        fake = FakeOpnsense(DROPPED)
        fake.heals_on_restart = False
        self.assertEqual(heal(router(fake), sleep=lambda _: None), 1)
        self.assertEqual(fake.restarts, 1, "never more than one restart per run")


if __name__ == "__main__":
    unittest.main()
