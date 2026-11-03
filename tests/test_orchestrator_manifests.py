import json
import pathlib
import subprocess
import tempfile
import unittest
from dataclasses import replace

import yaml

from scripts.orchestrator.jobs import JobSpec, KubernetesJobConfig, render_job
from scripts.orchestrator.manifests import DeployParams, IntakeParams, render

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIGEST = "sha256:" + "1" * 64
PARAMS = DeployParams(
    node="homelab-02",
    controller_image=f"registry.rupan.dev/apps/orchestrator@{DIGEST}",
    worker_image=f"registry.rupan.dev/apps/orchestrator-worker@{DIGEST}",
    host_path="/persist/persistent-agent",
    forge_url="https://git.rupan.dev",
    forge_ip="10.0.30.20",
    forge_owner="JEFF7712",
    forge_repo="agent-sandbox",
    project="agent-sandbox",
    repository_url="https://git.rupan.dev/JEFF7712/agent-sandbox.git",
    issue_url_prefix="https://git.rupan.dev/JEFF7712/agent-sandbox/issues",
    backend={"kind": "pi", "pi": "pi", "provider": "openai", "model": "gpt-5.6-sol"},
    principal="rupan",
    forge_login="JEFF7712",
    required_contexts=("ci/woodpecker/pr/validation",),
    ci_login="woodpecker",
    api_endpoints=(
        ("10.43.0.1", 443),
        ("10.0.30.11", 6443),
        ("10.0.30.12", 6443),
        ("10.0.30.13", 6443),
    ),
    ntfy_topic="orchestrator",
)
INTAKE = replace(
    PARAMS,
    intake=IntakeParams(
        woodpecker_url="http://ci.internal:8000",
        woodpecker_ip="10.0.30.20",
        woodpecker_port=8000,
        woodpecker_repo_id=1,
    ),
    backup_nfs_path="/tank/cluster/backups-db-dumps-pvc-dcd285d5",
)


def by_kind(docs: list[dict], kind: str) -> list[dict]:
    return [doc for doc in docs if doc["kind"] == kind]


