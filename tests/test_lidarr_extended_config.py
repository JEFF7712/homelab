"""Lidarr extended-scripts config must stay in the shell format hotio expects.

The extended services (`source /config/extended.conf`) execute the file
as bash. An ini-style file breaks every assignment, empties
$enableAutoConfig, and trips ConfValidationCheck, which exits the Audio
(Deezer downloader) and ARLChecker services at boot. Lidarr then searches
indexers forever and downloads nothing, with no loud error.
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIGS_MANIFEST = ROOT / "gitops" / "media" / "configs.yaml"
ARR_MANIFEST = ROOT / "gitops" / "media" / "arr.yaml"

REQUIRED_ASSIGNMENTS = {
    "enableAutoConfig": "false",
    "enableAudio": "true",
    "dlClientSource": "deezer",
    "audioFormat": "native",
    "audioBitrate": "lossless",
    "lidarrUrl": "http://127.0.0.1:8686",
}


def load_docs(path):
    return list(yaml.safe_load_all(path.read_text(encoding="utf-8")))


def extended_configmap():
    for doc in load_docs(CONFIGS_MANIFEST):
        if doc.get("kind") == "ConfigMap" and doc["metadata"]["name"] == (
            "lidarr-extended-config"
        ):
            return doc
    raise AssertionError("lidarr-extended-config ConfigMap not found")


def lidarr_container():
    for doc in load_docs(ARR_MANIFEST):
        if doc.get("kind") != "Deployment":
            continue
        if doc["metadata"]["name"] != "lidarr":
            continue
        containers = doc["spec"]["template"]["spec"]["containers"]
        return next(c for c in containers if c["name"] == "lidarr")
    raise AssertionError("lidarr container not found")


class TestTemplateFormat(unittest.TestCase):
    def setUp(self):
        self.template = extended_configmap()["data"]["extended.conf.template"]

    def test_no_ini_section_headers(self):
        headers = [
            line
            for line in self.template.splitlines()
            if re.match(r"\s*\[.+\]\s*$", line)
        ]
        self.assertEqual(headers, [])

    def test_required_knobs_present(self):
        assignments = {}
        for line in self.template.splitlines():
            match = re.match(r'\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"?([^"]*)"?\s*$', line)
            if match:
                assignments[match.group(1)] = match.group(2)
        for key, expected in REQUIRED_ASSIGNMENTS.items():
            self.assertEqual(
                assignments.get(key),
                expected,
                f"{key} must be {expected!r}",
            )

    def test_secrets_are_placeholders_only(self):
        self.assertIn("__DEEZER_ARL__", self.template)
        self.assertIn("__LIDARR_API_KEY__", self.template)
        self.assertIsNone(re.search(r"arlToken=\"[0-9a-f]{32,}\"", self.template))
        self.assertIsNone(re.search(r"lidarrAPI=\"[0-9a-f]{32,}\"", self.template))

    @unittest.skipUnless(shutil.which("bash"), "bash required")
    def test_template_sources_cleanly_as_bash(self):
        rendered = self.template.replace("__DEEZER_ARL__", "dummy-arl").replace(
            "__LIDARR_API_KEY__", "dummy-key"
        )
        probe = (
            "source /dev/stdin"
            ' && [ -n "$enableAutoConfig" ]'
            ' && [ "$enableAudio" = true ]'
            ' && [ "$dlClientSource" = deezer ]'
            ' && echo OK-"$arlToken"-"$lidarrAPI"'
        )
        proc = subprocess.run(
            ["bash", "-c", probe],
            input=rendered,
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(proc.stderr, "")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "OK-dummy-arl-dummy-key")


class TestRenderScript(unittest.TestCase):
    def test_render_script_compiles(self):
        source = extended_configmap()["data"]["10-render-extended-conf"]
        compile(source, "10-render-extended-conf", "exec")

    def test_render_script_requires_both_secrets(self):
        source = extended_configmap()["data"]["10-render-extended-conf"]
        self.assertIn("DEEZER_ARL", source)
        self.assertIn("LIDARR_API_KEY", source)
        self.assertIn("/config/extended.conf", source)


class TestBootWrapper(unittest.TestCase):
    def test_boot_wrapper_renders_then_starts_services(self):
        source = extended_configmap()["data"]["lidarr-boot.sh"]
        self.assertIn("10-render-extended-conf", source)
        self.assertIn("/config/custom-services.d/Audio", source)
        self.assertIn("/config/custom-services.d/ARLChecker", source)
        self.assertIn("exec /init", source)
        self.assertIn("bash /config/setup.bash", source)

    def test_boot_wrapper_scopes_deemix_config_home(self):
        source = extended_configmap()["data"]["lidarr-boot.sh"]
        self.assertIn("XDG_CONFIG_HOME=/config/xdg nohup", source)
        self.assertNotIn("export XDG_CONFIG_HOME", source)


class TestDeploymentWiring(unittest.TestCase):
    def setUp(self):
        self.container = lidarr_container()
        pod_spec = None
        for doc in load_docs(ARR_MANIFEST):
            if doc.get("kind") == "Deployment" and doc["metadata"]["name"] == "lidarr":
                pod_spec = doc["spec"]["template"]["spec"]
        self.pod_spec = pod_spec

    def test_no_static_extended_conf_mount(self):
        for mount in self.container["volumeMounts"]:
            self.assertNotEqual(mount.get("mountPath"), "/config/extended.conf")

    def test_boot_volume_and_command(self):
        by_name = {v["name"]: v for v in self.pod_spec["volumes"]}
        self.assertIn("extended-boot", by_name)
        self.assertEqual(
            by_name["extended-boot"]["configMap"]["name"],
            "lidarr-extended-config",
        )
        self.assertEqual(
            self.container["command"],
            ["bash", "/tmp/lidarr-extended/lidarr-boot.sh"],
        )

    def test_secret_env_present(self):
        env = {e["name"]: e for e in self.container.get("env", []) if "name" in e}
        for name in ("DEEZER_ARL", "LIDARR_API_KEY"):
            self.assertIn(name, env)
            ref = env[name]["valueFrom"]["secretKeyRef"]
            self.assertEqual(ref["name"], "media-app")
            self.assertEqual(ref["key"], name)


if __name__ == "__main__":
    unittest.main()
