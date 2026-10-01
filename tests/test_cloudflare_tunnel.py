"""Tunnel ingress parity: gitops/cloudflare/ingress-config.yaml must match
docs/runbooks/cloudflare-tunnel.md order, and every origin Service must
exist (raw manifest or chart-created)."""

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

CHART_CREATED = {
    ("ntfy-ntfy", "observability"),
    ("kube-prometheus-stack-grafana", "observability"),
}


def manifest_services() -> set:
    found = set()
    for f in (ROOT / "gitops").rglob("*.yaml"):
        for doc in f.read_text(encoding="utf-8").split("\n---"):
            if re.search(r"^kind: Service$", doc, re.MULTILINE):
                name = re.search(r"^  name: (\S+)", doc, re.MULTILINE)
                ns = re.search(r"^  namespace: (\S+)", doc, re.MULTILINE)
                if name and ns:
                    found.add((name.group(1), ns.group(1)))
    return found


def config_ingress() -> list:
    cm = yaml.safe_load(
        (ROOT / "gitops" / "cloudflare" / "ingress-config.yaml").read_text(
            encoding="utf-8"
        )
    )
    raw = cm["data"]["config.yaml"]
    cfg = yaml.safe_load(raw)
    assert cfg["tunnel"] == "0f08d8c5-6f2c-409e-ba80-dc0601e0227e"
    return cfg["ingress"]


def runbook_ingress() -> list:
    text = (ROOT / "docs" / "runbooks" / "cloudflare-tunnel.md").read_text(
        encoding="utf-8"
    )
    text = text.split("Removed 2026-09-15", 1)[0]
    entries = []
    for m in re.finditer(r"`([^`\s]+) -> ([^`]+)`", text):
        entries.append((m.group(1), m.group(2)))
    return entries


class TestTunnelIngressParity(unittest.TestCase):
    def test_config_matches_runbook_order(self) -> None:
        cfg = config_ingress()
        self.assertEqual(cfg[-1], {"service": "http_status:404"})
        got = [(r["hostname"], r["service"]) for r in cfg[:-1] if "hostname" in r]
        self.assertEqual(got, runbook_ingress())

    def test_no_duplicate_hostnames(self) -> None:
        routes = [
            (r["hostname"], r.get("path")) for r in config_ingress() if "hostname" in r
        ]
        self.assertEqual(len(routes), len(set(routes)))

    def test_every_origin_service_exists(self) -> None:
        live = manifest_services() | CHART_CREATED
        for rule in config_ingress():
            if "hostname" not in rule:
                continue
            host = re.sub(r"^https?://", "", rule["service"])
            parts = re.split(r"[.:]", host)
            self.assertGreaterEqual(len(parts), 2, rule["hostname"])
            self.assertIn((parts[0], parts[1]), live, rule["hostname"])


if __name__ == "__main__":
    unittest.main()
