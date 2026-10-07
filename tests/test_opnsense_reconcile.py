import json
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from opnsense_reconciler.inventory import Credentials
from opnsense_reconciler.reconcile import (
    main,
    reconcile_bgp_neighbors,
    reconcile_interfaces,
    reconcile_kea_interfaces,
    reconcile_outbound_nat,
    reconcile_udp_broadcast_relays,
    reconcile_unbound_acls,
    reconcile_unbound_host_overrides,
    resolve_vlan_devices,
    verify_bgp,
)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get(self, path: str) -> object:
        raise AssertionError(f"unexpected read: {path}")

    def post(self, path: str, payload: object) -> object:
        raise AssertionError(f"unexpected write: {path}")


class ReconcileInterfaceTests(unittest.TestCase):
    def test_reconcile_udp_broadcast_relays_adds_and_verifies_instances(self) -> None:
        relay = {
            "InstanceID": "1",
            "RevertTTL": "0",
            "description": "Govee LAN discovery",
            "enabled": "1",
            "interfaces": "opt1,opt3",
            "listenport": "4002",
            "multicastaddress": "239.255.255.250",
            "sourceaddress": "",
        }

        class RelayClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, object | None]] = []
                self.rows: list[dict[str, object]] = []

            def get(self, path: str) -> object:
                self.calls.append(("GET", path, None))
                if path.startswith("/api/udpbroadcastrelay/service/status/"):
                    return {"result": "OK"}
                return {"rows": self.rows}

            def post(self, path: str, payload: object) -> object:
                self.calls.append(("POST", path, payload))
                self.rows = [dict(relay, uuid="relay-1")]
                return {"result": "saved"}

        client = RelayClient()
        reconcile_udp_broadcast_relays(client, [relay])

        self.assertEqual(
            client.calls[1][0:2], ("POST", "/api/udpbroadcastrelay/settings/add_relay")
        )
        self.assertEqual(
            client.calls[2][0:2],
            ("GET", "/api/udpbroadcastrelay/settings/search_relay"),
        )
        self.assertIn(
            ("GET", "/api/udpbroadcastrelay/service/restart/relay-1", None),
            client.calls,
        )

    def test_reconcile_udp_broadcast_relays_skips_restart_when_in_sync(self) -> None:
        relay = {
            "InstanceID": "1",
            "RevertTTL": "0",
            "description": "Govee LAN discovery",
            "enabled": "1",
            "interfaces": "opt1,opt3",
            "listenport": "4002",
            "multicastaddress": "239.255.255.250",
            "sourceaddress": "",
        }

        class InSyncClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, object | None]] = []

            def get(self, path: str) -> object:
                self.calls.append(("GET", path, None))
                if path.startswith("/api/udpbroadcastrelay/service/status/"):
                    return {"result": "OK"}
                return {"rows": [dict(relay, uuid="relay-1")]}

            def post(self, path: str, payload: object) -> object:
                raise AssertionError(f"unexpected write: {path}")

        client = InSyncClient()
        reconcile_udp_broadcast_relays(client, [relay])

        self.assertTrue(
            all("service/restart" not in path for _, path, _ in client.calls)
        )

    def test_main_loads_protected_environment_and_desired_assignments(self) -> None:
        seen: dict[str, object] = {}

        def client_factory(
            url: str,
            credentials: Credentials,
            ca_file: str,
            server_name: str | None,
        ) -> FakeClient:
            seen["url"] = url
            seen["credentials"] = credentials
            seen["ca_file"] = ca_file
            seen["server_name"] = server_name
            return FakeClient()

        def reconcile(
            client: object,
            assignment_api_available: bool,
            desired: list[dict[str, object]],
        ) -> None:
            seen["client"] = client
            seen["assignment_api_available"] = assignment_api_available
            seen["desired"] = desired

        def reconcile_kea(client: object, desired: set[str]) -> None:
            seen["kea_client"] = client
            seen["kea_interfaces"] = desired

        def reconcile_bgp(
            client: object, desired: list[dict[str, object]]
        ) -> dict[str, int]:
            seen["bgp_client"] = client
            seen["bgp_neighbors"] = desired
            return {"10.0.30.11": 64512}

        def reconcile_outbound(
            client: object, desired: list[dict[str, object]]
        ) -> None:
            seen["outbound_client"] = client
            seen["outbound_rules"] = desired

        def reconcile_acls(client: object, desired: list[dict[str, object]]) -> None:
            seen["acl_client"] = client
            seen["acls"] = desired

        def reconcile_host_overrides(
            client: object, desired: list[dict[str, object]]
        ) -> None:
            seen["override_client"] = client
            seen["host_overrides"] = desired

        def reconcile_relays(client: object, desired: list[dict[str, object]]) -> None:
            seen["relay_client"] = client
            seen["relays"] = desired

        def ensure_relay_plugin(client: object) -> None:
            seen["relay_plugin_client"] = client

        def prove_bgp(
            client: object, peers: dict[str, int], routes: set[str]
        ) -> object:
            seen["proof_peers"] = peers
            seen["proof_routes"] = routes

            @dataclass(frozen=True)
            class Proof:
                ready: bool = True

            return Proof()

        with TemporaryDirectory() as directory:
            root = Path(directory)
            assignments = root / "assignments.json"
            inventory = root / "inventory.json"
            kea_interfaces = root / "kea-interfaces.json"
            udp_broadcast_relays = root / "udp-broadcast-relays.json"
            bgp_neighbors = root / "bgp-neighbors.json"
            bgp_expected_routes = root / "bgp-expected-routes.json"
            outbound_nat_rules = root / "outbound-nat-rules.json"
            unbound_acls = root / "unbound-acls.json"
            unbound_host_overrides = root / "unbound-host-overrides.json"
            assignments.write_text('[{"parent":"igb0","tag":30}]')
            inventory.write_text('{"assignment_api_available":true}')
            kea_interfaces.write_text('["opt1","opt2"]')
            udp_broadcast_relays.write_text("[]")
            bgp_neighbors.write_text('[{"address":"10.0.30.11","remoteas":"64512"}]')
            bgp_expected_routes.write_text('["10.0.40.10/32"]')
            outbound_nat_rules.write_text("[]")
            unbound_acls.write_text("[]")
            unbound_host_overrides.write_text("[]")

            main(
                [
                    "--assignments",
                    str(assignments),
                    "--inventory",
                    str(inventory),
                    "--kea-interfaces",
                    str(kea_interfaces),
                    "--udp-broadcast-relays",
                    str(udp_broadcast_relays),
                    "--bgp-neighbors",
                    str(bgp_neighbors),
                    "--bgp-expected-routes",
                    str(bgp_expected_routes),
                    "--outbound-nat-rules",
                    str(outbound_nat_rules),
                    "--unbound-acls",
                    str(unbound_acls),
                    "--unbound-host-overrides",
                    str(unbound_host_overrides),
                ],
                environ={
                    "OPNSENSE_URL": "https://192.168.1.1",
                    "OPNSENSE_API_KEY": "api-key",
                    "OPNSENSE_API_SECRET": "api-secret",
                    "OPNSENSE_CA_FILE": "/tmp/opnsense-ca.pem",
                    "OPNSENSE_TLS_SERVER_NAME": "OPNsense.internal",
                },
                client_factory=client_factory,
                reconcile=reconcile,
                reconcile_kea=reconcile_kea,
                reconcile_bgp=reconcile_bgp,
                reconcile_outbound=reconcile_outbound,
                reconcile_acls=reconcile_acls,
                reconcile_host_overrides=reconcile_host_overrides,
                reconcile_relays=reconcile_relays,
                ensure_relay_plugin=ensure_relay_plugin,
                prove_bgp=prove_bgp,
            )

        self.assertEqual(seen["credentials"], Credentials("api-key", "api-secret"))
        self.assertEqual(seen["assignment_api_available"], True)
        self.assertEqual(seen["desired"], [{"parent": "igb0", "tag": 30}])
        self.assertEqual(seen["kea_interfaces"], {"opt1", "opt2"})
        self.assertEqual(
            seen["bgp_neighbors"], [{"address": "10.0.30.11", "remoteas": "64512"}]
        )
        self.assertEqual(seen["proof_peers"], {"10.0.30.11": 64512})
        self.assertEqual(seen["proof_routes"], {"10.0.40.10/32"})
        self.assertEqual(seen["outbound_rules"], [])
        self.assertEqual(seen["acls"], [])
        self.assertEqual(seen["host_overrides"], [])
        self.assertEqual(seen["relays"], [])

    def test_resolve_vlan_devices_uses_live_parent_and_tag(self) -> None:
        desired = [
            {"descr": "clients", "parent": "igb0", "tag": 20, "ipaddr": "10.0.20.1/24"}
        ]
        live_vlans = {
            "rows": [
                {
                    "if": "igb0",
                    "tag": "20",
                    "vlanif": "vlan01 [CLUSTER]",
                }
            ]
        }

        resolved = resolve_vlan_devices(desired, live_vlans)

        self.assertEqual(
            resolved,
            [{"descr": "clients", "if": "vlan01", "ipaddr": "10.0.20.1/24"}],
        )

    def test_resolve_vlan_devices_refuses_missing_tag(self) -> None:
        desired = [{"descr": "infrastructure", "parent": "igb0", "tag": 30}]

        with self.assertRaisesRegex(RuntimeError, "igb0 tag 30"):
            resolve_vlan_devices(desired, {"rows": []})

    def test_reconcile_interfaces_refuses_unavailable_assignment_api(self) -> None:
        client = FakeClient()

        with self.assertRaisesRegex(RuntimeError, "assignment API unavailable"):
            reconcile_interfaces(
                client=client,
                assignment_api_available=False,
                desired_interfaces=[],
            )

        self.assertEqual(client.calls, [])

    def test_reconcile_interfaces_reads_current_assignments_before_noop(self) -> None:
        class AssignmentClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def get(self, path: str) -> object:
                self.calls.append(("GET", path))
                return {"rows": []}

            def post(self, path: str, payload: object) -> object:
                raise AssertionError("no-op reconciliation must not write")

        client = AssignmentClient()

        reconcile_interfaces(
            client=client,
            assignment_api_available=True,
            desired_interfaces=[],
        )

        self.assertEqual(
            client.calls, [("GET", "/api/interfaces/assignment/search_item")]
        )

    def test_reconcile_interfaces_adds_assignment_and_verifies_runtime(self) -> None:
        class AssignmentClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, object | None]] = []

            def get(self, path: str) -> object:
                self.calls.append(("GET", path, None))
                if path == "/api/interfaces/vlan_settings/search_item":
                    return {
                        "rows": [
                            {"if": "igb0", "tag": "10", "vlanif": "vlan02 [management]"}
                        ]
                    }
                if path == "/api/interfaces/assignment/search_item":
                    return {"rows": []}
                if path == "/api/interfaces/overview/interfaces_info/true":
                    return {
                        "rows": [
                            {
                                "device": "vlan02",
                                "addr4": "10.0.10.1/24",
                                "description": "management",
                            }
                        ]
                    }
                raise AssertionError(f"unexpected read: {path}")

            def post(self, path: str, payload: object) -> object:
                self.calls.append(("POST", path, payload))
                return {"result": "saved"}

        client = AssignmentClient()
        desired = {
            "descr": "management",
            "disablevlanhwfilter": "0",
            "enable": "1",
            "ipaddr": "10.0.10.1/24",
            "lock": "1",
            "parent": "igb0",
            "tag": 10,
            "type4": "staticv4",
            "type6": "none",
            "dhcp6-ia-pd-len": "0",
        }

        reconcile_interfaces(client, True, [desired])
        resolved_desired = {
            key: value for key, value in desired.items() if key not in {"parent", "tag"}
        }
        resolved_desired["if"] = "vlan02"

        self.assertEqual(
            client.calls,
            [
                ("GET", "/api/interfaces/vlan_settings/search_item", None),
                ("GET", "/api/interfaces/assignment/search_item", None),
                (
                    "POST",
                    "/api/interfaces/assignment/add_item",
                    {"interface": resolved_desired},
                ),
                ("POST", "/api/interfaces/assignment/reconfigure", {}),
                ("GET", "/api/interfaces/overview/interfaces_info/true", None),
            ],
        )

    def test_reconcile_interfaces_refuses_static_l3_runtime_drift(self) -> None:
        class DriftClient:
            def get(self, path: str) -> object:
                responses = {
                    "/api/interfaces/vlan_settings/search_item": {
                        "rows": [
                            {"if": "igb0", "tag": "10", "vlanif": "vlan02 [management]"}
                        ]
                    },
                    "/api/interfaces/assignment/search_item": {
                        "rows": [{"if": "vlan02", "identifier": "opt2"}]
                    },
                    "/api/interfaces/overview/interfaces_info/true": {
                        "rows": [
                            {
                                "device": "vlan02",
                                "addr4": None,
                                "description": "management",
                            }
                        ]
                    },
                }
                return responses[path]

            def post(self, path: str, payload: object) -> object:
                raise AssertionError(
                    f"runtime drift must not invoke unsupported write: {path}"
                )

        desired = [
            {
                "parent": "igb0",
                "tag": 10,
                "descr": "management",
                "ipaddr": "10.0.10.1/24",
            }
        ]

        with self.assertRaisesRegex(
            RuntimeError, "OPNsense 26.7 assignment API cannot configure"
        ):
            reconcile_interfaces(DriftClient(), True, desired)

    def test_reconcile_kea_interfaces_updates_and_verifies_listener_set(self) -> None:
        class KeaClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, object | None]] = []
                self.reads = 0

            def get(self, path: str) -> object:
                self.calls.append(("GET", path, None))
                if path == "/api/kea/service/status":
                    return {"status": "running"}
                self.reads += 1
                selected = (
                    {"opt1"} if self.reads == 1 else {"opt1", "opt2", "opt3", "opt5"}
                )
                return {
                    "dhcpv4": {
                        "general": {
                            "interfaces": {
                                name: {"selected": int(name in selected), "value": name}
                                for name in ("lan", "opt1", "opt2", "opt3", "opt5")
                            }
                        }
                    }
                }

            def post(self, path: str, payload: object) -> object:
                self.calls.append(("POST", path, payload))
                return {"result": "saved"}

        client = KeaClient()
        desired = {"opt1", "opt2", "opt3", "opt5"}

        reconcile_kea_interfaces(client, desired)

        self.assertIn(
            (
                "POST",
                "/api/kea/dhcpv4/set",
                {
                    "dhcpv4": {
                        "general": {
                            "enabled": "1",
                            "manual_config": "0",
                            "interfaces": "opt1,opt2,opt3,opt5",
                            "valid_lifetime": "4000",
                            "fwrules": "1",
                            "dhcp_socket_type": "raw",
                        }
                    }
                },
            ),
            client.calls,
        )
        self.assertIn(("POST", "/api/kea/service/reconfigure", {}), client.calls)

    def test_assignment_config_declares_all_vlan_gateways(self) -> None:
        path = (
            Path(__file__).resolve().parents[1] / "opnsense_reconciler/assignments.json"
        )
        assignments = json.loads(path.read_text())

        self.assertEqual(
            {(assignment["parent"], assignment["tag"]) for assignment in assignments},
            {
                ("igb0", 10),
                ("igb0", 20),
                ("igb0", 30),
                ("igb0", 40),
                ("igb0", 50),
                ("igb0", 60),
            },
        )
        self.assertEqual(
            {assignment["ipaddr"] for assignment in assignments},
            {
                "10.0.10.1/24",
                "10.0.20.1/24",
                "10.0.30.1/24",
                "10.0.40.1/24",
                "10.0.50.1/24",
                "10.0.60.1/24",
            },
        )


