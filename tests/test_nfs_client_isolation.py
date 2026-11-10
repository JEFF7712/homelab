from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("kubectl"), "kubectl required to render manifests")
class NfsClientIsolationTests(unittest.TestCase):
    def test_rendered_policy_blocks_rpc_without_granting_other_access(self) -> None:
        result = subprocess.run(
            ["kubectl", "kustomize", str(ROOT / "gitops/platform")],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        policies = [
            doc
            for doc in yaml.safe_load_all(result.stdout)
            if doc
            and doc.get("kind") == "CiliumClusterwideNetworkPolicy"
            and doc["metadata"]["name"] == "prevent-direct-pod-nfs"
        ]
        self.assertEqual(len(policies), 1)
        spec = policies[0]["spec"]
        self.assertEqual(spec["endpointSelector"], {})
        self.assertNotIn("nodeSelector", spec)
        self.assertEqual(spec["enableDefaultDeny"], {"ingress": False, "egress": False})
        self.assertNotIn("egress", spec)
        self.assertNotIn("ingress", spec)
        self.assertEqual(len(spec["egressDeny"]), 1)
        deny = spec["egressDeny"][0]
        self.assertEqual(deny["toEntities"], ["all"])
        self.assertEqual(
            {
                (p["port"], p["protocol"])
                for group in deny["toPorts"]
                for p in group["ports"]
            },
            {("111", "TCP"), ("111", "UDP"), ("2049", "TCP"), ("2049", "UDP")},
        )
