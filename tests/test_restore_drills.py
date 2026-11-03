from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BACKUPS = ROOT / "gitops/backups"
RECORD = BACKUPS / "restore-drill/record.sh"


def documents(path: Path) -> list[dict]:
    return [doc for doc in yaml.safe_load_all(path.read_text()) if doc]


def cronjobs() -> dict[str, dict]:
    jobs = {}
    for path in sorted(BACKUPS.glob("*.yaml")):
        for doc in documents(path):
            if doc.get("kind") == "CronJob":
                jobs[doc["metadata"]["name"]] = doc
    return jobs


def pod(job: dict) -> dict:
    return job["spec"]["jobTemplate"]["spec"]["template"]["spec"]


def script(container: dict) -> str:
    return "\n".join(container.get("command", []) + container.get("args", []))


def sentinel_datasets() -> set[str]:
    source = documents(ROOT / "gitops/automation/cluster-sentinel-script.yaml")[0]
    body = source["data"]["cluster-sentinel.sh"]
    match = re.search(r"for item in ([^;]+); do\n\s+drill_ns=", body)
    assert match, "sentinel has no restore drill loop"
    return set(match[1].split())


def recorded(job: dict) -> set[str]:
    """Datasets a drill job records, as namespace/dataset."""
    namespace = job["metadata"]["namespace"]
    names = set()
    for container in pod(job)["containers"]:
        command = container.get("command", [])
        if "/scripts/record.sh" in command:
            names |= {f"{namespace}/{name}" for name in command[3:]}
    return names


class RestoreDrillCoverageTest(unittest.TestCase):
    """Every backup has a drill, and the sentinel watches every drill."""

    def test_every_database_dump_is_restored_by_a_drill(self) -> None:
        jobs = cronjobs()
        dumped = set()
        for job in jobs.values():
            for container in pod(job)["containers"]:
                match = re.search(
                    r"pg_dump .*--file=/dumps/([a-z-]+)-\$stamp", script(container)
                )
                if match:
                    dumped.add(f"backups/{match[1]}-db")
        self.assertTrue(dumped)
        drilled = set().union(*(recorded(job) for job in jobs.values()))
        self.assertEqual(dumped - drilled, set())

    def test_every_restic_repository_is_drilled(self) -> None:
        jobs = cronjobs()
        backed_up = {
            job["metadata"]["namespace"]
            for job in jobs.values()
            for container in pod(job)["containers"]
            if "restic backup" in script(container)
        }
        drilled = {
            job["metadata"]["namespace"]
            for job in jobs.values()
            for container in pod(job).get("initContainers", [])
            if "restic check" in script(container)
        }
        self.assertEqual(backed_up - drilled, set())

    def test_sentinel_watches_exactly_the_drilled_datasets(self) -> None:
        drilled = set().union(*(recorded(job) for job in cronjobs().values()))
        self.assertEqual(sentinel_datasets(), drilled)

    def test_drill_pods_meet_the_restricted_profile(self) -> None:
        for name, job in cronjobs().items():
            if "drill" not in name:
                continue
            spec = pod(job)
            for container in spec.get("initContainers", []) + spec["containers"]:
                with self.subTest(job=name, container=container["name"]):
                    context = container["securityContext"]
                    self.assertTrue(context["runAsNonRoot"])
                    self.assertFalse(context["allowPrivilegeEscalation"])
                    self.assertEqual(context["capabilities"]["drop"], ["ALL"])
                    self.assertIn("limits", container["resources"])


class DrillScratchTest(unittest.TestCase):
    """Non-root drill containers cannot chmod a volume's mount root."""

    def test_no_container_chmods_a_mount_root(self) -> None:
        for name, job in cronjobs().items():
            if "drill" not in name:
                continue
            spec = pod(job)
            for container in spec.get("initContainers", []) + spec["containers"]:
                roots = {m["mountPath"] for m in container.get("volumeMounts", [])}
                for line in script(container).splitlines():
                    if "chmod" not in line:
                        continue
                    targets = set(line.split("chmod", 1)[1].split()[1:])
                    with self.subTest(job=name, container=container["name"]):
                        self.assertEqual(targets & roots, set(), line.strip())


class RecordScriptTest(unittest.TestCase):
    """record.sh writes evidence per dataset and fails on any failure."""

    def run_record(self, results: dict[str, str], datasets: list[str]):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "results").mkdir()
            for name, text in results.items():
                (work / "results" / name).write_text(text + "\n")
            bin_dir = work / "bin"
            bin_dir.mkdir()
            log = work / "kubectl.log"
            kubectl = bin_dir / "kubectl"
            kubectl.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {log}\n')
            kubectl.chmod(0o755)
            result = subprocess.run(
                ["sh", str(RECORD), str(work / "results"), *datasets],
                capture_output=True,
                text=True,
                env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
            )
            patches = [
                json.loads(line.split(" -p ", 1)[1])["data"]
                for line in log.read_text().splitlines()
                if line.startswith("patch ")
            ]
            return result, patches

    def test_passes_record_a_pass_time(self) -> None:
        result, patches = self.run_record(
            {"umami-db": "pass: umami.dump.gz 12 tables 40 rows"}, ["umami-db"]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(patches[0]["umami-db.passed"], patches[0]["umami-db.checked"])
        self.assertTrue(patches[0]["umami-db.result"].startswith("pass"))

    def test_failures_and_missing_results_keep_the_last_pass_and_fail(self) -> None:
        result, patches = self.run_record(
            {"immich-db": "fail: pg_restore: error"}, ["immich-db", "umami-db"]
        )
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("immich-db.passed", patches[0])
        self.assertEqual(patches[1]["umami-db.result"], "fail: no result")
        self.assertNotIn("umami-db.passed", patches[1])


if __name__ == "__main__":
    unittest.main()
