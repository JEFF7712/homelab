import json
import pathlib
import re
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
    backup=True,
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
                    "-schema-location",
                    str(
                        ROOT
                        / "schemas/kubernetes/{{.ResourceKind}}{{.KindSuffix}}.json"
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
        # No CIDR egress at all: public reach is the FQDN policy's alone.
        self.assertNotIn("ipBlock", json.dumps(worker["egress"]))
        [fqdn] = by_kind(docs, "CiliumNetworkPolicy")
        self.assertEqual(
            fqdn["spec"]["endpointSelector"],
            {"matchLabels": {"app.kubernetes.io/name": "orchestrator-worker"}},
        )
        public = [rule for rule in fqdn["spec"]["egress"] if "toFQDNs" in rule]
        (rule,) = public
        self.assertEqual(
            sorted(item["matchName"] for item in rule["toFQDNs"]),
            ["api.anthropic.com", "api.openai.com", "auth.openai.com", "chatgpt.com"],
        )
        self.assertEqual(
            rule["toPorts"][0]["ports"], [{"port": "443", "protocol": "TCP"}]
        )
        self.assertEqual(
            rule["toPorts"][0]["serverNames"],
            ["api.anthropic.com", "api.openai.com", "auth.openai.com", "chatgpt.com"],
        )
        self.assertFalse(
            any(key in json.dumps(fqdn) for key in ("toCIDR", "toEntities"))
        )
        # DNS only for provider and cluster-internal names: no "*" that would
        # let a query carry data to an outside nameserver.
        [dns] = [r for r in fqdn["spec"]["egress"] if "toEndpoints" in r]
        patterns = dns["toPorts"][0]["rules"]["dns"]
        # Exact names only: no wildcard, which either stops at a dot in
        # Cilium or covers a zone CoreDNS forwards upstream.
        self.assertEqual(
            patterns,
            [
                {"matchName": name}
                for name in (
                    "api.anthropic.com",
                    "api.openai.com",
                    "auth.openai.com",
                    "chatgpt.com",
                    "orchestrator-controller.persistent-agent.svc.cluster.local",
                )
            ],
        )
        [config_map] = by_kind(docs, "ConfigMap")
        config = json.loads(config_map["data"]["config.json"])
        # The URL workers dial is exactly the name their DNS policy admits.
        self.assertEqual(
            config["worker_api_url"],
            "http://orchestrator-controller.persistent-agent.svc.cluster.local:8090",
        )
        _, job = self.worker_job(PARAMS)
        self.assertEqual(
            job["spec"]["template"]["spec"]["dnsConfig"],
            {"options": [{"name": "ndots", "value": "1"}]},
        )
        # The plain NetworkPolicy grants workers no DNS of its own.
        self.assertNotIn("53", json.dumps(worker["egress"]))
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
        for name, backend in backends.items():
            with self.subTest(name):
                # The Codex login store's mount is rendered, not supplied.
                params = replace(PARAMS, backend=backend)
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
                # The job file, plus only the backend's own credential key.
                self.assertEqual(
                    mounted,
                    ["job.json", "claude-oauth-token"]
                    if name == "claude"
                    else ["job.json"],
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
        evidence = [
            doc
            for doc in by_kind(docs, "RoleBinding")
            if doc["roleRef"]["name"] == "orchestrator-evidence"
        ]
        self.assertTrue(evidence)
        for binding in evidence:
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

    def test_backup_runs_beside_the_store_on_a_volume_of_its_own(self) -> None:
        self.assertEqual(by_kind(render(PARAMS), "CronJob"), [])
        docs = render(INTAKE)
        [cron] = by_kind(docs, "CronJob")
        pod = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        self.assertEqual(pod["nodeSelector"], {"kubernetes.io/hostname": "homelab-02"})
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(pod["securityContext"]["supplementalGroups"], [1000])
        [container] = pod["containers"]
        self.assertIn("backup-rotate", container["args"])
        claims = {
            volume["persistentVolumeClaim"]["claimName"]
            for volume in pod["volumes"]
            if "persistentVolumeClaim" in volume
        }
        self.assertEqual(claims, {"orchestrator-data", "orchestrator-dumps"})
        dumps = next(
            doc
            for doc in by_kind(docs, "PersistentVolumeClaim")
            if doc["metadata"]["name"] == "orchestrator-dumps"
        )
        # Its own provisioned directory, never the shared db-dumps export.
        self.assertEqual(dumps["spec"]["storageClassName"], "nfs-cluster")
        self.assertNotIn("volumeName", dumps["spec"])
        self.assertFalse(
            any("nfs" in doc["spec"] for doc in by_kind(docs, "PersistentVolume"))
        )

    def test_codex_store_is_rendered_and_has_one_writer(self) -> None:
        codex = {
            "kind": "codex",
            "codex": "codex",
            "model": "gpt-5.6-sol",
            "codex_home": "/var/lib/codex-auth",
        }
        with self.assertRaises(ValueError):
            render(replace(PARAMS, backend=codex, max_running=2))
        docs = render(replace(PARAMS, backend=codex))
        [config_map] = by_kind(docs, "ConfigMap")
        jobs = json.loads(config_map["data"]["config.json"])["jobs"]
        volumes = {volume["name"]: volume for volume in jobs["extra_volumes"]}
        self.assertIn(
            {"name": "codex-auth", "mountPath": "/var/lib/codex-auth"},
            jobs["extra_mounts"],
        )
        self.assertEqual(
            volumes["codex-auth"]["persistentVolumeClaim"]["claimName"],
            "orchestrator-codex-auth",
        )
        store = next(
            doc
            for doc in by_kind(docs, "PersistentVolume")
            if doc["metadata"]["name"] == "orchestrator-codex-auth"
        )
        self.assertEqual(
            store["spec"]["local"]["path"], "/persist/persistent-agent-codex-auth"
        )
        deployment = by_kind(docs, "Deployment")[0]
        self.assertNotIn(
            "codex", json.dumps(deployment["spec"]["template"]["spec"]["volumes"])
        )

    def test_admission_policy_is_applied_exactly_as_committed(self) -> None:
        committed = [
            doc
            for doc in yaml.safe_load_all(
                (ROOT / "config/orchestrator/worker-admission.yaml").read_text()
            )
            if doc
        ]
        for params in (PARAMS, INTAKE):
            docs = render(params)
            rendered = by_kind(docs, "ValidatingAdmissionPolicy") + by_kind(
                docs, "ValidatingAdmissionPolicyBinding"
            )
            self.assertEqual(rendered, committed)
        [policy, binding] = committed
        self.assertEqual(policy["spec"]["failurePolicy"], "Fail")
        self.assertEqual(binding["spec"]["validationActions"], ["Deny"])
        self.assertEqual(
            policy["spec"]["matchConditions"][0]["expression"].split(),
            [
                "request.userInfo.username",
                "==",
                '"system:serviceaccount:persistent-agent:orchestrator-controller"',
            ],
        )

    def test_parameters_the_static_policy_fixes_are_required(self) -> None:
        for bad in (
            # A crafted Secret name once became CEL in the rendered policy.
            replace(
                PARAMS, worker_secret_name='x" || true || v.secret.secretName == "x'
            ),
            replace(PARAMS, worker_secret_name="orchestrator-secrets"),
            replace(
                PARAMS, worker_image=f"registry.rupan.dev/apps/orchestrator@{DIGEST}"
            ),
        ):
            with self.assertRaises(ValueError):
                render(bad)

    def test_rendered_worker_jobs_fit_the_admission_policy(self) -> None:
        """The facts the static policy checks, against the real Job builder;
        gate 4 proves the CEL itself against a live API server."""
        policy = (ROOT / "config/orchestrator/worker-admission.yaml").read_text()
        name_rule = re.search(r'name\.matches\("([^"]+)"\)', policy).group(1)
        image_rule = re.search(r'c\.image\.matches\(\s*"([^"]+)"\)', policy).group(1)
        image_rule = image_rule.replace("\\\\", "\\")
        for kind in ("pi", "claude"):
            backend = (
                PARAMS.backend
                if kind == "pi"
                else {
                    "kind": "claude",
                    "claude": "claude",
                    "model": "m",
                    "config_dir": "/c",
                    "token_file": "/run/worker/claude-oauth-token",
                }
            )
            _, job = self.worker_job(replace(PARAMS, backend=backend))
            self.assertRegex(job["metadata"]["name"], name_rule)
            pod = job["spec"]["template"]["spec"]
            for container in pod["containers"]:
                self.assertRegex(container["image"], image_rule)
            for volume in pod["volumes"]:
                if "secret" not in volume:
                    continue
                keys = [item["key"] for item in volume["secret"]["items"]]
                if volume["secret"]["secretName"] == job["metadata"]["name"]:
                    self.assertEqual(keys, ["job.json"])
                else:
                    self.assertEqual(
                        volume["secret"]["secretName"], "orchestrator-worker-secrets"
                    )
                    self.assertLessEqual(set(keys), {"claude-oauth-token"})
        [config_map] = by_kind(render(PARAMS), "ConfigMap")
        self.assertEqual(
            json.loads(config_map["data"]["config.json"])["work_root"], "/work"
        )

    def test_no_rendered_role_can_change_admission_or_rbac(self) -> None:
        for docs in (render(PARAMS), render(INTAKE)):
            for doc in by_kind(docs, "Role") + by_kind(docs, "ClusterRole"):
                for rule in doc["rules"]:
                    self.assertFalse(
                        {
                            "admissionregistration.k8s.io",
                            "rbac.authorization.k8s.io",
                            "*",
                        }
                        & set(rule["apiGroups"]),
                        doc["metadata"],
                    )

    def test_evidence_is_bound_only_in_enrolled_namespaces(self) -> None:
        docs = render(INTAKE)
        self.assertEqual(by_kind(docs, "ClusterRoleBinding"), [])
        [role] = by_kind(docs, "ClusterRole")
        self.assertEqual(
            {
                (r, v)
                for rule in role["rules"]
                for r in rule["resources"]
                for v in rule["verbs"]
            },
            {
                ("pods", "get"),
                ("pods", "list"),
                ("pods/log", "get"),
                ("events", "list"),
            },
        )
        enrolled = json.loads(
            (ROOT / "config/orchestrator/evidence-namespaces.json").read_text()
        )
        bound = {
            doc["metadata"]["namespace"]
            for doc in by_kind(docs, "RoleBinding")
            if doc["roleRef"]["name"] == "orchestrator-evidence"
        }
        self.assertEqual(bound, set(enrolled["allowed"]))
        for excluded in (
            "pod-agent",
            "external-secrets",
            "kube-system",
            "persistent-agent",
        ):
            self.assertIn(excluded, enrolled["excluded"])
            self.assertNotIn(excluded, bound)
        [config_map] = by_kind(docs, "ConfigMap")
        config = json.loads(config_map["data"]["config.json"])
        self.assertEqual(
            set(config["intake"]["evidence_namespaces"]), set(enrolled["allowed"])
        )

    def test_every_namespace_alerts_can_name_is_allowed_or_excluded(self) -> None:
        enrolled = json.loads(
            (ROOT / "config/orchestrator/evidence-namespaces.json").read_text()
        )
        classified = set(enrolled["allowed"]) | set(enrolled["excluded"])
        self.assertFalse(set(enrolled["allowed"]) & set(enrolled["excluded"]))
        named: dict[str, str] = {}
        # Alert rules label alerts with the namespace of whatever fired, so
        # every namespace the repository runs things in can be named.
        for path in sorted((ROOT / "gitops").rglob("*.yaml")):
            for document in yaml.safe_load_all(path.read_text()):
                if not isinstance(document, dict):
                    continue
                metadata = document.get("metadata") or {}
                if document.get("kind") == "Namespace" and metadata.get("name"):
                    named.setdefault(metadata["name"], str(path))
                namespace = metadata.get("namespace")
                # SOPS-encrypted manifests carry ciphertext, not names.
                if isinstance(namespace, str) and not namespace.startswith("ENC["):
                    named.setdefault(namespace, str(path))
        # Namespaces named literally in alert expressions and the sentinel.
        literal = re.compile(
            r'namespace\s*=~?\s*"([a-z][a-z0-9-]*)"|-n "?([a-z][a-z0-9-]*)"?\s'
        )
        sources = [
            *sorted((ROOT / "gitops/observability").rglob("*.yaml")),
            ROOT / "gitops/automation/cluster-sentinel-script.yaml",
        ]
        for path in sources:
            for match in literal.finditer(path.read_text()):
                name = match.group(1) or match.group(2)
                if name and not name.startswith("$"):
                    named.setdefault(name, str(path))
        unclassified = {
            name: where for name, where in named.items() if name not in classified
        }
        self.assertEqual(unclassified, {})


if __name__ == "__main__":
    unittest.main()
