from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "gitops/observability/kube-prometheus-stack/etcd-rules.yaml"
RELEASE = ROOT / "gitops/observability/kube-prometheus-stack/release.yaml"
KUSTOMIZATION = ROOT / "gitops/observability/kustomization.yaml"

NODES = ("homelab-01", "homelab-02", "homelab-03")
SERVER_ADDRESSES = ("10.0.30.11", "10.0.30.12", "10.0.30.13")
# Upper bounds Prometheus keeps after the job's bucket drop.
KEPT_BUCKETS = ("0.008", "0.016", "0.032", "0.064", "0.128", "0.256", "0.512")
KEPT_BUCKETS += ("1.024", "2.048", "+Inf")
QUOTA = 2 * 1024**3


def alerts() -> dict[str, dict]:
    groups = yaml.safe_load(RULES.read_text())["spec"]["groups"]
    return {rule["alert"]: rule for group in groups for rule in group["rules"]}


def etcd_job() -> dict:
    release = yaml.safe_load(RELEASE.read_text())
    jobs = release["spec"]["values"]["prometheus"]["prometheusSpec"][
        "additionalScrapeConfigs"
    ]
    (job,) = [job for job in jobs if job["job_name"] == "etcd"]
    return job


def ingested(name: str, le: str | None = None) -> bool:
    """Apply the job's metric relabelling as Prometheus would (anchored)."""
    for step in etcd_job()["metric_relabel_configs"]:
        value = name if le is None else f"{name};{le}"
        if step["source_labels"] == ["__name__"]:
            value = name
        matched = re.fullmatch(step["regex"], value) is not None
        if step["action"] == "keep" and not matched:
            return False
        if step["action"] == "drop" and matched:
            return False
    return True


class EtcdScrapeTests(unittest.TestCase):
    def test_job_scrapes_each_server_with_its_node_label(self) -> None:
        targets = {
            config["labels"]["node"]: config["targets"]
            for config in etcd_job()["static_configs"]
        }
        self.assertEqual(
            targets,
            {
                node: [f"{address}:2381"]
                for node, address in zip(NODES, SERVER_ADDRESSES, strict=True)
            },
        )

    def test_every_metric_the_rules_use_is_ingested(self) -> None:
        used = set()
        for rule in alerts().values():
            used |= set(re.findall(r"\b(etcd_[a-z_]+)\b", rule["expr"]))
        self.assertTrue(used)
        for name in sorted(used):
            with self.subTest(metric=name):
                if name.endswith("_bucket"):
                    for le in KEPT_BUCKETS:
                        self.assertTrue(ingested(name, le), (name, le))
                else:
                    self.assertTrue(ingested(name), name)

    def test_unused_families_and_far_buckets_are_dropped(self) -> None:
        for name in (
            "grpc_server_handled_total",
            "etcd_server_apply_duration_seconds_bucket",
            "etcd_network_peer_round_trip_time_seconds_bucket",
            "go_goroutines",
        ):
            with self.subTest(metric=name):
                self.assertFalse(ingested(name))
        for le in ("0.001", "0.002", "0.004", "4.096", "8.192"):
            with self.subTest(le=le):
                self.assertFalse(
                    ingested("etcd_disk_wal_fsync_duration_seconds_bucket", le)
                )

    def test_rules_file_is_reconciled(self) -> None:
        resources = yaml.safe_load(KUSTOMIZATION.read_text())["resources"]
        self.assertIn("kube-prometheus-stack/etcd-rules.yaml", resources)

    def test_severities_follow_failure_tolerance(self) -> None:
        severities = {
            name: rule["labels"]["severity"] for name, rule in alerts().items()
        }
        self.assertEqual(
            severities,
            {
                "EtcdMemberDown": "critical",
                "EtcdQuorumAtRisk": "critical",
                "EtcdNoLeader": "critical",
                "EtcdFrequentLeaderChanges": "warning",
                "EtcdWalFsyncSlow": "warning",
                "EtcdWalFsyncCritical": "critical",
                "EtcdBackendCommitSlow": "warning",
                "EtcdDatabaseNearQuota": "warning",
                "EtcdDatabaseAtQuota": "critical",
            },
        )


def histogram(name: str, node: str, latency: float, per_minute: int = 300) -> list:
    """All observations at one latency: buckets at or above it count them."""
    series = []
    for le in KEPT_BUCKETS:
        bound = float("inf") if le == "+Inf" else float(le)
        step = per_minute if latency <= bound else 0
        series.append(
            {
                "series": f'{name}_bucket{{job="etcd",node="{node}",le="{le}"}}',
                "values": f"0+{step}x90",
            }
        )
    return series