class ManifestTests(unittest.TestCase):
    def test_decisions_are_required_not_defaulted(self) -> None:
        for bad in (
            replace(PARAMS, node="homelab-05"),
            replace(PARAMS, node="homelab-04"),
            replace(PARAMS, backend={**PARAMS.backend, "env": {"OPENAI_API_KEY": "x"}}),
            replace(PARAMS, node=""),
            replace(
                PARAMS, controller_image="registry.rupan.dev/apps/orchestrator:latest"
            ),
            replace(PARAMS, host_path="/tmp/x"),
        ):
            with self.assertRaises(ValueError):
                render(bad)

    def test_rendered_manifests_pass_the_repository_schemas(self) -> None:
        docs = render(INTAKE)
        spec = JobSpec("att_1", "task_1", "http://c", "t", "b", {"kind": "pi"})
        secret, job = render_job(
            spec,
            KubernetesJobConfig(
                namespace="persistent-agent", image=PARAMS.worker_image
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "all.yaml"
            path.write_text(yaml.safe_dump_all([*docs, secret, job]))
            result = subprocess.run(
                [
                    "kubeconform",
                    "-strict",
                    "-summary",
                    "-kubernetes-version",
                    "1.35.7",
                    "-schema-location",
                    str(
                        ROOT
                        / "schemas/kubernetes-core/v1.35.7-standalone-strict/{{.ResourceKind}}{{.KindSuffix}}.json"
                    ),
                    str(path),
                ],
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Skipped: 0", result.stdout + result.stderr)

    def test_single_pinned_controller_with_protected_state(self) -> None:
        docs = render(PARAMS)
        (deployment,) = by_kind(docs, "Deployment")
        self.assertEqual(deployment["spec"]["replicas"], 1)
        self.assertEqual(deployment["spec"]["strategy"]["type"], "Recreate")
        pod = deployment["spec"]["template"]["spec"]
        self.assertEqual(pod["nodeSelector"], {"kubernetes.io/hostname": "homelab-02"})
        (volume,) = by_kind(docs, "PersistentVolume")
        terms = volume["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"]
        self.assertEqual(terms[0]["matchExpressions"][0]["values"], ["homelab-02"])
        for kind in ("Namespace", "PersistentVolume", "PersistentVolumeClaim"):
            (doc,) = by_kind(docs, kind)
            self.assertEqual(
                doc["metadata"]["annotations"]["kustomize.toolkit.fluxcd.io/prune"],
                "disabled",
            )
        (namespace,) = by_kind(docs, "Namespace")
        self.assertEqual(
            namespace["metadata"]["labels"]["pod-security.kubernetes.io/enforce"],
            "restricted",
        )
        self.assertNotIn("hostPath", json.dumps(pod))

    def test_workers_get_no_cluster_credentials_and_no_private_network(self) -> None:
        docs = render(PARAMS)
        accounts = {
            doc["metadata"]["name"]: doc for doc in by_kind(docs, "ServiceAccount")
        }
        self.assertIs(
            accounts["orchestrator-worker"]["automountServiceAccountToken"], False
        )
        bindings = by_kind(docs, "RoleBinding")
        self.assertEqual(
            [s["name"] for b in bindings for s in b["subjects"]],
            ["orchestrator-controller"],
        )
        policies = {
            doc["metadata"]["name"]: doc for doc in by_kind(docs, "NetworkPolicy")
        }
        self.assertEqual(
            policies["default-deny"]["spec"]["policyTypes"], ["Ingress", "Egress"]
        )
        worker = policies["orchestrator-worker"]["spec"]
        self.assertEqual(worker["ingress"], [])
        public = [
            rule
            for rule in worker["egress"]
            if rule["to"][0].get("ipBlock", {}).get("cidr") == "0.0.0.0/0"
        ]
        (rule,) = public
        self.assertIn("10.0.0.0/8", rule["to"][0]["ipBlock"]["except"])
        self.assertEqual(rule["ports"], [{"protocol": "TCP", "port": 443}])
        config = json.loads(by_kind(docs, "ConfigMap")[0]["data"]["config.json"])
        self.assertEqual(config["listen"]["client"], "127.0.0.1:8091")
        self.assertEqual(
            config["jobs"]["node_selector"], {"kubernetes.io/hostname": "homelab-02"}
        )

    def test_backend_settings_follow_a_closed_schema(self) -> None:
        from scripts.orchestrator.manifests import validate_backend

        claude = {
            "kind": "claude",
            "claude": "claude",
            "model": "claude-opus-5-5",
            "config_dir": "/work/claude",
            "token_file": "/run/worker/claude-oauth-token",
        }
        codex = {
            "kind": "codex",
            "codex": "codex",
            "model": "gpt-5.6-sol",
            "codex_home": "/var/lib/codex-auth",
        }
        codex_mount = ({"name": "codex-auth", "mountPath": "/var/lib/codex-auth"},)
        validate_backend(PARAMS.backend)
        validate_backend(claude)
        validate_backend(codex, codex_mount)
        refused = {
            # credentials under innocuous names, in values, or in env
            "innocuous key": {**PARAMS.backend, "notes": "sk-live-abc"},
            "env": {**PARAMS.backend, "env": {"OPENAI_API_KEY": "x"}},
            "credential in model": {**PARAMS.backend, "model": "gpt sk-abc def"},
            "token-shaped model": {**PARAMS.backend, "model": "sk-ant-oat01-abc"},
            "token-shaped provider": {**PARAMS.backend, "provider": "ghp_abcdef"},
            "url userinfo": {**PARAMS.backend, "pi": "https://u:p@host/pi"},
            "token elsewhere": {
                **claude,
                "token_file": "/run/orchestrator/forge-publisher-token",
            },
            "inline token": {**claude, "token_file": "sk-ant-oat01-abc"},
            "unmounted codex home": codex,
            "other codex home": {**codex, "codex_home": "/home/rupan/.codex"},
            "missing token": {k: v for k, v in claude.items() if k != "token_file"},
            "bad sandbox": {**claude, "sandbox": "none"},
            "flag type": {**PARAMS.backend, "experimental": "yes"},
            "unknown kind": {"kind": "shell"},
        }
        for name, backend in refused.items():
            with self.subTest(name), self.assertRaises(ValueError):
                validate_backend(
                    backend, codex_mount if name != "unmounted codex home" else ()
                )

    def test_production_never_enables_local_jobs(self) -> None:
        docs = render(PARAMS)
        (deployment,) = by_kind(docs, "Deployment")
        args = deployment["spec"]["template"]["spec"]["containers"][0]["args"]
        self.assertNotIn("--allow-local-jobs", args)
        config = json.loads(by_kind(docs, "ConfigMap")[0]["data"]["config.json"])
        self.assertEqual(config["jobs"]["kind"], "kubernetes")

    def worker_job(self, params: DeployParams) -> tuple[dict, dict]:
        """The Secret and Job the controller would create for one attempt."""
        config = json.loads(
            by_kind(render(params), "ConfigMap")[0]["data"]["config.json"]
        )
        jobs = config["jobs"]
        spec = JobSpec(
            "att_1", "task_1", "http://c", "attempt-token", "b", params.backend
        )
        return render_job(
            spec,
            KubernetesJobConfig(
                namespace=jobs["namespace"],
                image=jobs["image"],
                extra_volumes=tuple(jobs["extra_volumes"]),
                extra_mounts=tuple(jobs["extra_mounts"]),
            ),
        )

    def test_workers_hold_no_forge_credential_and_cannot_reach_the_forge(
        self,
    ) -> None:
        backends = {
            "pi": PARAMS.backend,
            "codex": {
                "kind": "codex",
                "codex": "codex",
                "model": "m",
                "codex_home": "/var/lib/codex-auth",
            },
            "claude": {
                "kind": "claude",
                "claude": "claude",
                "model": "m",
                "config_dir": "/c",
                "token_file": "/run/worker/claude-oauth-token",
            },
        }
        codex_mount = ({"name": "codex-auth", "mountPath": "/var/lib/codex-auth"},)
        for name, backend in backends.items():
            with self.subTest(name):
                params = replace(
                    PARAMS,
                    backend=backend,
                    extra_worker_mounts=codex_mount if name == "codex" else (),
                )
                secret, job = self.worker_job(params)
                document = json.dumps(job)
                self.assertNotIn("forge", document)
                self.assertNotIn(params.secret_name, document)
                pod = job["spec"]["template"]["spec"]
                (container,) = pod["containers"]
                self.assertEqual(
                    {e["name"] for e in container["env"]}, {"HOME", "TMPDIR"}
                )
                mounted = [
                    item["key"]
                    for volume in pod["volumes"]
                    for item in volume.get("secret", {}).get("items", [])
                ]
                self.assertEqual(
                    mounted, ["claude-oauth-token"] if name == "claude" else []
                )
                job_file = json.loads(secret["stringData"]["job.json"])
                self.assertEqual(
                    set(job_file),
                    {
                        "attempt_id",
                        "task_id",
                        "api_url",
                        "token",
                        "brief",
                        "backend",
                        "lease_seconds",
                        "deadline_seconds",
                        "poll_seconds",
                        "work_root",
                    },
                )
        docs = render(PARAMS)
        policies = {d["metadata"]["name"]: d for d in by_kind(docs, "NetworkPolicy")}
        egress = json.dumps(policies["orchestrator-worker"]["spec"]["egress"])
        self.assertNotIn(PARAMS.forge_ip, egress)
        controller_egress = json.dumps(policies["orchestrator-controller"]["spec"])
        self.assertIn(PARAMS.forge_ip, controller_egress)

    def test_only_the_controller_mounts_the_publisher_credential(self) -> None:
        docs = render(PARAMS)
        config = json.loads(by_kind(docs, "ConfigMap")[0]["data"]["config.json"])
        self.assertEqual(
            config["publisher"]["token_file"],
            "/run/orchestrator/forge-publisher-token",
        )
        self.assertNotIn("worker_git_token_file", config)
        (deployment,) = by_kind(docs, "Deployment")
        volumes = deployment["spec"]["template"]["spec"]["volumes"]
        self.assertIn(
            PARAMS.secret_name,
            [v.get("secret", {}).get("secretName") for v in volumes],
        )
        self.assertNotIn("/run/orchestrator", json.dumps(config["jobs"]))

    def test_intake_evidence_rbac_never_reaches_secrets(self) -> None:
        self.assertEqual(by_kind(render(PARAMS), "ClusterRole"), [])
        docs = render(INTAKE)
        [role] = by_kind(docs, "ClusterRole")
        [binding] = by_kind(docs, "ClusterRoleBinding")
        self.assertEqual(
            binding["subjects"],
            [
                {
                    "kind": "ServiceAccount",
                    "name": "orchestrator-controller",
                    "namespace": "persistent-agent",
                }
            ],
        )
        for rule in role["rules"]:
            self.assertFalse({"secrets", "configmaps", "*"} & set(rule["resources"]))
            self.assertLessEqual(set(rule["verbs"]), {"get", "list"})
        for doc in by_kind(docs, "Role") + by_kind(docs, "ClusterRole"):
            for rule in doc["rules"]:
                if "secrets" in rule["resources"]:
                    self.assertFalse(
                        {"get", "list", "watch"} & set(rule["verbs"]), doc["metadata"]
                    )

    def test_only_alert_sources_reach_the_intake_port(self) -> None:
        docs = render(INTAKE)
        policy = next(
            doc
            for doc in by_kind(docs, "NetworkPolicy")
            if doc["metadata"]["name"] == "orchestrator-controller"
        )
        intake = [
            rule
            for rule in policy["spec"]["ingress"]
            if rule["ports"] == [{"protocol": "TCP", "port": 8092}]
        ]
        [rule] = intake
        sources = {
            (
                peer["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"],
                peer["podSelector"]["matchLabels"]["app.kubernetes.io/name"],
            )
            for peer in rule["from"]
        }
        self.assertEqual(
            sources,
            {("observability", "alertmanager"), ("automation", "cluster-sentinel")},
        )
        self.assertIn(
            {
                "to": [{"ipBlock": {"cidr": "10.0.30.20/32"}}],
                "ports": [{"protocol": "TCP", "port": 8000}],
            },
            policy["spec"]["egress"],
        )
        worker = next(
            doc
            for doc in by_kind(docs, "NetworkPolicy")
            if doc["metadata"]["name"] == "orchestrator-worker"
        )
        self.assertEqual(worker["spec"]["ingress"], [])
        [config_map] = by_kind(docs, "ConfigMap")
        config = json.loads(config_map["data"]["config.json"])
        self.assertEqual(config["intake"]["open_limit"], 3)
        self.assertTrue(
            all(
                path.startswith("/run/orchestrator/")
                for path in config["intake"]["token_files"].values()
            )
        )
        self.assertNotIn("intake", json.dumps(config["jobs"]))

    def test_backup_runs_beside_the_store_and_hands_snapshots_to_the_shipper(
        self,
    ) -> None:
        with self.assertRaises(ValueError):
            render(replace(INTAKE, backup_nfs_path="/tank/cluster/immich-library"))
        self.assertEqual(by_kind(render(PARAMS), "CronJob"), [])
        docs = render(INTAKE)
        [cron] = by_kind(docs, "CronJob")
        pod = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        self.assertEqual(pod["nodeSelector"], {"kubernetes.io/hostname": "homelab-02"})
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(pod["securityContext"]["supplementalGroups"], [1000])
        [container] = pod["containers"]
        self.assertIn("backup-rotate", container["args"] + container.get("command", []))
        claims = {
            volume["persistentVolumeClaim"]["claimName"]
            for volume in pod["volumes"]
            if "persistentVolumeClaim" in volume
        }
        self.assertEqual(claims, {"orchestrator-data", "orchestrator-dumps"})
        dumps = next(
            doc
            for doc in by_kind(docs, "PersistentVolume")
            if doc["metadata"]["name"] == "orchestrator-dumps"
        )
        self.assertEqual(
            dumps["spec"]["nfs"],
            {
                "server": "10.0.30.20",
                "path": "/tank/cluster/backups-db-dumps-pvc-dcd285d5",
            },
        )


if __name__ == "__main__":
    unittest.main()
