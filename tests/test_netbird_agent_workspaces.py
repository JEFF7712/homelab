from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from scripts.netbird_agent_workspaces import ReconcileError, build_plan

MANIFEST = json.loads(
    (
        Path(__file__).resolve().parents[1] / "config/netbird/agent-workspaces.json"
    ).read_text()
)


def api_state() -> tuple[list[dict[str, Any]], ...]:
    networks = [{"id": "network-id", "name": "agent-workspaces"}]
    peers = [
        {"id": "router-id", "name": "adguard-netbird-01"},
        {"id": "rupan-peer-id", "name": "laptop-nixos"},
        {"id": "sam-peer-id", "name": "HPSPECTRE - Sam"},
    ]
    routers = [
        {
            "peer": "router-id",
            "enabled": True,
            "masquerade": True,
            "metric": 9999,
        }
    ]
    resources = [
        {
            "id": "sam-resource-id",
            "name": "sam-workspace",
            "address": "172.31.10.6/32",
            "enabled": True,
            "groups": None,
        }
    ]
    policies = [
        {
            "id": "sam-policy-id",
            "name": "sam-to-workspace",
            "enabled": True,
            "rules": [
                {
                    "name": "sam-to-workspace",
                    "enabled": True,
                    "action": "accept",
                    "bidirectional": False,
                    "protocol": "tcp",
                    "ports": ["22", "443"],
                    "sourceResource": {"id": "sam-peer-id", "type": "peer"},
                    "destinationResource": {"id": "sam-resource-id", "type": "host"},
                }
            ],
        }
    ]
    return networks, peers, routers, resources, policies


class NetBirdPlanTests(unittest.TestCase):
    def test_plan_adds_only_rupan_resource_and_policy(self) -> None:
        plan = build_plan(MANIFEST, *api_state())

        self.assertEqual(
            [action[:2] for action in plan],
            [
                ("create-resource", "rupan-workspace"),
                ("create-policy", "rupan-to-workspace"),
            ],
        )
        self.assertEqual(plan[0][2]["address"], "172.31.10.2/32")
        rule = plan[1][2]["rules"][0]
        self.assertEqual(
            rule["sourceResource"], {"id": "rupan-peer-id", "type": "peer"}
        )
        self.assertEqual(
            rule["destinationResource"],
            {"id": "planned:rupan-workspace", "type": "host"},
        )

    def test_plan_rejects_a_router_peer_drift(self) -> None:
        networks, peers, routers, resources, policies = api_state()
        routers[0]["peer"] = "unexpected-peer"

        with self.assertRaisesRegex(ReconcileError, "router"):
            build_plan(MANIFEST, networks, peers, routers, resources, policies)

    def test_plan_rejects_broader_existing_workspace_policy(self) -> None:
        networks, peers, routers, resources, policies = api_state()
        resources.append(
            {
                "id": "rupan-resource-id",
                "name": "rupan-workspace",
                "address": "172.31.10.2/32",
                "enabled": True,
                "groups": [],
            }
        )
        policies.append(
            {
                "id": "rupan-policy-id",
                "name": "rupan-to-workspace",
                "enabled": True,
                "rules": [
                    {
                        "name": "rupan-to-workspace",
                        "enabled": True,
                        "action": "accept",
                        "bidirectional": True,
                        "protocol": "tcp",
                        "ports": ["22", "443"],
                        "sourceResource": {"id": "rupan-peer-id", "type": "peer"},
                        "destinationResource": {
                            "id": "rupan-resource-id",
                            "type": "host",
                        },
                    }
                ],
            }
        )

        with self.assertRaisesRegex(
            ReconcileError, "policy rupan-to-workspace differs"
        ):
            build_plan(MANIFEST, networks, peers, routers, resources, policies)
