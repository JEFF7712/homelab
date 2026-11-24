from __future__ import annotations

import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

ROOT = Path(__file__).resolve().parents[1]
ALERTMANAGER = ROOT / "gitops/observability/kube-prometheus-stack/release.yaml"
NTFY = ROOT / "gitops/observability/ntfy/release.yaml"
TEMPLATES = ROOT / "gitops/observability/ntfy/templates.yaml"
KUSTOMIZATION = ROOT / "gitops/observability/kustomization.yaml"


def helm_release(path: Path) -> dict:
    return next(
        doc
        for doc in yaml.safe_load_all(path.read_text())
        if doc and doc["kind"] == "HelmRelease"
    )


class AlertDeliveryTests(unittest.TestCase):
    """ntfy's built-in alertmanager template overflowed its 4 KiB limit on
    groups of four or more alerts, and the webhook's 400 dropped them
    (624 failed deliveries in the week to 2026-11-24)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.alertmanager = helm_release(ALERTMANAGER)["spec"]["values"]["alertmanager"]
        cls.ntfy = helm_release(NTFY)["spec"]["values"]
        cls.templates = yaml.safe_load(TEMPLATES.read_text())

    def test_every_ntfy_receiver_uses_a_shipped_template(self) -> None:
        shipped = {name.removesuffix(".yml") for name in self.templates["data"]}
        urls = [
            hook["url"]
            for receiver in self.alertmanager["config"]["receivers"]
            for hook in receiver.get("webhook_configs", [])
            if "ntfy" in hook["url"]
        ]
        self.assertTrue(urls)
        for url in urls:
            with self.subTest(url=url):
                self.assertIn(parse_qs(urlparse(url).query)["template"][0], shipped)

    def test_ntfy_reads_the_template_configmap(self) -> None:
        self.assertIn("ntfy/templates.yaml", KUSTOMIZATION.read_text())
        mounts = {m["name"]: m["mountPath"] for m in self.ntfy["extraVolumeMounts"]}
        volumes = {
            v["name"]: v.get("configMap", {}).get("name")
            for v in self.ntfy["extraVolumes"]
        }
        name = next(
            n for n, cm in volumes.items() if cm == self.templates["metadata"]["name"]
        )
        config = yaml.safe_load(self.ntfy["ntfy"]["extraConfig"])
        self.assertEqual(config["template-dir"], mounts[name])

    def test_template_caps_the_listed_alerts(self) -> None:
        for name, body in self.templates["data"].items():
            with self.subTest(template=name):
                template = yaml.safe_load(body)
                self.assertEqual(set(template), {"title", "message"})
                self.assertIn("lt $i 8", template["message"])

    def test_workload_alerts_group_per_namespace(self) -> None:
        routes = self.alertmanager["config"]["route"]["routes"]
        workload = next(r for r in routes if "KubePodNotReady" in r["matchers"][0])
        self.assertEqual(workload["group_by"], ["namespace"])
        critical = next(r for r in routes if r["matchers"] == ['severity = "critical"'])
        self.assertLess(routes.index(critical), routes.index(workload))


if __name__ == "__main__":
    unittest.main()
