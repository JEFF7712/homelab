from __future__ import annotations

import pathlib
import subprocess
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


class AutomationCredentials(unittest.TestCase):
    def test_obsolete_static_token_is_absent_but_projected_job_identity_remains(
        self,
    ) -> None:
        rendered = subprocess.run(
            ["kubectl", "kustomize", str(ROOT / "gitops/automation")],
            check=True,
            capture_output=True,
            text=True,
        )
        documents = [
            doc for doc in yaml.safe_load_all(rendered.stdout) if isinstance(doc, dict)
        ]
        self.assertFalse(
            any(
                doc["kind"] == "Secret"
                and doc["metadata"]["name"] == "automation-runner-ci-token"
                for doc in documents
            )
        )
        self.assertTrue(
            any(
                doc["kind"] == "ServiceAccount"
                and doc["metadata"]["name"] == "automation-runner"
                for doc in documents
            )
        )
        reader = next(
            doc
            for doc in documents
            if doc["kind"] == "ClusterRoleBinding"
            and doc["metadata"]["name"] == "automation-deployment-reader"
        )
        self.assertEqual(
            reader["subjects"],
            [
                {
                    "kind": "ServiceAccount",
                    "name": "automation-runner",
                    "namespace": "automation",
                }
            ],
        )
