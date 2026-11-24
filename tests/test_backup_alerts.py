from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

import yaml

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "gitops/observability/kube-prometheus-stack/cluster-rules.yaml"


class BackupAlertTests(unittest.TestCase):
    """Behavior of external backup alerts under promtool."""

    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("promtool") is None:
            raise unittest.SkipTest("promtool is required for behavioral PromQL cases")
        groups = yaml.safe_load(RULES.read_text())["spec"]["groups"]
        cluster_group = next(g for g in groups if g["name"] == "cluster.rules")
        backup_alerts = {
            "ExternalBackupFailed",
            "ExternalBackupStale",
            "ExternalBackupMissing",
        }
        rules = [r for r in cluster_group["rules"] if r.get("alert") in backup_alerts]
        for rule in rules:
            rule.pop("annotations", None)
        cls.group = {"name": "backup.rules", "rules": rules}

    def run_case(
        self,
        series: list[dict],
        eval_time: str,
        expected: dict[str, list[dict[str, str]]],
    ) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="backup-promtool-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "rules.yaml").write_text(yaml.safe_dump({"groups": [self.group]}))
        tests = [
            {
                "eval_time": eval_time,
                "alertname": alertname,
                "exp_alerts": [{"exp_labels": labels} for labels in alert_instances],
            }
            for alertname, alert_instances in expected.items()
        ]
        (tmp / "test.yaml").write_text(
            yaml.safe_dump(
                {
                    "rule_files": ["rules.yaml"],
                    "evaluation_interval": "1m",
                    "tests": [
                        {
                            "interval": "1m",
                            "input_series": series,
                            "alert_rule_test": tests,
                        }
                    ],
                }
            )
        )
        result = subprocess.run(
            ["promtool", "test", "rules", str(tmp / "test.yaml")],
            capture_output=True,
            text=True,
            cwd=tmp,
            timeout=120,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_recent_successful_backup_is_quiet(self) -> None:
        # Eval time is at 2h (7200s). Last success was at 3600s (1 hour ago, fresh). Status = 1.
        series = [
            {
                "series": 'immich_backup_last_status{job="adguard-netbird-01-node"}',
                "values": "1+0x180",
            },
            {
                "series": 'immich_backup_last_success_timestamp_seconds{job="adguard-netbird-01-node"}',
                "values": "3600+0x180",
            },
        ]
        self.run_case(
            series,
            "2h",
            {
                "ExternalBackupFailed": [],
                "ExternalBackupStale": [],
                "ExternalBackupMissing": [],
            },
        )

    def test_failed_backup_fires_alert(self) -> None:
        series = [
            {
                "series": 'immich_backup_last_status{job="adguard-netbird-01-node"}',
                "values": "0+0x180",
            },
            {
                "series": 'immich_backup_last_success_timestamp_seconds{job="adguard-netbird-01-node"}',
                "values": "3600+0x180",
            },
        ]
        self.run_case(
            series,
            "16m",
            {
                "ExternalBackupFailed": [
                    {"job": "adguard-netbird-01-node", "severity": "critical"}
                ],
                "ExternalBackupStale": [],
                "ExternalBackupMissing": [],
            },
        )

    def test_stale_backup_fires_alert(self) -> None:
        # 129600s = 36 hours. If last success was at 0s, at 37h (133200s) it's stale (> 36h).
        # eval_time is 37h.
        series = [
            {
                "series": 'immich_backup_last_status{job="adguard-netbird-01-node"}',
                "values": "1+0x3000",
            },
            {
                "series": 'immich_backup_last_success_timestamp_seconds{job="adguard-netbird-01-node"}',
                "values": "0+0x3000",
            },
        ]
        self.run_case(
            series,
            "37h",
            {
                "ExternalBackupFailed": [],
                "ExternalBackupStale": [
                    {"job": "adguard-netbird-01-node", "severity": "critical"}
                ],
                "ExternalBackupMissing": [],
            },
        )

    def test_missing_metric_fires_alert(self) -> None:
        # No immich_backup_last_success_timestamp_seconds series at all
        series = [
            {
                "series": 'immich_backup_last_status{job="adguard-netbird-01-node"}',
                "values": "1+0x180",
            },
        ]
        self.run_case(
            series,
            "16m",
            {
                "ExternalBackupFailed": [],
                "ExternalBackupStale": [],
                "ExternalBackupMissing": [
                    {"job": "adguard-netbird-01-node", "severity": "critical"}
                ],
            },
        )


class OffsiteBackupAlertTests(unittest.TestCase):
    """The weekly offsite copy tolerates a missed week and a desktop left off."""

    run_case = BackupAlertTests.run_case
    JOB: ClassVar[dict[str, str]] = {
        "job": "adguard-netbird-01-node",
        "severity": "warning",
    }
    SUCCESS = (
        'offsite_backup_last_success_timestamp_seconds{job="adguard-netbird-01-node"}'
    )

    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("promtool") is None:
            raise unittest.SkipTest("promtool is required for behavioral PromQL cases")
        groups = yaml.safe_load(RULES.read_text())["spec"]["groups"]
        cluster_group = next(g for g in groups if g["name"] == "cluster.rules")
        alerts = {"OffsiteBackupStale", "OffsiteBackupMissing"}
        rules = [r for r in cluster_group["rules"] if r.get("alert") in alerts]
        for rule in rules:
            rule.pop("annotations", None)
        cls.group = {"name": "offsite.rules", "rules": rules}

    def test_one_missed_week_is_quiet(self) -> None:
        series = [{"series": self.SUCCESS, "values": "0+0x16000"}]
        self.run_case(
            series, "8d", {"OffsiteBackupStale": [], "OffsiteBackupMissing": []}
        )

    def test_nine_days_without_a_copy_warns(self) -> None:
        series = [{"series": self.SUCCESS, "values": "0+0x16000"}]
        self.run_case(
            series,
            "218h",
            {"OffsiteBackupStale": [self.JOB], "OffsiteBackupMissing": []},
        )

    def test_first_copy_has_ten_days_before_warning(self) -> None:
        series = [
            {
                "series": 'offsite_backup_last_status{job="adguard-netbird-01-node"}',
                "values": "0+0x16000",
            }
        ]
        self.run_case(
            series, "9d", {"OffsiteBackupStale": [], "OffsiteBackupMissing": []}
        )
        self.run_case(
            series,
            "241h",
            {
                "OffsiteBackupStale": [],
                "OffsiteBackupMissing": [self.JOB],
            },
        )


if __name__ == "__main__":
    unittest.main()