class BgpVerificationTests(unittest.TestCase):
    def test_verify_bgp_reports_missing_canary_route(self) -> None:
        class BgpClient:
            def get(self, path: str) -> object:
                responses = {
                    "/api/quagga/service/status": {"status": "running"},
                    "/api/quagga/diagnostics/bgpsummary": {
                        "response": {
                            "peers": {
                                "10.0.30.11": {
                                    "state": "Established",
                                    "remoteAs": 64512,
                                }
                            }
                        }
                    },
                    "/api/quagga/diagnostics/search_bgproute4": {"rows": []},
                }
                return responses[path]

        result = verify_bgp(
            BgpClient(),
            expected_peers={"10.0.30.11": 64512},
            expected_routes={"10.0.40.10/32"},
        )

        self.assertFalse(result.ready)
        self.assertEqual(result.missing_routes, {"10.0.40.10/32"})

    def test_verify_bgp_reads_ipv4_unicast_peers_and_bare_host_routes(self) -> None:
        class BgpClient:
            def get(self, path: str) -> object:
                responses = {
                    "/api/quagga/service/status": {"status": "running"},
                    "/api/quagga/diagnostics/bgpsummary": {
                        "response": {
                            "ipv4Unicast": {
                                "peers": {
                                    "10.0.30.11": {
                                        "state": "Established",
                                        "remoteAs": 64512,
                                    },
                                    "10.0.30.12": {
                                        "state": "Established",
                                        "remoteAs": 64512,
                                    },
                                    "10.0.30.13": {
                                        "state": "Established",
                                        "remoteAs": 64512,
                                    },
                                }
                            }
                        }
                    },
                    "/api/quagga/diagnostics/search_bgproute4": {
                        "rows": [{"prefix": "10.0.40.10"}]
                    },
                }
                return responses[path]

        result = verify_bgp(
            BgpClient(),
            expected_peers={
                "10.0.30.11": 64512,
                "10.0.30.12": 64512,
                "10.0.30.13": 64512,
            },
            expected_routes={"10.0.40.10/32"},
        )

        self.assertTrue(result.ready)
        self.assertEqual(result.missing_peers, set())
        self.assertEqual(result.missing_routes, set())

    def test_bgp_expected_routes_stay_inside_lb_pool(self) -> None:
        import ipaddress

        path = (
            Path(__file__).resolve().parents[1]
            / "opnsense_reconciler/bgp-expected-routes.json"
        )
        expected = json.loads(path.read_text())
        pool = ipaddress.ip_network("10.0.40.0/24")

        self.assertIsInstance(expected, list)
        self.assertTrue(expected)
        for prefix in expected:
            with self.subTest(prefix=prefix):
                network = ipaddress.ip_network(prefix)
                self.assertEqual(network.prefixlen, 32)
                self.assertTrue(network.subnet_of(pool))
                self.assertIn(
                    int(network.network_address) - int(pool.network_address),
                    range(10, 20),
                )


