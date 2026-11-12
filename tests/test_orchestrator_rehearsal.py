import json
import pathlib
import unittest

from scripts.orchestrator.manifests import render
from scripts.orchestrator.rehearsal import (
    admission_cases,
    params,
    placeholder_secrets,
    probe_pod,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent


class RehearsalTests(unittest.TestCase):
    def test_rehearsal_renders_the_pinned_images_on_homelab_02(self) -> None:
        deploy = params()
        pins = json.loads((ROOT / "config/orchestrator/images.json").read_text())
        self.assertEqual(deploy.controller_image, pins["controller"])
        self.assertEqual(deploy.worker_image, pins["worker"])
        self.assertEqual(deploy.node, "homelab-02")
        kinds = {doc["kind"] for doc in render(deploy)}
        self.assertIn("ValidatingAdmissionPolicy", kinds)
        self.assertIn("CiliumNetworkPolicy", kinds)

    def test_placeholder_secrets_are_random_and_cover_the_config(self) -> None:
        deploy = params()
        first = placeholder_secrets(deploy)
        second = placeholder_secrets(deploy)
        self.assertNotEqual(first[0]["stringData"], second[0]["stringData"])
        [config_map] = [d for d in render(deploy) if d["kind"] == "ConfigMap"]
        config = json.loads(config_map["data"]["config.json"])
        referenced = {
            path.rsplit("/", 1)[1]
            for path in json.dumps(config).split('"')
            if path.startswith("/run/orchestrator/")
        }
        self.assertLessEqual(referenced, set(first[0]["stringData"]))

    def test_probe_runs_with_a_worker_identity(self) -> None:
        deploy = params()
        pod = probe_pod(deploy)
        spec = pod["spec"]
        self.assertEqual(
            pod["metadata"]["labels"], {"app.kubernetes.io/name": "orchestrator-worker"}
        )
        self.assertEqual(spec["serviceAccountName"], "orchestrator-worker")
        self.assertFalse(spec["automountServiceAccountToken"])
        self.assertEqual(
            spec["dnsConfig"], {"options": [{"name": "ndots", "value": "1"}]}
        )
        self.assertEqual(
            spec["containers"][0]["args"],
            ["python", "-m", "scripts.orchestrator.network_probe"],
        )
        self.assertNotIn("job", json.dumps(spec["volumes"]))

    def test_exactly_one_case_is_admitted_and_each_denial_changes_the_job(self) -> None:
        cases = admission_cases(params())
        admitted = [name for name, ok, _ in cases if ok]
        self.assertEqual(admitted, ["worker job"])
        good = cases[0][2]
        for name, _, job in cases[1:]:
            with self.subTest(name):
                self.assertNotEqual(job, good)


if __name__ == "__main__":
    unittest.main()
