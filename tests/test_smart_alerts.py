from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "gitops/observability/kube-prometheus-stack/cluster-rules.yaml"


class SmartAlertTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        data = yaml.safe_load(RULES.read_text())
        cls.alerts = {}
        for group in data.get("spec", {}).get("groups", []):
            for rule in group.get("rules", []):
                if "alert" in rule:
                    cls.alerts[rule["alert"]] = rule

    def test_temperature_alerts_filter_by_current_temperature(self) -> None:
        high = self.alerts["SmartDeviceTemperatureHigh"]
        self.assertEqual(high["labels"]["severity"], "warning")
        self.assertIn(
            'smartctl_device_temperature{temperature_type="current"} > 60', high["expr"]
        )

        critical = self.alerts["SmartDeviceTemperatureCritical"]
        self.assertEqual(critical["labels"]["severity"], "critical")
        self.assertIn(
            'smartctl_device_temperature{temperature_type="current"} > 70',
            critical["expr"],
        )