class BgpNeighborReconciliationTests(unittest.TestCase):
    def desired(self) -> list[dict[str, object]]:
        return [
            {
                "address": "10.0.30.11",
                "description": "cilium-homelab-01",
                "remoteas": "64512",
                "updatesource": "opt3",
                "nexthopself": "1",
                "enabled": "1",
            },
            {
                "address": "10.0.30.12",
                "description": "cilium-homelab-02",
                "remoteas": "64512",
                "updatesource": "opt3",
                "nexthopself": "1",
                "enabled": "1",
            },
        ]

    def test_noop_when_live_matches_desired(self) -> None:
        class BgpClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def get(self, path: str) -> object:
                self.calls.append(("GET", path))
                return {
                    "rows": [
                        {
                            "uuid": "uuid-11",
                            "address": "10.0.30.11",
                            "description": "cilium-homelab-01",
                            "remoteas": "64512",
                            "updatesource": "opt3",
                            "nexthopself": "1",
                            "enabled": "1",
                        },
                        {
                            "uuid": "uuid-12",
                            "address": "10.0.30.12",
                            "description": "cilium-homelab-02",
                            "remoteas": "64512",
                            "updatesource": "opt3",
                            "nexthopself": "1",
                            "enabled": "1",
                        },
                    ]
                }

            def post(self, path: str, payload: object) -> object:
                raise AssertionError(f"matching state must not write: {path}")

        client = BgpClient()

        peers = reconcile_bgp_neighbors(client, self.desired())

        self.assertEqual(peers, {"10.0.30.11": 64512, "10.0.30.12": 64512})
        self.assertEqual(
            client.calls,
            [
                ("GET", "/api/quagga/bgp/search_neighbor"),
                ("GET", "/api/quagga/bgp/search_neighbor"),
            ],
        )

    def test_adds_missing_peer_reconfigures_and_verifies(self) -> None:
        class BgpClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, object | None]] = []
                self.reads = 0

            def get(self, path: str) -> object:
                self.calls.append(("GET", path, None))
                self.reads += 1
                rows = [
                    {
                        "uuid": "uuid-11",
                        "address": "10.0.30.11",
                        "description": "cilium-homelab-01",
                        "remoteas": "64512",
                        "updatesource": "opt3",
                        "nexthopself": "1",
                        "enabled": "1",
                    }
                ]
                if self.reads > 1:
                    rows.append(
                        {
                            "uuid": "uuid-12",
                            "address": "10.0.30.12",
                            "description": "cilium-homelab-02",
                            "remoteas": "64512",
                            "updatesource": "opt3",
                            "nexthopself": "1",
                            "enabled": "1",
                        }
                    )
                return {"rows": rows}

            def post(self, path: str, payload: object) -> object:
                self.calls.append(("POST", path, payload))
                return {"result": "saved"}

        client = BgpClient()

        reconcile_bgp_neighbors(client, self.desired())

        self.assertIn(
            (
                "POST",
                "/api/quagga/bgp/add_neighbor",
                {
                    "neighbor": {
                        "address": "10.0.30.12",
                        "description": "cilium-homelab-02",
                        "remoteas": "64512",
                        "updatesource": "opt3",
                        "nexthopself": "1",
                        "enabled": "1",
                    }
                },
            ),
            client.calls,
        )
        self.assertIn(("POST", "/api/quagga/service/reconfigure", {}), client.calls)

    def test_updates_drifted_remote_as_by_uuid(self) -> None:
        class BgpClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, object | None]] = []
                self.rows = [
                    {
                        "uuid": "uuid-11",
                        "address": "10.0.30.11",
                        "description": "cilium-homelab-01",
                        "remoteas": "64599",
                        "updatesource": "opt3",
                        "nexthopself": "1",
                        "enabled": "1",
                    },
                    {
                        "uuid": "uuid-12",
                        "address": "10.0.30.12",
                        "description": "cilium-homelab-02",
                        "remoteas": "64512",
                        "updatesource": "opt3",
                        "nexthopself": "1",
                        "enabled": "1",
                    },
                ]

            def get(self, path: str) -> object:
                self.calls.append(("GET", path, None))
                return {"rows": [dict(row) for row in self.rows]}

            def post(self, path: str, payload: object) -> object:
                self.calls.append(("POST", path, payload))
                uuid = path.rsplit("/", 1)[-1]
                neighbor = (
                    payload.get("neighbor") if isinstance(payload, dict) else None
                )
                for row in self.rows:
                    if row["uuid"] == uuid and isinstance(neighbor, dict):
                        row.update(neighbor)
                return {"result": "saved"}

        client = BgpClient()

        reconcile_bgp_neighbors(client, self.desired())

        update = [
            call
            for call in client.calls
            if call[1] == "/api/quagga/bgp/set_neighbor/uuid-11"
        ]
        self.assertEqual(len(update), 1)
        self.assertEqual(
            update[0][2],
            {
                "neighbor": {
                    "address": "10.0.30.11",
                    "description": "cilium-homelab-01",
                    "remoteas": "64512",
                    "updatesource": "opt3",
                    "nexthopself": "1",
                    "enabled": "1",
                }
            },
        )

    def test_refuses_unknown_live_peer_without_writing(self) -> None:
        class BgpClient:
            def get(self, path: str) -> object:
                return {
                    "rows": [
                        {
                            "uuid": "uuid-11",
                            "address": "10.0.30.11",
                            "description": "cilium-homelab-01",
                            "remoteas": "64512",
                            "updatesource": "opt3",
                            "nexthopself": "1",
                            "enabled": "1",
                        },
                        {
                            "uuid": "uuid-rogue",
                            "address": "10.0.30.99",
                            "description": "rogue",
                            "remoteas": "64512",
                            "updatesource": "opt3",
                            "nexthopself": "1",
                            "enabled": "1",
                        },
                    ]
                }

            def post(self, path: str, payload: object) -> object:
                raise AssertionError(
                    f"unexpected peer must not be deleted automatically: {path}"
                )

        with self.assertRaisesRegex(RuntimeError, "10.0.30.99"):
            reconcile_bgp_neighbors(
                client=BgpClient(), desired_neighbors=self.desired()[:1]
            )

    def test_rejects_failed_store_result(self) -> None:
        class BgpClient:
            def get(self, path: str) -> object:
                return {"rows": []}

            def post(self, path: str, payload: object) -> object:
                return {
                    "result": "failed",
                    "validations": {"neighbor.remoteas": "invalid"},
                }

        with self.assertRaisesRegex(RuntimeError, "failed"):
            reconcile_bgp_neighbors(
                client=BgpClient(), desired_neighbors=self.desired()[:1]
            )

    def test_rejects_duplicate_desired_addresses(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate"):
            reconcile_bgp_neighbors(
                client=FakeClient(),
                desired_neighbors=[self.desired()[0], self.desired()[0]],
            )

    def test_bgp_neighbor_config_declares_three_cilium_peers(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "opnsense_reconciler/bgp-neighbors.json"
        )
        neighbors = json.loads(path.read_text())

        self.assertEqual(
            {neighbor["address"] for neighbor in neighbors},
            {"10.0.30.11", "10.0.30.12", "10.0.30.13"},
        )
        for neighbor in neighbors:
            with self.subTest(address=neighbor.get("address")):
                self.assertEqual(neighbor["remoteas"], "64512")
                self.assertEqual(neighbor["updatesource"], "opt3")
                self.assertEqual(neighbor["nexthopself"], "1")
                self.assertEqual(neighbor["enabled"], "1")


class OutboundNatReconciliationTests(unittest.TestCase):
    _DESIRED_RULE = {
        "interface": "wan",
        "ipprotocol": "inet",
        "protocol": "any",
        "source_net": "10.0.10.0/24",
        "source_not": "0",
        "source_port": "",
        "destination_net": "any",
        "destination_not": "0",
        "destination_port": "",
        "target": "wanip",
        "target_port": "",
        "poolopts": "round-robin",
        "staticnatport": "0",
        "log": "0",
        "nonat": "0",
        "nosync": "0",
        "endpoint_independent": "0",
        "description": "Management VLAN to WAN",
        "enabled": "1",
        "sequence": 200000,
    }

    def desired(self) -> list[dict[str, object]]:
        return [dict(self._DESIRED_RULE)]

    def test_noop_when_live_matches_desired(self) -> None:
        rule = self._DESIRED_RULE

        class NatClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def get(self, path: str) -> object:
                self.calls.append(("GET", path))
                return {"rows": [dict(rule, uuid="uuid-nat")]}

            def post(self, path: str, payload: object) -> object:
                raise AssertionError(f"matching outbound NAT must not write: {path}")

        client = NatClient()
        reconcile_outbound_nat(client, self.desired())
        self.assertEqual(
            client.calls,
            [
                ("GET", "/api/firewall/source_nat/search_rule"),
                ("GET", "/api/firewall/source_nat/search_rule"),
            ],
        )

    def test_verification_normalizes_bool_to_string_enabled(self) -> None:
        rule = self._DESIRED_RULE

        class NatClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def get(self, path: str) -> object:
                self.calls.append(("GET", path))
                # OPNsense may return enabled as bool True instead of "1".
                live_rule = dict(rule, uuid="uuid-nat", enabled=True)
                return {"rows": [live_rule]}

            def post(self, path: str, payload: object) -> object:
                raise AssertionError(f"normalized match must not write: {path}")

        client = NatClient()
        reconcile_outbound_nat(client, self.desired())

    def test_verification_normalizes_string_sequence_to_int(self) -> None:
        rule = self._DESIRED_RULE

        class NatClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def get(self, path: str) -> object:
                self.calls.append(("GET", path))
                # OPNsense search_rule returns sequence as a string.
                live_rule = dict(rule, uuid="uuid-nat", sequence="200000")
                return {"rows": [live_rule]}

            def post(self, path: str, payload: object) -> object:
                raise AssertionError(f"normalized match must not write: {path}")

        client = NatClient()
        reconcile_outbound_nat(client, self.desired())

    def test_adds_missing_rule_applies_and_verifies(self) -> None:
        rule = self._DESIRED_RULE

        class NatClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, object | None]] = []
                self.reads = 0

            def get(self, path: str) -> object:
                self.calls.append(("GET", path, None))
                self.reads += 1
                rows: list[dict[str, object]] = []
                if self.reads > 1:
                    rows.append(dict(rule, uuid="uuid-nat"))
                return {"rows": rows}

            def post(self, path: str, payload: object) -> object:
                self.calls.append(("POST", path, payload))
                return {"result": "saved"}

        client = NatClient()
        reconcile_outbound_nat(client, self.desired())
        self.assertIn(
            ("POST", "/api/firewall/source_nat/add_rule", {"rule": rule}),
            client.calls,
        )
        self.assertIn(("POST", "/api/firewall/filter/apply", {}), client.calls)

    def test_refuses_unknown_live_rule_without_writing(self) -> None:
        class NatClient:
            def get(self, path: str) -> object:
                return {
                    "rows": [
                        {
                            "uuid": "uuid-rogue",
                            "interface": "lan",
                            "ipprotocol": "inet",
                            "protocol": "any",
                            "source_net": "192.168.0.0/24",
                            "destination_net": "any",
                            "target": "wanip",
                            "description": "rogue",
                            "enabled": "1",
                            "sequence": 1,
                        }
                    ]
                }

            def post(self, path: str, payload: object) -> object:
                raise AssertionError(
                    f"unexpected rule must not be removed automatically: {path}"
                )

        with self.assertRaisesRegex(RuntimeError, "rogue"):
            reconcile_outbound_nat(NatClient(), [])

    def test_filters_auto_generated_rules_from_live_inventory(self) -> None:
        rule = self._DESIRED_RULE

        class NatClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, object | None]] = []
                self.reads = 0

            def get(self, path: str) -> object:
                self.calls.append(("GET", path, None))
                self.reads += 1
                rows: list[dict[str, object]] = [
                    {
                        "uuid": "uuid-auto",
                        "interface": "wan",
                        "ipprotocol": "inet",
                        "protocol": "any",
                        "source_net": "10.0.20.0/24",
                        "destination_net": "any",
                        "target": "wanip",
                        "description": "Auto created rule",
                        "enabled": "1",
                        "sequence": 1,
                    },
                    {
                        "uuid": "uuid-isakmp",
                        "interface": "wan",
                        "ipprotocol": "inet",
                        "protocol": "udp",
                        "source_net": "any",
                        "destination_net": "any",
                        "destination_port": "500",
                        "target": "wanip",
                        "description": "Auto created rule for ISAKMP",
                        "enabled": "1",
                        "sequence": 2,
                    },
                ]
                if self.reads > 1:
                    rows.append(dict(rule, uuid="uuid-mgmt"))
                return {"rows": rows}

            def post(self, path: str, payload: object) -> object:
                self.calls.append(("POST", path, payload))
                return {"result": "saved"}

        client = NatClient()
        reconcile_outbound_nat(client, self.desired())
        add_calls = [
            call
            for call in client.calls
            if call[1] == "/api/firewall/source_nat/add_rule"
        ]
        self.assertEqual(len(add_calls), 1)

    def test_rejects_failed_store_result(self) -> None:
        class NatClient:
            def get(self, path: str) -> object:
                return {"rows": []}

            def post(self, path: str, payload: object) -> object:
                return {"result": "failed", "validations": {"rule.sequence": "x"}}

        with self.assertRaisesRegex(RuntimeError, "failed"):
            reconcile_outbound_nat(NatClient(), self.desired())

    def test_rejects_duplicate_descriptions(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate"):
            reconcile_outbound_nat(
                client=FakeClient(),
                desired_rules=[self.desired()[0], self.desired()[0]],
            )

    def test_outbound_nat_rules_stay_inside_homelab_subnets(self) -> None:
        import ipaddress

        path = (
            Path(__file__).resolve().parents[1]
            / "opnsense_reconciler/outbound-nat-rules.json"
        )
        rules = json.loads(path.read_text())

        self.assertIsInstance(rules, list)
        for rule in rules:
            with self.subTest(description=rule.get("description")):
                self.assertEqual(rule["interface"], "wan")
                self.assertEqual(rule["ipprotocol"], "inet")
                source_net = ipaddress.ip_network(rule["source_net"])
                self.assertIn(source_net.prefixlen, (24,), "management source is /24")
                self.assertEqual(rule["destination_net"], "any")
                self.assertEqual(rule["target"], "wanip")


class UnboundAclReconciliationTests(unittest.TestCase):
    def desired(self) -> list[dict[str, object]]:
        return [
            {
                "name": "management-allowed",
                "enabled": "1",
                "action": "allow",
                "networks": ["10.0.10.0/24"],
                "description": "Allow recursive DNS from the management VLAN",
            }
        ]

    def test_noop_when_live_matches_desired(self) -> None:
        class AclClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def get(self, path: str) -> object:
                self.calls.append(("GET", path))
                return {
                    "rows": [
                        {
                            "uuid": "uuid-acl",
                            "name": "management-allowed",
                            "enabled": "1",
                            "action": "allow",
                            "networks": "10.0.10.0/24",
                        }
                    ]
                }

            def post(self, path: str, payload: object) -> object:
                raise AssertionError(f"matching ACL must not write: {path}")

        client = AclClient()
        reconcile_unbound_acls(client, self.desired())
        self.assertEqual(
            client.calls,
            [
                ("GET", "/api/unbound/settings/search_acl"),
                ("GET", "/api/unbound/settings/search_acl"),
            ],
        )

    def test_adds_missing_acl_reconfigures_and_verifies(self) -> None:
        class AclClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, object | None]] = []
                self.reads = 0

            def get(self, path: str) -> object:
                self.calls.append(("GET", path, None))
                self.reads += 1
                rows: list[dict[str, object]] = []
                if self.reads > 1:
                    rows.append(
                        {
                            "uuid": "uuid-acl",
                            "name": "management-allowed",
                            "enabled": "1",
                            "action": "allow",
                            "networks": "10.0.10.0/24",
                        }
                    )
                return {"rows": rows}

            def post(self, path: str, payload: object) -> object:
                self.calls.append(("POST", path, payload))
                return {"result": "saved"}

        client = AclClient()
        reconcile_unbound_acls(client, self.desired())
        self.assertIn(
            (
                "POST",
                "/api/unbound/settings/add_acl",
                {
                    "acl": {
                        "name": "management-allowed",
                        "enabled": "1",
                        "action": "allow",
                        "networks": "10.0.10.0/24",
                        "description": "Allow recursive DNS from the management VLAN",
                    }
                },
            ),
            client.calls,
        )
        self.assertIn(("POST", "/api/unbound/service/reconfigure", {}), client.calls)

    def test_refuses_unknown_live_acl_without_writing(self) -> None:
        class AclClient:
            def get(self, path: str) -> object:
                return {
                    "rows": [
                        {
                            "uuid": "uuid-rogue",
                            "name": "rogue",
                            "action": "allow",
                            "networks": "192.168.0.0/24",
                        }
                    ]
                }

            def post(self, path: str, payload: object) -> object:
                raise AssertionError(
                    f"unexpected ACL must not be removed automatically: {path}"
                )

        with self.assertRaisesRegex(RuntimeError, "rogue"):
            reconcile_unbound_acls(AclClient(), [])

    def test_rejects_invalid_action(self) -> None:
        with self.assertRaisesRegex(ValueError, "action"):
            reconcile_unbound_acls(
                client=FakeClient(),
                desired_acls=[
                    {
                        "name": "bad",
                        "enabled": "1",
                        "action": "trust-me",
                        "networks": ["10.0.10.0/24"],
                    }
                ],
            )

    def test_rejects_non_list_networks(self) -> None:
        with self.assertRaisesRegex(ValueError, "networks"):
            reconcile_unbound_acls(
                client=FakeClient(),
                desired_acls=[
                    {
                        "name": "bad",
                        "enabled": "1",
                        "action": "allow",
                        "networks": "10.0.10.0/24",
                    }
                ],
            )

    def test_rejects_invalid_enabled(self) -> None:
        with self.assertRaisesRegex(ValueError, "enabled"):
            reconcile_unbound_acls(
                client=FakeClient(),
                desired_acls=[
                    {
                        "name": "bad",
                        "enabled": "yes",
                        "action": "allow",
                        "networks": ["10.0.10.0/24"],
                    }
                ],
            )

    def test_unbound_acls_cover_homelab_subnets(self) -> None:
        import ipaddress

        path = (
            Path(__file__).resolve().parents[1]
            / "opnsense_reconciler/unbound-acls.json"
        )
        acls = json.loads(path.read_text())

        self.assertIsInstance(acls, list)
        seen_actions = {acl["action"] for acl in acls}
        self.assertEqual(seen_actions, {"allow"})
        for acl in acls:
            with self.subTest(name=acl.get("name")):
                self.assertTrue(acl["networks"])
                for net in acl["networks"]:
                    network = ipaddress.ip_network(net)
                    self.assertTrue(
                        network.subnet_of(ipaddress.ip_network("10.0.0.0/8"))
                    )


