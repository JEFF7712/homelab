import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from scripts.ci import postgres_backup_canary as canary
from scripts.ci.registry_advance import adopt_candidates, ineligible

ROOT = Path(__file__).resolve().parents[1]
OLD, NEW, OTHER = ("sha256:" + ch * 64 for ch in "123")
REPO = canary.REPOSITORY
POLICY = {"postgres_backup_clients": True}


class BackupCanaryTest(TestCase):
    def test_jobs_are_scratch_only_and_use_the_exact_candidate(self):
        for series in canary.SERIES:
            image = f"registry.rupan.dev/{REPO}@{NEW}"
            jobs = canary.render_jobs(ROOT, series, image)
            self.assertEqual(len(jobs), len(canary.SERIES[series]))
            for job in jobs:
                pod = job["spec"]["template"]["spec"]
                self.assertFalse(pod["automountServiceAccountToken"])
                self.assertEqual(pod["initContainers"][0]["image"], image)
                self.assertTrue(
                    all("persistentVolumeClaim" not in vol for vol in pod["volumes"])
                )
                self.assertIn('grep -q "^pass:"', pod["containers"][0]["command"][-1])

    def test_every_matching_restore_job_must_complete(self):
        with TemporaryDirectory() as tmp:
            checkout = Path(tmp)
            shutil.copytree(
                ROOT / canary.ROOT,
                checkout / canary.ROOT,
                copy_function=shutil.copyfile,
            )
            (checkout / canary.ROOT).chmod(0o700)
            result, paths = canary.prepare(checkout, "17", NEW)
            self.assertEqual(result, "stage")
            self.assertEqual(len(paths), 2)
            complete = {
                "status": {"conditions": [{"type": "Complete", "status": "True"}]}
            }
            failed = {"status": {"conditions": [{"type": "Failed", "status": "True"}]}}
            for responses, expected in [
                ([complete, None], "waiting"),
                ([complete, failed], "failed"),
                ([complete, complete], "ready"),
            ]:
                with patch.object(canary, "kubectl_json", side_effect=responses):
                    self.assertEqual(
                        canary.prepare(checkout, "17", NEW), (expected, [])
                    )

    def test_selects_same_series_and_keeps_servers_ineligible(self):
        path = "gitops/backups/home-assistant-db-dump.yaml"
        text = (
            (ROOT / path)
            .read_text()
            .replace(
                "f9cf3e1f0066f0eb0c48707d864f0ac5941e9ff454a072077d97a4d278d07d73",
                OLD.removeprefix("sha256:"),
            )
        )
        lock = {
            "images": [
                {
                    "kind": "upstream",
                    "destination_repository": REPO,
                    "digest": digest,
                    "source": {"tag": tag},
                }
                for digest, tag in [
                    (OLD, "17.12-alpine"),
                    (NEW, "17.13-alpine"),
                    (OTHER, "16.16-alpine"),
                ]
            ]
        }
        candidates = adopt_candidates(
            [(path, text)], {REPO: {NEW, OTHER}}, lock, POLICY
        )
        self.assertEqual([entry[2] for entry in candidates], [NEW])
        server = {
            "kind": "StatefulSet",
            "metadata": {"namespace": "backups", "name": "home-assistant-db-dump"},
        }
        self.assertIsNotNone(
            ineligible(
                REPO,
                "17.12-alpine",
                "17.13-alpine",
                [(path, server)],
                f"{REPO}@{OLD}",
                POLICY,
            )
        )
