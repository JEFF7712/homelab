import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


class TestMediaCloudflareRuntimeWiring(unittest.TestCase):
    def test_gluetun_health_probe_port_passes_through_firewall(self) -> None:
        docs = yaml.safe_load_all(
            (ROOT / "gitops/media/download.yaml").read_text(encoding="utf-8")
        )
        deployment = next(
            doc for doc in docs if doc and doc.get("kind") == "Deployment"
        )
        gluetun = next(
            container
            for container in deployment["spec"]["template"]["spec"]["containers"]
            if container["name"] == "gluetun"
        )
        env = {item["name"]: item.get("value") for item in gluetun["env"]}

        self.assertIn("9999", env["FIREWALL_INPUT_PORTS"].split(","))
        self.assertEqual(gluetun["readinessProbe"]["httpGet"]["port"], 9999)

    def test_cloudflared_reloader_observes_its_configmap(self) -> None:
        deployment = yaml.safe_load(
            (ROOT / "gitops/cloudflare/tunnel.yaml").read_text(encoding="utf-8")
        )

        annotations = deployment["spec"]["template"]["metadata"]["annotations"]
        self.assertEqual(annotations.get("reloader.stakater.com/auto"), "true")


if __name__ == "__main__":
    unittest.main()