class UnboundHostOverrideReconciliationTests(unittest.TestCase):
    def desired(self) -> list[dict[str, object]]:
        return [
            {
                "hostname": "registry",
                "domain": "rupan.dev",
                "rr": "A",
                "server": "10.0.30.20",
                "description": "Local container registry",
                "enabled": "1",
            }
        ]

    def test_noop_when_live_matches_desired(self) -> None:
        class OverrideClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def get(self, path: str) -> object:
                self.calls.append(("GET", path))
                return {
                    "rows": [
                        {
                            "uuid": "uuid-override",
                            "hostname": "registry",
                            "domain": "rupan.dev",
                            "rr": "A",
                            "server": "10.0.30.20",
                            "enabled": "1",
                        }
                    ]
                }

            def post(self, path: str, payload: object) -> object:
                raise AssertionError(f"matching override must not write: {path}")

        client = OverrideClient()
        reconcile_unbound_host_overrides(client, self.desired())
        self.assertEqual(
            client.calls,
            [
                ("GET", "/api/unbound/settings/search_host_override"),
                ("GET", "/api/unbound/settings/search_host_override"),
            ],
        )

    def test_adds_missing_override_reconfigures_and_verifies(self) -> None:
        class OverrideClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, object | None]] = []
                self.reads = 0

            def get(self, path: str) -> object:
                self.calls.append(("GET", path, None))
                self.reads += 1
                rows: list[dict[str, object]] = []
                if self.reads > 1:
                    rows.append(
                        {
                            "uuid": "uuid-override",
                            "hostname": "registry",
                            "domain": "rupan.dev",
                            "rr": "A",
                            "server": "10.0.30.20",
                            "enabled": "1",
                        }
                    )
                return {"rows": rows}

            def post(self, path: str, payload: object) -> object:
                self.calls.append(("POST", path, payload))
                return {"result": "saved"}

        client = OverrideClient()
        reconcile_unbound_host_overrides(client, self.desired())
        self.assertIn(
            (
                "POST",
                "/api/unbound/settings/add_host_override",
                {
                    "host": {
                        "hostname": "registry",
                        "domain": "rupan.dev",
                        "rr": "A",
                        "server": "10.0.30.20",
                        "description": "Local container registry",
                        "enabled": "1",
                    }
                },
            ),
            client.calls,
        )
        self.assertIn(("POST", "/api/unbound/service/reconfigure", {}), client.calls)

    def test_updates_drifted_server_by_uuid(self) -> None:
        desired = self.desired()

        class OverrideClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, object | None]] = []
                self.rows: list[dict[str, object]] = [
                    {
                        "uuid": "uuid-override",
                        "hostname": "registry",
                        "domain": "rupan.dev",
                        "rr": "A",
                        "server": "10.0.30.99",
                        "enabled": "1",
                    }
                ]

            def get(self, path: str) -> object:
                self.calls.append(("GET", path, None))
                return {"rows": self.rows}

            def post(self, path: str, payload: object) -> object:
                self.calls.append(("POST", path, payload))
                if path.startswith("/api/unbound/settings/set_host_override/"):
                    self.rows = [dict(desired[0], uuid="uuid-override")]
                return {"result": "saved"}

        client = OverrideClient()
        reconcile_unbound_host_overrides(client, desired)
        self.assertIn(
            (
                "POST",
                "/api/unbound/settings/set_host_override/uuid-override",
                {
                    "host": {
                        "hostname": "registry",
                        "domain": "rupan.dev",
                        "rr": "A",
                        "server": "10.0.30.20",
                        "description": "Local container registry",
                        "enabled": "1",
                    }
                },
            ),
            client.calls,
        )

    def test_refuses_unknown_live_override_without_writing(self) -> None:
        class OverrideClient:
            def get(self, path: str) -> object:
                return {
                    "rows": [
                        {
                            "uuid": "uuid-rogue",
                            "hostname": "rogue",
                            "domain": "example.com",
                            "rr": "A",
                            "server": "192.0.2.1",
                        }
                    ]
                }

            def post(self, path: str, payload: object) -> object:
                raise AssertionError(
                    f"unexpected override must not be removed automatically: {path}"
                )

        with self.assertRaisesRegex(RuntimeError, "rogue.example.com/A"):
            reconcile_unbound_host_overrides(OverrideClient(), [])

    def test_rejects_record_type_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "AAAA"):
            reconcile_unbound_host_overrides(
                client=FakeClient(),
                desired_overrides=[
                    {
                        "hostname": "registry",
                        "domain": "rupan.dev",
                        "rr": "AAAA",
                        "server": "10.0.30.20",
                        "enabled": "1",
                    }
                ],
            )

    def test_rejects_dotted_hostname(self) -> None:
        with self.assertRaisesRegex(ValueError, "single label"):
            reconcile_unbound_host_overrides(
                client=FakeClient(),
                desired_overrides=[
                    {
                        "hostname": "registry.rupan.dev",
                        "domain": "rupan.dev",
                        "rr": "A",
                        "server": "10.0.30.20",
                        "enabled": "1",
                    }
                ],
            )

    def test_registry_override_points_at_nas(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "opnsense_reconciler/unbound-host-overrides.json"
        )
        overrides = json.loads(path.read_text())

        self.assertIsInstance(overrides, list)
        by_key = {
            f"{item['hostname']}.{item['domain']}/{item['rr']}": item
            for item in overrides
        }
        self.assertIn("registry.rupan.dev/A", by_key)
        self.assertEqual(by_key["registry.rupan.dev/A"]["server"], "10.0.30.20")
        self.assertEqual(by_key["registry.rupan.dev/A"]["enabled"], "1")
        self.assertIn("git.rupan.dev/A", by_key)
        self.assertEqual(by_key["git.rupan.dev/A"]["server"], "10.0.30.20")
        self.assertEqual(by_key["git.rupan.dev/A"]["enabled"], "1")
        # Adopted pre-existing overrides: removing either entry returns the
        # reconciler to fail-closed on the next apply.
        self.assertEqual(by_key["grafana.homelab/A"]["server"], "10.0.40.13")
        self.assertEqual(by_key["ntfy.homelab/A"]["server"], "10.0.40.12")


if __name__ == "__main__":
    unittest.main()
