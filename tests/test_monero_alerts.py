from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

import yaml

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "gitops/observability/kube-prometheus-stack/monero-rules.yaml"
RELEASE = ROOT / "gitops/observability/kube-prometheus-stack/release.yaml"
KUSTOMIZATION = ROOT / "gitops/observability/kustomization.yaml"


class MoneroAlertTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = yaml.safe_load(RULES.read_text())
        cls.release = yaml.safe_load(RELEASE.read_text())

    def _alerts(self) -> dict:
        alerts = {}
        for group in self.rules["spec"]["groups"]:
            for rule in group["rules"]:
                if "alert" in rule:
                    alerts[rule["alert"]] = rule
        return alerts

    def test_persist_warning_fires_at_eighty_percent(self) -> None:
        rule = self._alerts()["MoneroPersistSpaceWarning"]
        self.assertEqual(rule["labels"]["severity"], "warning")
        self.assertIn('mountpoint="/persist"', rule["expr"])
        self.assertIn('job="homelab-04-node"', rule["expr"])
        self.assertIn("0.80", rule["expr"])

    def test_persist_critical_and_unit_failure_page(self) -> None:
        alerts = self._alerts()
        critical = alerts["MoneroPersistSpaceCritical"]
        self.assertEqual(critical["labels"]["severity"], "critical")
        self.assertIn("0.90", critical["expr"])
        failed = alerts["MoneroServiceFailed"]
        self.assertEqual(failed["labels"]["severity"], "critical")
        self.assertIn('name="monero.service"', failed["expr"])
        # A missing series (collector gap) must page, not stay silent.
        self.assertIn("absent(", failed["expr"])

    def test_scrape_and_routing_wire_alerts_to_ntfy(self) -> None:
        scrape_configs = self.release["spec"]["values"]["prometheus"]["prometheusSpec"][
            "additionalScrapeConfigs"
        ]
        monero_jobs = [
            job for job in scrape_configs if job["job_name"] == "homelab-04-node"
        ]
        self.assertEqual(len(monero_jobs), 1)
        targets = monero_jobs[0]["static_configs"][0]["targets"]
        self.assertIn("10.0.30.14:9101", targets)

        config = self.release["spec"]["values"]["alertmanager"]["config"]
        receivers = {receiver["name"] for receiver in config["receivers"]}
        self.assertIn("ntfy-default", receivers)
        self.assertIn("ntfy-critical", receivers)
        inhibits = config["inhibit_rules"]
        self.assertTrue(
            any(
                rule.get("source_matchers")
                == ['alertname = "MoneroPersistSpaceCritical"']
                and rule.get("target_matchers")
                == ['alertname = "MoneroPersistSpaceWarning"']
                for rule in inhibits
            )
        )

    def test_rules_file_is_reconciled(self) -> None:
        kustomization = yaml.safe_load(KUSTOMIZATION.read_text())
        self.assertIn(
            "kube-prometheus-stack/monero-rules.yaml",
            kustomization["resources"],
        )


