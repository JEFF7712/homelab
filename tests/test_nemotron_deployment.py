import unittest
from pathlib import Path

import yaml


class NativeDeploymentTest(unittest.TestCase):
    def test_packaged_runtime_is_not_masked(self):
        root = Path(__file__).resolve().parents[1]
        resources = list(
            yaml.safe_load_all((root / "gitops/voice/stt-nemotron.yaml").read_text())
        )
        deployment = next(item for item in resources if item["kind"] == "Deployment")
        spec = deployment["spec"]["template"]["spec"]
        container = spec["containers"][0]
        self.assertRegex(
            container["image"],
            r"^registry\.rupan\.dev/apps/jarvis-nemotron@sha256:[0-9a-f]{64}$",
        )
        self.assertNotIn("command", container)
        self.assertNotIn("LD_LIBRARY_PATH", {env["name"] for env in container["env"]})
        self.assertFalse(
            {"/app", "/opt/nemo/lib64"}
            & {mount["mountPath"] for mount in container["volumeMounts"]}
        )
        self.assertEqual(spec["nodeSelector"]["kubernetes.io/hostname"], "homelab-04")
        self.assertEqual(container["resources"]["limits"]["nvidia.com/gpu"], "1")
        self.assertTrue(
            {"/models", "/data", "/etc/voice-id"}
            <= {mount["mountPath"] for mount in container["volumeMounts"]}
        )
        kustomization = yaml.safe_load(
            (root / "gitops/voice/kustomization.yaml").read_text()
        )
        self.assertNotIn(
            "nemotron-bridge-code",
            {item["name"] for item in kustomization["configMapGenerator"]},
        )


if __name__ == "__main__":
    unittest.main()
