from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class CronJobAlertTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("promtool") is None:
            raise unittest.SkipTest("promtool is required for behavioral PromQL cases")
        rules = yaml.safe_load(
            (
                ROOT / "gitops/observability/kube-prometheus-stack/cluster-rules.yaml"
            ).read_text()
        )
        group = next(g for g in rules["spec"]["groups"] if g["name"] == "jobs.rules")
        cls.rule = next(
            r for r in group["rules"] if r["alert"] == "CronJobLastRunFailed"
        )
        cls.rule.pop("annotations", None)

    def check_case(
        self,
        *,
        success: int | None = None,
        active: int = 0,
        suspended: int = 0,
        schedule: int = 0,
        alert: bool = True,
        eval_time: str = "1h",
    ) -> None:
        values = {
            "kube_cronjob_status_last_schedule_time": schedule,
            "kube_cronjob_status_active": active,
            "kube_cronjob_spec_suspend": suspended,
        }
        if success is not None:
            values["kube_cronjob_status_last_successful_time"] = success
        labels = {"namespace": "backups", "cronjob": "new-backup"}
        label_text = 'namespace="backups",cronjob="new-backup"'
        series = [
            {"series": f"{metric}{{{label_text}}}", "values": f"{value}+0x120"}
            for metric, value in values.items()
        ]
        with tempfile.TemporaryDirectory(prefix="cronjob-promtool-") as directory:
            tmp = Path(directory)
            (tmp / "rules.yaml").write_text(
                yaml.safe_dump(
                    {
                        "groups": [{"name": "jobs", "rules": [self.rule]}],
                    }
                )
            )
            expected = (
                [{"exp_labels": {**labels, "severity": "warning"}}] if alert else []
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
                                "alert_rule_test": [
                                    {
                                        "eval_time": eval_time,
                                        "alertname": "CronJobLastRunFailed",
                                        "exp_alerts": expected,
                                    }
                                ],
                            }
                        ],
                    }
                )
            )
            result = subprocess.run(
                ["promtool", "test", "rules", str(tmp / "test.yaml")],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_never_successful_job_alerts(self) -> None:
        self.check_case()

    def test_latest_failed_run_alerts(self) -> None:
        self.check_case(success=-3600)

    def test_active_job_is_quiet(self) -> None:
        self.check_case(active=1, alert=False)

    def test_suspended_job_is_quiet(self) -> None:
        self.check_case(suspended=1, alert=False)

    def test_recent_run_gets_completion_grace(self) -> None:
        self.check_case(schedule=2700, alert=False)

    def test_success_after_latest_schedule_is_quiet(self) -> None:
        self.check_case(success=60, alert=False)

    def test_failure_must_persist_after_grace(self) -> None:
        self.check_case(eval_time="40m", alert=False)
