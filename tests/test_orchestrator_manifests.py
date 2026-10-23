import json
import pathlib
import subprocess
import tempfile
import unittest
from dataclasses import replace

import yaml

from scripts.orchestrator.jobs import JobSpec, KubernetesJobConfig, render_job
from scripts.orchestrator.manifests import DeployParams, render

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
        docs = render(PARAMS)
        spec = JobSpec(
            "att_1",
            "task_1",
            "http://c",
            "t",
            "b",
            "r",
            "main",
            "agent/task_1/1",
            {"kind": "pi"},
            "m",
        )
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


if __name__ == "__main__":
    unittest.main()
