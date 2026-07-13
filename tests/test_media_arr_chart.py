from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
CHART_PATH = REPO_ROOT / "infrastructure" / "media-arr"


class MediaArrChartTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        rendered = subprocess.run(
            ["helm", "template", "media-arr", str(CHART_PATH), "--namespace", "media"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        documents = [document for document in yaml.safe_load_all(rendered) if isinstance(document, dict)]
        identities = [(document["kind"], document["metadata"]["name"]) for document in documents]
        if len(identities) != len(set(identities)):
            raise AssertionError("chart rendered duplicate resource identities")
        cls.resources = dict(zip(identities, documents, strict=True))

    def test_renders_four_resources_per_workload(self) -> None:
        expected = set()
        for name in ("sonarr", "radarr", "lidarr", "prowlarr", "bazarr"):
            expected.add(("PersistentVolumeClaim", f"{name}-config"))
            expected.update((kind, name) for kind in ("Deployment", "Service", "Ingress"))

        self.assertEqual(expected, set(self.resources))
        self.assertEqual(20, len(self.resources))

    def test_preserves_workload_specific_images_ports_and_storage(self) -> None:
        expected = {
            "sonarr": ("lscr.io/linuxserver/sonarr@sha256:0b3f344388bd7bed4f2f770058de795e76447e4a481b83c8d5f8fed489371fde", 8989, "2Gi"),
            "radarr": ("lscr.io/linuxserver/radarr@sha256:c0a4335d4249b46102f64cf6fa27ffc3bddfd9138fac1e4ddf238afd37f02d1f", 7878, "2Gi"),
            "lidarr": ("ghcr.io/hotio/lidarr@sha256:f8a76d2ff0dac8e449c93c7c0e81b5fb063a3cd926d12420fdb1388818d2fdfe", 8686, "20Gi"),
            "prowlarr": ("lscr.io/linuxserver/prowlarr@sha256:2489c6dbaf11e3a6d71aeb2e6980d04193d4af611aa7064a974851222fd41722", 9696, "2Gi"),
            "bazarr": ("lscr.io/linuxserver/bazarr@sha256:4b5e510042bf471c8bafab89cada9774fba2fb25f16ec64235151cacbe847c10", 6767, "2Gi"),
        }

        for name, (image, port, storage) in expected.items():
            deployment = self.resources[("Deployment", name)]
            container = deployment["spec"]["template"]["spec"]["containers"][0]
            claim = self.resources[("PersistentVolumeClaim", f"{name}-config")]
            self.assertEqual(image, container["image"])
            self.assertEqual(port, container["ports"][0]["containerPort"])
            self.assertEqual(storage, claim["spec"]["resources"]["requests"]["storage"])

    def test_preserves_permission_and_media_mount_exceptions(self) -> None:
        for name in ("sonarr", "radarr", "lidarr"):
            pod_spec = self.resources[("Deployment", name)]["spec"]["template"]["spec"]
            self.assertEqual("fix-permissions", pod_spec["initContainers"][0]["name"])

        for name in ("prowlarr", "bazarr"):
            pod_spec = self.resources[("Deployment", name)]["spec"]["template"]["spec"]
            self.assertNotIn("initContainers", pod_spec)

        prowlarr_mounts = self.resources[("Deployment", "prowlarr")]["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
        self.assertNotIn("/data", {mount["mountPath"] for mount in prowlarr_mounts})

    def test_preserves_lidarr_extensions_and_sonarr_probe(self) -> None:
        lidarr = self.resources[("Deployment", "lidarr")]["spec"]["template"]["spec"]
        lidarr_container = lidarr["containers"][0]
        self.assertEqual(["/bin/sh", "-c"], lidarr_container["command"])
        self.assertIn("extended-config", {volume["name"] for volume in lidarr["volumes"]})
        self.assertFalse(lidarr_container["securityContext"]["readOnlyRootFilesystem"])

        sonarr = self.resources[("Deployment", "sonarr")]["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual("/ping", sonarr["livenessProbe"]["httpGet"]["path"])


if __name__ == "__main__":
    unittest.main()
