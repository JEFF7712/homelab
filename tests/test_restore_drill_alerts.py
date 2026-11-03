from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "gitops/observability/kube-prometheus-stack/cluster-rules.yaml"
DAY = 86400


class RestoreDrillAlertTests(unittest.TestCase):
    """Behavior of the platform restore drill alerts under promtool."""

    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("promtool") is None:
            raise unittest.SkipTest("promtool is required for behavioral PromQL cases")
        groups = yaml.safe_load(RULES.read_text())["spec"]["groups"]
        group = next(g for g in groups if g["name"] == "restore-drill.rules")
        # Expressions are under test; annotations are templated prose.
        for rule in group["rules"]:
            rule.pop("annotations", None)
        cls.group = group

    def run_case(
        self,
        series: list[dict],
        eval_time: str,
        expected: dict[str, list[str]],
    ) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="restore-drill-promtool-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "rules.yaml").write_text(yaml.safe_dump({"groups": [self.group]}))
        tests = [
            {
                "eval_time": eval_time,
                "alertname": alertname,
                "exp_alerts": [
                    {"exp_labels": {"dataset": dataset, "severity": "critical"}}
                    for dataset in datasets
                ],
            }
            for alertname, datasets in expected.items()
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

    def drill(self, dataset: str, passed: int, result: int) -> list[dict]:
        return [
            {
                "series": f'homelab_restore_drill_last_success_timestamp_seconds{{dataset="{dataset}"}}',
                "values": f"{passed}+0x200",
            },
            {
                "series": f'homelab_restore_drill_last_result{{dataset="{dataset}"}}',
                "values": f"{result}+0x200",
            },
        ]

    def test_recent_passes_are_quiet(self) -> None:
        series = self.drill("platform-nas-01", 0, 1) + self.drill(
            "platform-homelab-04", 0, 1
        )
        self.run_case(
            series,
            "3h",
            {
                "RestoreDrillFailed": [],
                "RestoreDrillStale": [],
                "RestoreDrillMissing": [],
            },
        )

    def test_a_failed_drill_pages_for_that_dataset(self) -> None:
        series = self.drill("platform-nas-01", 0, 1) + self.drill(
            "platform-homelab-04", 0, 0
        )
        self.run_case(series, "10m", {"RestoreDrillFailed": ["platform-homelab-04"]})

    def test_no_pass_in_eight_days_pages(self) -> None:
        series = self.drill("platform-nas-01", -9 * DAY, 1)
        self.run_case(
            series,
            "2h",
            {"RestoreDrillStale": ["platform-nas-01"], "RestoreDrillFailed": []},
        )

    def test_missing_evidence_pages_after_six_hours(self) -> None:
        series = [{"series": 'up{job="nas-01-node"}', "values": "1+0x500"}]
        self.run_case(series, "5h", {"RestoreDrillMissing": []})
        self.run_case(series, "7h", {"RestoreDrillMissing": ["platform-nas-01"]})


if __name__ == "__main__":
    unittest.main()
