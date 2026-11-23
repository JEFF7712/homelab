"""Directory coherence without changing payloads, PC games, or external paths."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "gitops/media/questarr-romm-rescan.mjs"


class RommDirectoryRefreshTests(unittest.TestCase):
    def refresh(self, root):
        return subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                "import {refreshDirectoryTimes} from "
                + json.dumps(MODULE.as_uri())
                + "; console.log(await refreshDirectoryTimes(process.argv[1], new Set(['PC'])));",
                str(root),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_refreshes_console_directories_without_touching_payloads_or_pc(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "library"
            external = Path(directory) / "external"
            for path in (
                root / "nes/game",
                root / "PC/game",
                root / ".hidden",
                external,
            ):
                path.mkdir(parents=True)
            payload = root / "nes/game/2048.nes"
            payload.write_bytes(b"payload")
            (root / "nes/external").symlink_to(external, target_is_directory=True)
            paths = [
                root,
                root / "nes",
                root / "nes/game",
                root / "PC",
                root / "PC/game",
                root / ".hidden",
                external,
                payload,
            ]
            for path in paths:
                os.utime(path, (1, 1))
            result = self.refresh(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "3")
            for path in paths[:3]:
                self.assertGreater(path.stat().st_mtime, 1)
            for path in paths[3:]:
                self.assertEqual(path.stat().st_mtime, 1, str(path))
            self.assertEqual(payload.read_bytes(), b"payload")

    def test_missing_or_symlink_root_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            link = root / "link"
            link.symlink_to(root, target_is_directory=True)
            self.assertNotEqual(self.refresh(root / "missing").returncode, 0)
            result = self.refresh(link)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not a symlink", result.stderr)

    def test_scheduled_exclusions_match_romm_configuration(self):
        import yaml

        documents = list(
            yaml.safe_load_all(
                (ROOT / "gitops/media/questarr-romm-rescan.yaml").read_text()
            )
        )
        container = documents[0]["spec"]["jobTemplate"]["spec"]["template"]["spec"][
            "containers"
        ][0]
        env = {entry["name"]: entry["value"] for entry in container["env"]}
        config = yaml.safe_load((ROOT / "gitops/romm/library-config.yml").read_text())
        self.assertEqual(
            set(json.loads(env["EXCLUDED_PLATFORMS"])),
            set(config["exclude"]["platforms"]),
        )

    def test_scheduled_reader_shares_questarr_node_but_not_its_service_selector(self):
        import yaml

        resources = list(
            yaml.safe_load_all(
                subprocess.check_output(
                    ["kubectl", "kustomize", str(ROOT / "gitops/media")], text=True
                )
            )
        )
        deployment = next(
            r
            for r in resources
            if r["kind"] == "Deployment" and r["metadata"]["name"] == "questarr"
        )
        job = next(
            r
            for r in resources
            if r["kind"] == "CronJob"
            and r["metadata"]["name"] == "questarr-romm-rescan"
        )
        template = job["spec"]["jobTemplate"]["spec"]["template"]
        affinity = template["spec"]["affinity"]["podAffinity"][
            "requiredDuringSchedulingIgnoredDuringExecution"
        ][0]
        self.assertEqual(affinity["topologyKey"], "kubernetes.io/hostname")
        self.assertEqual(affinity["labelSelector"], deployment["spec"]["selector"])
        self.assertNotEqual(template["metadata"]["labels"]["app"], "questarr")

    def test_projected_configmap_symlink_runs_the_cli_instead_of_silently_exiting(self):
        with tempfile.TemporaryDirectory() as directory:
            projected = Path(directory) / "rescan.mjs"
            projected.symlink_to(MODULE)
            result = subprocess.run(
                ["node", str(projected)], text=True, capture_output=True, check=False
            )
            # The local test host lacks Questarr's /app backend. Reaching its import
            # proves the CLI ran; the broken entrypoint silently returned success.
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("RomM library reconciliation failed:", result.stderr)
            self.assertIn("/app/dist/server/romm.js", result.stderr)
