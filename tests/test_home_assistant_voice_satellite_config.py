import unittest
from pathlib import Path

import yaml


class VoiceSatelliteConfigTests(unittest.TestCase):
    def test_mic_volume_overrides_upstream_maximum_default(self) -> None:
        root = Path(__file__).resolve().parents[1]
        manifests = yaml.safe_load_all(
            (root / "gitops/voice/satellite.yaml").read_text()
        )
        manifest = next(item for item in manifests if item["kind"] == "Deployment")
        container = manifest["spec"]["template"]["spec"]["containers"][0]
        args = container["args"]
        volume_index = args.index("--mic-volume")
        self.assertEqual(args[volume_index + 1], "75")

    def test_satellite_probes_use_healthcheck_not_pgrep(self) -> None:
        root = Path(__file__).resolve().parents[1]
        manifests = yaml.safe_load_all(
            (root / "gitops/voice/satellite.yaml").read_text()
        )
        deployment = next(item for item in manifests if item["kind"] == "Deployment")
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        for probe_name in ("startupProbe", "readinessProbe", "livenessProbe"):
            probe = container.get(probe_name)
            self.assertIsNotNone(probe, f"Missing {probe_name}")
            cmd = probe.get("exec", {}).get("command", [])
            self.assertIn("/app/healthcheck.py", cmd)
            self.assertNotIn("pgrep", cmd)

    def test_satellite_patches_configmap_and_mounts(self) -> None:
        root = Path(__file__).resolve().parents[1]
        manifests = list(
            yaml.safe_load_all((root / "gitops/voice/satellite.yaml").read_text())
        )
        configmap = next(item for item in manifests if item["kind"] == "ConfigMap")
        self.assertEqual(configmap["metadata"]["name"], "satellite-patch-code")
        self.assertIn("__main__.py", configmap["data"])
        self.assertIn("satellite.py", configmap["data"])
        self.assertIn("healthcheck.py", configmap["data"])
        for name in ("__main__.py", "satellite.py", "healthcheck.py"):
            self.assertEqual(
                configmap["data"][name],
                (root / "gitops/voice/satellite" / name).read_text(),
                f"Embedded {name} must match the tested source",
            )

        deployment = next(item for item in manifests if item["kind"] == "Deployment")
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        subpaths = [
            m.get("subPath") for m in container["volumeMounts"] if "subPath" in m
        ]
        self.assertIn("__main__.py", subpaths)
        self.assertIn("satellite.py", subpaths)
        self.assertIn("healthcheck.py", subpaths)

    def test_satellite_environment_inhibition_and_stall(self) -> None:
        root = Path(__file__).resolve().parents[1]
        manifests = yaml.safe_load_all(
            (root / "gitops/voice/satellite.yaml").read_text()
        )
        deployment = next(item for item in manifests if item["kind"] == "Deployment")
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        env = {item["name"]: item["value"] for item in container["env"]}
        self.assertIn("ACOUSTIC_TAIL_SECONDS", env)
        self.assertIn("AUDIO_STALL_TIMEOUT", env)
        self.assertIn("HEALTH_PORT", env)
        self.assertEqual(env["ACOUSTIC_TAIL_SECONDS"], "0.35")
        self.assertEqual(env["AUDIO_STALL_TIMEOUT"], "6.0")
        self.assertEqual(env["HEALTH_PORT"], "10202")


if __name__ == "__main__":
    unittest.main()
