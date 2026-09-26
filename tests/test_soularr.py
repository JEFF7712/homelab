"""Soularr wiring: Lidarr wanted list -> Soulseek downloads via slskd.

Covers the ConfigMap template plus boot script embedded in
gitops/media/soularr.yaml (loaded from the manifest so tests track the
shipped code) and the cross-manifest wiring (CronJob, slskd env,
ExternalSecret key, kustomization, PVC).
"""

import configparser
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SOULARR_MANIFEST = ROOT / "gitops" / "media" / "soularr.yaml"
ARR_MANIFEST = ROOT / "gitops" / "media" / "arr.yaml"
DOWNLOAD_MANIFEST = ROOT / "gitops" / "media" / "download.yaml"
SECRETS_MANIFEST = ROOT / "gitops" / "media" / "secrets.yaml"
KUSTOMIZATION = ROOT / "gitops" / "media" / "kustomization.yaml"
VOLUMES_MANIFEST = ROOT / "gitops" / "media" / "volumes.yaml"

LOCKED_PYTHON_PREFIX = "registry.rupan.dev/upstream/docker.io/library/python@"


def load_docs(path):
    return list(yaml.safe_load_all(path.read_text(encoding="utf-8")))


def soularr_configmap():
    for doc in load_docs(SOULARR_MANIFEST):
        if doc.get("kind") == "ConfigMap" and doc["metadata"]["name"] == "soularr":
            return doc
    raise AssertionError("soularr ConfigMap not found")


def soularr_cronjob():
    for doc in load_docs(SOULARR_MANIFEST):
        if doc.get("kind") == "CronJob" and doc["metadata"]["name"] == "soularr":
            return doc
    raise AssertionError("soularr CronJob not found")


def soularr_container():
    cronjob = soularr_cronjob()
    containers = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"][
        "containers"
    ]
    return next(c for c in containers if c["name"] == "soularr")


class TestConfigTemplate(unittest.TestCase):
    def setUp(self):
        self.template = soularr_configmap()["data"]["config.ini.template"]

    def test_required_sections(self):
        parser = configparser.ConfigParser()
        parser.read_string(self.template)
        for section in ("Lidarr", "Slskd", "Search Settings", "Download Settings"):
            self.assertIn(section, parser.sections())

    def test_service_urls_and_paths(self):
        parser = configparser.ConfigParser()
        parser.read_string(self.template)
        self.assertEqual(parser["Lidarr"]["host_url"], "http://lidarr")
        self.assertEqual(parser["Slskd"]["host_url"], "http://slskd")
        self.assertEqual(parser["Lidarr"]["download_dir"], "/data/downloads/soulseek")
        self.assertEqual(parser["Slskd"]["download_dir"], "/data/downloads/soulseek")

    def test_secrets_are_placeholders_only(self):
        self.assertIn("__LIDARR_API_KEY__", self.template)
        self.assertIn("__SLSKD_API_KEY__", self.template)
        self.assertIsNone(re.search(r"api_key = [0-9a-f]{16,}", self.template))


class TestBootScript(unittest.TestCase):
    def test_run_script_compiles(self):
        source = soularr_configmap()["data"]["run.py"]
        compile(source, "run.py", "exec")

    def test_run_script_pins_upstream_and_renders(self):
        source = soularr_configmap()["data"]["run.py"]
        self.assertIn("0700090ed1c539455b05af9e16ab1a5bf8b9baff", source)
        self.assertIn("soularr.py", source)
        self.assertIn("requirements.txt", source)
        self.assertIn("LIDARR_API_KEY", source)
        self.assertIn("SLSKD_API_KEY", source)
        self.assertIn("--config-dir", source)


class TestCronJobWiring(unittest.TestCase):
    def setUp(self):
        self.cronjob = soularr_cronjob()
        self.container = soularr_container()

    def test_locked_image_and_schedule(self):
        self.assertTrue(self.container["image"].startswith(LOCKED_PYTHON_PREFIX))
        self.assertIn("@sha256:", self.container["image"])
        self.assertIn("schedule", self.cronjob["spec"])

    def test_secret_env_present(self):
        env = {e["name"]: e for e in self.container.get("env", [])}
        for name in ("LIDARR_API_KEY", "SLSKD_API_KEY"):
            self.assertIn(name, env)
            ref = env[name]["valueFrom"]["secretKeyRef"]
            self.assertEqual(ref["name"], "media-app")
            self.assertEqual(ref["key"], name)

    def test_volumes(self):
        pod_spec = self.cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        by_name = {v["name"]: v for v in pod_spec["volumes"]}
        self.assertEqual(by_name["script"]["configMap"]["name"], "soularr")
        self.assertEqual(
            by_name["config"]["persistentVolumeClaim"]["claimName"],
            "soularr-config",
        )
        self.assertEqual(
            by_name["media"]["persistentVolumeClaim"]["claimName"],
            "media-library",
        )
        mounts = {m["mountPath"] for m in self.container["volumeMounts"]}
        self.assertTrue({"/app", "/config", "/data", "/tmp"} <= mounts)

    def test_readonly_nonroot(self):
        security = self.container["securityContext"]
        self.assertTrue(security["readOnlyRootFilesystem"])
        self.assertTrue(security["runAsNonRoot"])


class TestCrossManifestWiring(unittest.TestCase):
    def test_slskd_primary_api_key_env(self):
        for doc in load_docs(DOWNLOAD_MANIFEST):
            if doc.get("kind") != "Deployment":
                continue
            for container in doc["spec"]["template"]["spec"]["containers"]:
                if container["name"] != "slskd":
                    continue
                env = {e["name"]: e for e in container.get("env", [])}
                self.assertIn("SLSKD_API_KEY", env)
                ref = env["SLSKD_API_KEY"]["valueFrom"]["secretKeyRef"]
                self.assertEqual(ref["name"], "media-app")
                self.assertEqual(ref["key"], "SLSKD_API_KEY")
                return
        raise AssertionError("slskd container not found")

    def test_secret_remote_ref(self):
        for doc in load_docs(SECRETS_MANIFEST):
            if doc.get("kind") != "ExternalSecret":
                continue
            if doc["metadata"]["name"] != "media-app":
                continue
            keys = {
                entry["secretKey"]: entry["remoteRef"]["key"]
                for entry in doc["spec"]["data"]
            }
            self.assertEqual(keys.get("SLSKD_API_KEY"), "SLSKD_API_KEY")
            return
        raise AssertionError("media-app ExternalSecret not found")

    def test_kustomization_includes_soularr(self):
        kust = yaml.safe_load(KUSTOMIZATION.read_text(encoding="utf-8"))
        self.assertIn("soularr.yaml", kust["resources"])

    def test_soularr_config_pvc(self):
        names = [
            doc["metadata"]["name"]
            for doc in load_docs(VOLUMES_MANIFEST)
            if doc.get("kind") == "PersistentVolumeClaim"
        ]
        self.assertIn("soularr-config", names)


if __name__ == "__main__":
    unittest.main()
