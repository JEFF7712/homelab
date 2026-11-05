import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.agent.evidence import write_evidence
from scripts.agent.status import cluster_observation, configured_targets, status_payload

ROOT = Path(__file__).resolve().parents[1]


class AgentStatusTest(unittest.TestCase):
    def test_discovers_configured_targets(self) -> None:
        targets = configured_targets(ROOT)
        self.assertEqual(targets["cluster_api"], "https://10.0.30.11:6443")
        self.assertEqual(
            targets["cluster_nodes"],
            {
                f"homelab-{number:02}": f"10.0.30.{10 + number}"
                for number in range(1, 6)
            },
        )
        self.assertEqual(targets["opnsense"], "https://OPNsense.internal")
        self.assertEqual(targets["opnsense_ca_variable"], "OPNSENSE_CA_FILE")
        self.assertEqual(
            targets["opnsense_credential_variables"],
            ["OPNSENSE_READONLY_API_KEY", "OPNSENSE_READONLY_API_SECRET"],
        )

    def test_unknown_probe_is_nonhealthy_and_bounded(self) -> None:
        payload = status_payload(
            ROOT,
            "cluster",
            total_timeout=0.01,
            runner=lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError()),
        )
        self.assertEqual(payload["status"], "incomplete")
        self.assertTrue(payload["timestamp"])

    def test_discovery_uses_generated_inventory_without_nix_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "inventory").mkdir()
            (root / "inventory/fleet.json").write_text(
                json.dumps(
                    {
                        "cluster_api": "https://10.0.30.21:6443",
                        "cluster_nodes": {"homelab-01": "10.0.30.21"},
                        "hosts": {},
                    }
                )
            )
            (root / ".gitlab-ci.yml").write_text(
                'OPNSENSE_URI: "https://OPNsense.internal"\n'
            )
            targets = configured_targets(root)
            self.assertEqual(targets["cluster_api"], "https://10.0.30.21:6443")
            self.assertEqual(targets["cluster_nodes"], {"homelab-01": "10.0.30.21"})

    def test_cluster_probe_reports_each_read_only_observation(self) -> None:
        def successful(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            resource = {
                "metadata": {"name": "healthy", "generation": 1},
                "status": {
                    "phase": "Running",
                    "observedGeneration": 1,
                    "conditions": [{"type": "Ready", "status": "True"}],
                },
            }
            self.assertEqual(argv[-2:], ["-o", "json"])
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"items": [resource]}), ""
            )

        payload = status_payload(ROOT, "cluster", runner=successful)
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(
            [probe["name"] for probe in payload["probes"]],
            ["nodes", "flux", "workloads"],
        )

    def test_successful_api_calls_do_not_hide_unhealthy_resources(self) -> None:
        def runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {"name": "unhealthy", "generation": 2},
                                "status": {
                                    "phase": "Running",
                                    "observedGeneration": 1,
                                    "conditions": [
                                        {"type": "Ready", "status": "False"}
                                    ],
                                },
                            }
                        ]
                    }
                ),
                "",
            )

        payload = status_payload(ROOT, "cluster", runner=runner)
        self.assertEqual(payload["status"], "incomplete")
        self.assertTrue(all(p["status"] == "failed" for p in payload["probes"]))

    def test_running_pod_requires_readiness_and_completed_jobs_are_quiet(self) -> None:
        pods = {
            "items": [
                {
                    "metadata": {"name": "renderer", "namespace": "led-matrix"},
                    "status": {
                        "phase": "Running",
                        "conditions": [{"type": "Ready", "status": "False"}],
                    },
                },
                {"metadata": {"name": "backup"}, "status": {"phase": "Succeeded"}},
            ]
        }
        observed = cluster_observation("workloads", json.dumps(pods))
        self.assertEqual(observed["unhealthy_count"], 1)
        self.assertIn("led-matrix/renderer", observed["detail"])

    def test_stale_flux_readiness_is_unhealthy(self) -> None:
        flux = {
            "items": [
                {
                    "metadata": {"name": "apps", "generation": 2},
                    "status": {
                        "observedGeneration": 1,
                        "conditions": [{"type": "Ready", "status": "True"}],
                    },
                }
            ]
        }
        self.assertEqual(
            cluster_observation("flux", json.dumps(flux))["status"], "failed"
        )

    def test_cordoned_node_is_not_healthy(self) -> None:
        nodes = {
            "items": [
                {
                    "metadata": {"name": "worker"},
                    "spec": {"unschedulable": True},
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                }
            ]
        }
        self.assertEqual(
            cluster_observation("nodes", json.dumps(nodes))["status"], "failed"
        )

    def test_empty_and_malformed_observations_fail_closed(self) -> None:
        for name in ("nodes", "flux", "workloads"):
            for output in ("not-json", "{}", '{"items":null}', '{"items":[null]}'):
                with self.subTest(name=name, output=output):
                    self.assertEqual(
                        cluster_observation(name, output)["status"], "failed"
                    )
        self.assertEqual(
            cluster_observation("nodes", '{"items":[]}')["status"], "failed"
        )
        self.assertEqual(
            cluster_observation("workloads", '{"items":[]}')["status"], "pass"
        )

    def test_large_resource_list_is_parsed_before_output_truncation(self) -> None:
        def runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            items = [
                {
                    "metadata": {"name": "x" * 100, "generation": 1},
                    "status": {
                        "phase": "Running",
                        "observedGeneration": 1,
                        "conditions": [{"type": "Ready", "status": "True"}],
                    },
                }
            ] * 100
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"items": items}), ""
            )

        payload = status_payload(ROOT, "cluster", runner=runner)
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["probes"][0]["resource_count"], 100)

    def test_evidence_is_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_evidence(
                Path(directory), "network", "router", "failed", "token=secret"
            )
            content = path.read_text()
        self.assertNotIn("secret", content)
        self.assertIn("[REDACTED]", content)

    def test_network_includes_worker_result_within_remaining_budget(self) -> None:
        calls = []

        def runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            calls.append((argv, kwargs["timeout"]))
            output = (
                json.dumps({"status": "pass", "detail": "BGP diagnostics passed"})
                if "scripts.agent.opnsense_status" in argv
                else "ping passed"
            )
            return subprocess.CompletedProcess(argv, 0, output, "")

        payload = status_payload(ROOT, "network", runner=runner)
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["probes"][-1]["name"], "bgp")
        self.assertEqual(payload["probes"][-1]["detail"], "BGP diagnostics passed")
        self.assertIn("scripts.agent.opnsense_status", calls[-1][0])
        self.assertGreater(calls[-1][1], 0)
        self.assertLessEqual(calls[-1][1], 30)

    def test_unavailable_credentials_prevent_network_success(self) -> None:
        def runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            if "scripts.agent.opnsense_status" in argv:
                return subprocess.CompletedProcess(
                    argv,
                    1,
                    json.dumps(
                        {"status": "unavailable", "detail": "credentials unavailable"}
                    ),
                    "",
                )
            return subprocess.CompletedProcess(argv, 0, "ok", "")

        payload = status_payload(ROOT, "network", runner=runner)
        self.assertEqual(payload["status"], "incomplete")
        self.assertEqual(payload["probes"][-1]["status"], "unavailable")

    def test_network_does_not_start_worker_after_total_timeout(self) -> None:
        calls = []
        payload = status_payload(
            ROOT,
            "network",
            total_timeout=0,
            runner=lambda *args, **kwargs: calls.append(args),
        )
        self.assertEqual(calls, [])
        self.assertEqual(payload["status"], "incomplete")
        self.assertEqual(payload["probes"][-1]["detail"], "total timeout")

    def test_worker_timeout_prevents_network_success(self) -> None:
        def runner(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            if "scripts.agent.opnsense_status" in argv:
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            return subprocess.CompletedProcess(argv, 0, "ok", "")

        payload = status_payload(ROOT, "network", runner=runner)
        self.assertEqual(payload["status"], "incomplete")
        self.assertEqual(payload["probes"][-1]["status"], "failed")
        self.assertEqual(payload["probes"][-1]["detail"], "timeout")


if __name__ == "__main__":
    unittest.main()
