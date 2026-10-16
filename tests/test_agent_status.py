import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.agent.evidence import write_evidence
from scripts.agent.status import configured_targets, status_payload

ROOT = Path(__file__).resolve().parents[1]


class AgentStatusTest(unittest.TestCase):
    def test_discovers_configured_targets(self) -> None:
        targets = configured_targets(ROOT)
        self.assertEqual(targets["cluster_api"], "https://10.0.30.11:6443")
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

    def test_cluster_probe_reports_each_read_only_observation(self) -> None:
        def successful(
            *args: object, **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            del args, kwargs
            return subprocess.CompletedProcess([], 0, "ok", "")

        payload = status_payload(ROOT, "cluster", runner=successful)
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(
            [probe["name"] for probe in payload["probes"]],
            ["nodes", "flux", "workloads"],
        )

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