class MoneroPromtoolTests(unittest.TestCase):
    """Behavioral PromQL cases run against the reconciled rules file."""

    POINTS = 30
    SIZE_GIB = 100
    FS_LABELS: ClassVar[dict] = {"job": "homelab-04-node", "mountpoint": "/persist"}
    UNIT_LABELS: ClassVar[dict] = {
        "job": "homelab-04-node",
        "name": "monero.service",
        "state": "active",
    }

    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("promtool") is None:
            raise unittest.SkipTest("promtool is required for behavioral PromQL cases")
        cls.groups = yaml.safe_load(RULES.read_text())["spec"]["groups"]

    def _rule(self, alertname: str) -> dict:
        for group in self.groups:
            for rule in group["rules"]:
                if rule.get("alert") == alertname:
                    return rule
        raise KeyError(alertname)

    def _run_case(self, name, avail_gib, unit_state, expectations) -> None:
        # Expectations are (alertname, labels) with None meaning silence.
        # Promtool compares full labels and annotations, so firing alerts
        # carry the complete series label set plus the rule's annotations.
        tmp = Path(tempfile.mkdtemp(prefix="monero-promtool-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "rules.yaml").write_text(yaml.safe_dump({"groups": self.groups}))
        series = [
            {
                "series": 'node_filesystem_avail_bytes{job="homelab-04-node",mountpoint="/persist"}',
                "values": f"{avail_gib}+0x{self.POINTS - 1}",
            },
            {
                "series": 'node_filesystem_size_bytes{job="homelab-04-node",mountpoint="/persist"}',
                "values": f"{self.SIZE_GIB}+0x{self.POINTS - 1}",
            },
        ]
        if unit_state is not None:
            series.append(
                {
                    "series": 'node_systemd_unit_state{job="homelab-04-node",name="monero.service",state="active"}',
                    "values": f"{unit_state}+0x{self.POINTS - 1}",
                }
            )
        alert_tests = []
        for alertname, labels in expectations:
            if labels is None:
                exp_alerts: list = []
            else:
                exp_alerts = [
                    {
                        "exp_labels": labels,
                        "exp_annotations": self._rule(alertname)["annotations"],
                    }
                ]
            alert_tests.append(
                {
                    "eval_time": "20m",
                    "alertname": alertname,
                    "exp_alerts": exp_alerts,
                }
            )
        (tmp / "test.yaml").write_text(
            yaml.safe_dump(
                {
                    "rule_files": ["rules.yaml"],
                    "evaluation_interval": "1m",
                    "tests": [
                        {
                            "interval": "1m",
                            "input_series": series,
                            "alert_rule_test": alert_tests,
                        }
                    ],
                }
            )
        )
        proc = subprocess.run(
            ["promtool", "test", "rules", str(tmp / "test.yaml")],
            capture_output=True,
            text=True,
            cwd=tmp,
            timeout=120,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"{name}:\n{proc.stdout}\n{proc.stderr}")

    def test_healthy_filesystem_and_unit_fire_nothing(self) -> None:
        self._run_case(
            "healthy",
            50,
            "1",
            [
                ("MoneroPersistSpaceWarning", None),
                ("MoneroPersistSpaceCritical", None),
                ("MoneroServiceFailed", None),
            ],
        )

    def test_warning_fires_at_81_percent_used(self) -> None:
        self._run_case(
            "warning",
            19,
            "1",
            [
                (
                    "MoneroPersistSpaceWarning",
                    {**self.FS_LABELS, "severity": "warning"},
                ),
                ("MoneroPersistSpaceCritical", None),
                ("MoneroServiceFailed", None),
            ],
        )

    def test_critical_fires_at_91_percent_used(self) -> None:
        self._run_case(
            "critical",
            9,
            "1",
            [
                (
                    "MoneroPersistSpaceWarning",
                    {**self.FS_LABELS, "severity": "warning"},
                ),
                (
                    "MoneroPersistSpaceCritical",
                    {**self.FS_LABELS, "severity": "critical"},
                ),
                ("MoneroServiceFailed", None),
            ],
        )

    def test_inactive_service_pages(self) -> None:
        self._run_case(
            "inactive",
            50,
            "0",
            [
                ("MoneroPersistSpaceWarning", None),
                ("MoneroPersistSpaceCritical", None),
                (
                    "MoneroServiceFailed",
                    {**self.UNIT_LABELS, "severity": "critical"},
                ),
            ],
        )

    def test_missing_collector_pages(self) -> None:
        # absent() preserves the selector labels on this Prometheus version,
        # so the page identifies the host and unit even with no series.
        self._run_case(
            "missing",
            50,
            None,
            [
                ("MoneroPersistSpaceWarning", None),
                ("MoneroPersistSpaceCritical", None),
                (
                    "MoneroServiceFailed",
                    {**self.UNIT_LABELS, "severity": "critical"},
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