class EtcdPromtoolTests(unittest.TestCase):
    """Behavioural PromQL cases; the healthy shape is the live 2026-11-15 p99."""

    ALL = tuple(alerts())

    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("promtool") is None:
            raise unittest.SkipTest("promtool is required for behavioral PromQL cases")
        cls.rules = alerts()
        # Annotations are templated on $value; the cases assert on firing
        # labels, so promtool runs the rules without them.
        cls.groups = yaml.safe_load(RULES.read_text())["spec"]["groups"]
        for group in cls.groups:
            for rule in group["rules"]:
                rule.pop("annotations", None)

    def scenario(
        self,
        *,
        up: dict[str, str] | None = None,
        leader: dict[str, str] | None = None,
        fsync: dict[str, float] | None = None,
        commit: dict[str, float] | None = None,
        db_fraction: dict[str, float] | None = None,
        leader_changes: dict[str, str] | None = None,
    ) -> list:
        series = []
        for node in NODES:
            labels = f'job="etcd",node="{node}"'
            series += [
                {"series": f"up{{{labels}}}", "values": (up or {}).get(node, "1x90")},
                {
                    "series": f"etcd_server_has_leader{{{labels}}}",
                    "values": (leader or {}).get(node, "1x90"),
                },
                {
                    "series": f"etcd_server_leader_changes_seen_total{{{labels}}}",
                    "values": (leader_changes or {}).get(node, "1x90"),
                },
                {
                    "series": f"etcd_server_quota_backend_bytes{{{labels}}}",
                    "values": f"{QUOTA}x90",
                },
                {
                    "series": f"etcd_mvcc_db_total_size_in_bytes{{{labels}}}",
                    "values": f"{int(QUOTA * (db_fraction or {}).get(node, 0.022))}x90",
                },
            ]
            series += histogram(
                "etcd_disk_wal_fsync_duration_seconds",
                node,
                (fsync or {}).get(node, 0.03),
            )
            series += histogram(
                "etcd_disk_backend_commit_duration_seconds",
                node,
                (commit or {}).get(node, 0.03),
            )
        return series

    def expect(self, series: list, firing: dict[str, list[str]]) -> None:
        """firing maps alertname -> nodes expected; every other alert is silent."""
        tests = []
        for alertname in self.ALL:
            nodes = firing.get(alertname, [])
            rule = self.rules[alertname]
            if alertname == "EtcdQuorumAtRisk":
                # A cluster-wide count carries no member labels.
                exp = [{"exp_labels": dict(rule["labels"])}] if nodes else []
            else:
                # Latency quantiles aggregate by node; the rest keep the job.
                job = {} if "histogram_quantile" in rule["expr"] else {"job": "etcd"}
                exp = [
                    {"exp_labels": {**job, "node": node, **rule["labels"]}}
                    for node in nodes
                ]
            tests.append(
                {"eval_time": "60m", "alertname": alertname, "exp_alerts": exp}
            )
        tmp = Path(tempfile.mkdtemp(prefix="etcd-promtool-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "rules.yaml").write_text(yaml.safe_dump({"groups": self.groups}))
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
        proc = subprocess.run(
            ["promtool", "test", "rules", str(tmp / "test.yaml")],
            capture_output=True,
            text=True,
            cwd=tmp,
            timeout=120,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_live_healthy_shape_fires_nothing(self) -> None:
        self.expect(self.scenario(), {})

    def test_rolling_deploy_leader_changes_stay_quiet(self) -> None:
        # Each restarted member resets its counter and sees up to three
        # changes during a three-server rolling deploy.
        self.expect(self.scenario(leader_changes={"homelab-02": "5x30 1 2 3 4x58"}), {})

    def test_unstable_leadership_warns(self) -> None:
        self.expect(
            self.scenario(leader_changes={"homelab-03": "1+1x90"}),
            {"EtcdFrequentLeaderChanges": ["homelab-03"]},
        )

    def test_one_member_down_pages_without_quorum_alert(self) -> None:
        self.expect(
            self.scenario(up={"homelab-02": "0x90"}),
            {"EtcdMemberDown": ["homelab-02"]},
        )

    def test_two_members_down_is_quorum_at_risk(self) -> None:
        self.expect(
            self.scenario(up={"homelab-02": "0x90", "homelab-03": "0x90"}),
            {
                "EtcdMemberDown": ["homelab-02", "homelab-03"],
                "EtcdQuorumAtRisk": ["any"],
            },
        )

    def test_member_without_leader_pages(self) -> None:
        self.expect(
            self.scenario(leader={"homelab-01": "0x90"}),
            {"EtcdNoLeader": ["homelab-01"]},
        )

    def test_slow_disk_warns_then_pages(self) -> None:
        self.expect(
            self.scenario(fsync={"homelab-02": 0.4}, commit={"homelab-02": 0.4}),
            {
                "EtcdWalFsyncSlow": ["homelab-02"],
                "EtcdBackendCommitSlow": ["homelab-02"],
            },
        )
        self.expect(
            self.scenario(fsync={"homelab-03": 1.5}),
            {
                "EtcdWalFsyncSlow": ["homelab-03"],
                "EtcdWalFsyncCritical": ["homelab-03"],
            },
        )

    def test_database_growth_warns_then_pages(self) -> None:
        self.expect(
            self.scenario(db_fraction={"homelab-01": 0.85}),
            {"EtcdDatabaseNearQuota": ["homelab-01"]},
        )
        self.expect(
            self.scenario(db_fraction={"homelab-01": 0.97}),
            {
                "EtcdDatabaseNearQuota": ["homelab-01"],
                "EtcdDatabaseAtQuota": ["homelab-01"],
            },
        )


if __name__ == "__main__":
    unittest.main()
