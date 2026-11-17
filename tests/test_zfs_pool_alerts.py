from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "gitops/observability/kube-prometheus-stack/cluster-rules.yaml"
GB = 10**9


class ZfsPoolSpaceAlertTests(unittest.TestCase):
    """Pool usage comes from dataset filesystems; zpool gauges are not exported."""

    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("promtool") is None:
            raise unittest.SkipTest("promtool is required for behavioral PromQL cases")
        rules = yaml.safe_load(RULES.read_text())
        group = next(g for g in rules["spec"]["groups"] if g["name"] == "cluster.rules")
        names = {"zpool:used:percent", "ZfsPoolSpaceWarning", "ZfsPoolSpaceCritical"}
        cls.rules = [
            {k: v for k, v in r.items() if k != "annotations"}
            for r in group["rules"]
            if r.get("record", r.get("alert")) in names
        ]
        assert len(cls.rules) == 3

    def check_case(
        self,
        used_gb: dict[str, int],
        avail_gb: int,
        warning: bool,
        critical: bool,
        mounts: dict[str, int] | None = None,
    ) -> None:
        series = []
        for device, used in {**used_gb, "tank/media@daily": 9_999}.items():
            for mount in range((mounts or {}).get(device, 1)):
                series += self.dataset_series(device, mount, used, avail_gb)
        self.run_promtool(series, warning, critical)

    @staticmethod
    def dataset_series(
        device: str, mount: int, used: int, avail_gb: int
    ) -> list[dict[str, str]]:
        labels = (
            f'job="nas-01-node",fstype="zfs",device="{device}",'
            f'mountpoint="/{device}/{mount}"'
        )
        size = (used + avail_gb) * GB
        return [
            {
                "series": f"node_filesystem_size_bytes{{{labels}}}",
                "values": f"{size}+0x30",
            },
            {
                "series": f"node_filesystem_avail_bytes{{{labels}}}",
                "values": f"{avail_gb * GB}+0x30",
            },
        ]

    def run_promtool(
        self, series: list[dict[str, str]], warning: bool, critical: bool
    ) -> None:
        expected = {"zpool": "tank"}
        cases = [
            {
                "eval_time": "20m",
                "alertname": name,
                "exp_alerts": (
                    [{"exp_labels": {**expected, "severity": severity}}]
                    if fires
                    else []
                ),
            }
            for name, severity, fires in (
                ("ZfsPoolSpaceWarning", "warning", warning),
                ("ZfsPoolSpaceCritical", "critical", critical),
            )
        ]
        with tempfile.TemporaryDirectory(prefix="zpool-promtool-") as directory:
            tmp = Path(directory)
            (tmp / "rules.yaml").write_text(
                yaml.safe_dump({"groups": [{"name": "zfs", "rules": self.rules}]})
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
                                "alert_rule_test": cases,
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

    def test_nearly_full_pool_is_critical(self) -> None:
        # 2026-11-15: 8.27 TB of datasets, 124 GB free.
        self.check_case(
            {"tank/media": 7_650, "tank/cluster": 500, "tank/registry": 120},
            avail_gb=124,
            warning=True,
            critical=True,
        )

    def test_warning_between_thresholds(self) -> None:
        self.check_case({"tank/media": 850}, avail_gb=150, warning=True, critical=False)

    def test_snapshot_mounts_do_not_count_as_usage(self) -> None:
        self.check_case(
            {"tank/media": 700}, avail_gb=300, warning=False, critical=False
        )

    def test_bind_mounted_dataset_counts_once(self) -> None:
        # 2026-11-16 zroot: persist is bind-mounted 12 times. Counted once it
        # is 77.5% used; summing every mount reported 94% and paged critical.
        self.check_case(
            {
                "tank/persist": 60,
                "tank/attic": 70,
                "tank/nix-cache": 35,
                "tank/nix": 9,
                "tank/log": 3,
            },
            avail_gb=51,
            warning=False,
            critical=False,
            mounts={"tank/persist": 12, "tank/nix": 2},
        )


if __name__ == "__main__":
    unittest.main()
