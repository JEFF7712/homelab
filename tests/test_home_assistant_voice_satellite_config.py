import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import yaml

from tests.gitops_render import config_map


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
        configmap = config_map("voice", "satellite-patch-code")
        self.assertEqual(configmap["metadata"]["namespace"], "voice")
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


def load_music_player_tuning() -> dict:
    """Execute only the tuning constants and helper; the module needs hardware."""
    source = (
        Path(__file__).resolve().parents[1] / "gitops/voice/satellite/__main__.py"
    ).read_text()
    tree = ast.parse(source)
    nodes = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "_low_latency_music_player"
        )
        or (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "MUSIC_MPV_OPTIONS"
                for target in node.targets
            )
        )
    ]
    namespace: dict = {"MpvMediaPlayer": object, "_LOGGER": Mock()}
    exec(compile(ast.Module(nodes, []), "__main__.py", "exec"), namespace)
    return namespace


class MusicPlayerBufferingTests(unittest.TestCase):
    def test_music_player_uses_short_mpv_buffers(self) -> None:
        tuning = load_music_player_tuning()
        mpv: dict = {}
        player = SimpleNamespace(_player=SimpleNamespace(_mpv=mpv))
        self.assertIs(tuning["_low_latency_music_player"](player), player)
        self.assertLessEqual(mpv["audio-buffer"], 0.2)
        self.assertLessEqual(mpv["cache-secs"], 2)
        self.assertLessEqual(mpv["demuxer-readahead-secs"], 2)
        self.assertEqual(mpv["demuxer-max-bytes"], "2MiB")

    def test_changed_upstream_layout_keeps_player_and_warns(self) -> None:
        tuning = load_music_player_tuning()
        player = SimpleNamespace()
        self.assertIs(tuning["_low_latency_music_player"](player), player)
        tuning["_LOGGER"].warning.assert_called_once()

    def test_only_music_player_is_tuned(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "gitops/voice/satellite/__main__.py"
        ).read_text()
        calls = {
            keyword.arg: keyword.value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            for keyword in node.keywords
            if keyword.arg in ("music_player", "tts_player")
        }
        wrapper = calls["music_player"]
        self.assertIsInstance(wrapper, ast.Call)
        self.assertEqual(wrapper.func.id, "_low_latency_music_player")
        self.assertEqual(calls["tts_player"].func.id, "MpvMediaPlayer")


if __name__ == "__main__":
    unittest.main()
