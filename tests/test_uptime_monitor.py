from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ENDPOINTS = ROOT / "config/uptime/external-endpoints.json"
INGRESS = ROOT / "gitops/cloudflare/ingress-config.yaml"
RULES = ROOT / "gitops/observability/kube-prometheus-stack/cluster-rules.yaml"
JOB = 'job="home-pc-01-uptime"'


def ingress_hostnames() -> set[str]:
    # The cloudflared ConfigMap embeds its config as a YAML string.
    config = yaml.safe_load(yaml.safe_load(INGRESS.read_text())["data"]["config.yaml"])
    rules = config["ingress"]
    assert rules, "no ingress rules found"
    return {rule["hostname"] for rule in rules if rule.get("hostname")}


def monitored() -> list[str]:
    data = json.loads(ENDPOINTS.read_text())
    return [host for hosts in data["groups"].values() for host in hosts]


class EndpointCoverageTest(unittest.TestCase):
    def test_every_public_hostname_is_probed_or_excluded(self) -> None:
        data = json.loads(ENDPOINTS.read_text())
        covered = set(monitored()) | set(data["excluded"])
        missing = ingress_hostnames() - covered
        self.assertEqual(missing, set(), "new public hostname without a probe")

    def test_no_stale_or_duplicate_probes(self) -> None:
        hosts = monitored()
        self.assertEqual(len(hosts), len(set(hosts)))
        self.assertEqual(set(hosts) - ingress_hostnames(), set())
        excluded = set(json.loads(ENDPOINTS.read_text())["excluded"])
        self.assertEqual(excluded & set(hosts), set())


class UptimeAlertTest(unittest.TestCase):
    """Behaviour of the outside-in uptime alerts under promtool."""

    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("promtool") is None:
            raise unittest.SkipTest("promtool is required for behavioral PromQL cases")
        groups = yaml.safe_load(RULES.read_text())["spec"]["groups"]
        group = next(g for g in groups if g["name"] == "uptime.rules")
        rules = [r for r in group["rules"] if "alert" in r]
        for rule in rules:
            rule.pop("annotations", None)
        cls.group = {"name": "uptime.rules", "rules": rules}

    def run_case(self, series: list[dict], eval_time: str, expected: dict) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="uptime-promtool-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "rules.yaml").write_text(yaml.safe_dump({"groups": [self.group]}))
        tests = [
            {
                "eval_time": eval_time,
                "alertname": name,
                "exp_alerts": [{"exp_labels": labels} for labels in alerts],
            }
            for name, alerts in expected.items()
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

    @staticmethod
    def endpoint(name: str, values: str) -> dict:
        return {
            "series": (
                f'gatus_results_endpoint_success{{{JOB},name="{name}",group="sites"}}'
            ),
            "values": values,
        }

    def test_one_site_down_alerts_for_that_site(self) -> None:
        series = [
            self.endpoint("a.example", "0+0x30"),
            self.endpoint("b.example", "1+0x30"),
            self.endpoint("c.example", "1+0x30"),
        ]
        self.run_case(
            series,
            "15m",
            {
                "ExternalEndpointDown": [
                    {
                        "job": "home-pc-01-uptime",
                        "name": "a.example",
                        "group": "sites",
                        "severity": "warning",
                    }
                ],
                "ExternalHomelabUnreachable": [],
            },
        )

    def test_most_sites_down_is_one_uplink_alert(self) -> None:
        series = [
            self.endpoint("a.example", "0+0x30"),
            self.endpoint("b.example", "0+0x30"),
            self.endpoint("c.example", "1+0x30"),
        ]
        self.run_case(
            series,
            "15m",
            {
                "ExternalEndpointDown": [],
                "ExternalHomelabUnreachable": [{"severity": "critical"}],
            },
        )

    def test_silent_probe_warns_after_an_hour(self) -> None:
        series = [{"series": f"up{{{JOB}}}", "values": "0+0x120"}]
        self.run_case(series, "50m", {"ExternalUptimeMonitorDown": []})
        self.run_case(
            series,
            "70m",
            {
                "ExternalUptimeMonitorDown": [
                    {"job": "home-pc-01-uptime", "severity": "warning"}
                ]
            },
        )


@unittest.skipUnless(
    shutil.which("nix"), "nix is required for configuration evaluation"
)
class UptimeWiringTest(unittest.TestCase):
    def evaluate(self, attribute: str, apply: str) -> dict:
        return json.loads(
            subprocess.run(
                [
                    "nix",
                    "eval",
                    "--json",
                    f"path:{ROOT}?dir=flake#nixosConfigurations.{attribute}",
                    "--apply",
                    apply,
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )

    def test_pc_probes_every_listed_host_and_serves_only_netbird(self) -> None:
        pc = self.evaluate(
            "home-pc-01.config",
            "c: { inherit (c.services.gatus.settings) endpoints metrics;"
            " port = c.services.gatus.settings.web.port;"
            " open = c.services.gatus.openFirewall;"
            " wt0 = c.networking.firewall.interfaces.wt0.allowedTCPPorts; }",
        )
        self.assertTrue(pc["metrics"])
        self.assertFalse(pc["open"])
        self.assertIn(pc["port"], pc["wt0"])
        self.assertEqual(
            sorted(e["url"] for e in pc["endpoints"]),
            sorted(f"https://{host}/" for host in monitored()),
        )

    def test_relay_admits_only_the_cluster_network(self) -> None:
        relay = self.evaluate(
            "adguard-netbird-01.config",
            "c: { socket = c.systemd.sockets.netbird-relay-home-pc-01-uptime"
            ".listenStreams; input = c.networking.firewall.extraInputRules; }",
        )
        self.assertEqual(relay["socket"], ["10.0.30.10:2202"])
        rules = [
            line.strip() for line in relay["input"].splitlines() if "dport 2202" in line
        ]
        self.assertEqual(rules, ["ip saddr { 10.0.30.0/24 } tcp dport 2202 accept"])


if __name__ == "__main__":
    unittest.main()
