import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "gitops" / "media" / "spotify-sync.yaml"
KUSTOMIZATION = ROOT / "gitops" / "media" / "kustomization.yaml"
SECRETS = ROOT / "gitops" / "media" / "secrets.yaml"


class TestSpotifySyncManifest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        documents = list(yaml.safe_load_all(MANIFEST.read_text()))
        cls.config_map = next(doc for doc in documents if doc["kind"] == "ConfigMap")
        cls.cronjob = next(doc for doc in documents if doc["kind"] == "CronJob")

    def test_manifest_is_owned_by_media_kustomization(self) -> None:
        kustomization = yaml.safe_load(KUSTOMIZATION.read_text())
        self.assertIn("spotify-sync.yaml", kustomization["resources"])

    def test_cronjob_is_hourly_and_non_overlapping(self) -> None:
        spec = self.cronjob["spec"]
        self.assertEqual(spec["schedule"], "17 * * * *")
        self.assertEqual(spec["timeZone"], "America/Chicago")
        self.assertEqual(spec["concurrencyPolicy"], "Forbid")
        self.assertEqual(spec["jobTemplate"]["spec"]["activeDeadlineSeconds"], 21600)

    def test_cronjob_mounts_library_and_script(self) -> None:
        pod = self.cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        container = pod["containers"][0]
        mounts = {mount["name"]: mount for mount in container["volumeMounts"]}
        volumes = {volume["name"]: volume for volume in pod["volumes"]}
        self.assertEqual(mounts["media"]["mountPath"], "/music")
        self.assertTrue(mounts["script"]["readOnly"])
        self.assertEqual(
            volumes["media"]["persistentVolumeClaim"]["claimName"],
            "media-library",
        )
        self.assertEqual(volumes["script"]["configMap"]["name"], "spotify-sync")

    def test_runtime_is_non_root_and_filesystem_read_only(self) -> None:
        container = self.cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"][
            "containers"
        ][0]
        security = container["securityContext"]
        self.assertTrue(security["readOnlyRootFilesystem"])
        self.assertTrue(security["runAsNonRoot"])
        self.assertEqual(security["runAsUser"], 1000)
        self.assertEqual(security["capabilities"]["drop"], ["ALL"])

    def test_credentials_are_secret_references(self) -> None:
        container = self.cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"][
            "containers"
        ][0]
        env = {entry["name"]: entry for entry in container["env"]}
        for name in (
            "SPOTIFY_CLIENT_ID",
            "SPOTIFY_CLIENT_SECRET",
            "SPOTIFY_REFRESH_TOKEN",
            "LIDARR_API_KEY",
        ):
            self.assertEqual(
                env[name]["valueFrom"]["secretKeyRef"],
                {"name": "media-app", "key": name},
            )
        self.assertNotIn("secretKey", self.config_map["data"])
        script = self.config_map["data"]["sync.py"]
        self.assertNotIn("AQC1", script)
        self.assertNotIn("b14ea1b6b14b4b2d9c29383d44f05a53", script)

    def test_external_secret_exposes_spotify_credentials(self) -> None:
        documents = list(yaml.safe_load_all(SECRETS.read_text()))
        media_app = next(
            document
            for document in documents
            if document.get("kind") == "ExternalSecret"
            and document["metadata"]["name"] == "media-app"
        )
        keys = {entry["secretKey"] for entry in media_app["spec"]["data"]}
        self.assertTrue(
            {
                "SPOTIFY_CLIENT_ID",
                "SPOTIFY_CLIENT_SECRET",
                "SPOTIFY_REFRESH_TOKEN",
                "LIDARR_API_KEY",
            }.issubset(keys)
        )


if __name__ == "__main__":
    unittest.main()
