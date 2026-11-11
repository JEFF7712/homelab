from __future__ import annotations

import json
import pathlib
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from scripts.registry import advance
from scripts.registry.advance import (
    added_sources,
    adoptions,
    merge_lock,
    select,
    workloads,
)

OLD = "sha256:" + "1" * 64
NEW = "sha256:" + "2" * 64
INIT = "sha256:" + "3" * 64
REPOSITORY = "upstream/lscr.io/linuxserver/radarr"
MIRROR = "registry.rupan.dev/" + REPOSITORY

DIFF = f"""diff --git a/gitops/media/registry-sources.yaml b/gitops/media/registry-sources.yaml
--- a/gitops/media/registry-sources.yaml
+++ b/gitops/media/registry-sources.yaml
@@ -28,1 +28,1 @@ items:
-  - image: lscr.io/linuxserver/radarr:6.3.0@{OLD}  # yamllint disable-line
+  - image: lscr.io/linuxserver/radarr:6.4.4@{NEW}  # yamllint disable-line
diff --git a/gitops/media/arr.yaml b/gitops/media/arr.yaml
--- a/gitops/media/arr.yaml
+++ b/gitops/media/arr.yaml
@@ -1,1 +1,1 @@
+          image: docker.io/library/busybox:1@{INIT}
"""


class MirrorStepTest(unittest.TestCase):
    def test_only_lines_added_to_declarations_count(self) -> None:
        self.assertEqual(
            added_sources(DIFF), {f"lscr.io/linuxserver/radarr:6.4.4@{NEW}"}
        )

    def test_selects_only_new_upstream_records_for_added_sources(self) -> None:
        source = f"lscr.io/linuxserver/radarr:6.4.4@{NEW}"
        inventory = {
            "images": [
                {"id": "new", "kind": "upstream", "source": {"reference": source}},
                {"id": "old", "kind": "upstream", "source": {"reference": source}},
                {"id": "unrelated", "kind": "upstream", "source": {"reference": "x"}},
                {"id": "app", "kind": "first-party", "source": {"reference": source}},
            ]
        }
        chosen = select(inventory, {"old"}, {source})
        self.assertEqual([record["id"] for record in chosen], ["new"])

    def test_lock_merge_never_repoints_a_held_tag(self) -> None:
        lock = {
            "images": [
                {
                    "id": "b-old",
                    "destination_repository": REPOSITORY,
                    "destination_tags": ["6.4", "retention-deployed-1"],
                    "digest": OLD,
                }
            ]
        }
        record = {
            "id": "a-new",
            "destination_repository": REPOSITORY,
            "destination_tags": ["6.4", "retention-deployed-2"],
            "digest": NEW,
        }
        self.assertEqual(merge_lock(lock, [record, record]), ["a-new"])
        self.assertEqual([entry["id"] for entry in lock["images"]], ["a-new", "b-old"])
        self.assertEqual(
            lock["images"][0]["destination_tags"], ["retention-deployed-2"]
        )


class AdoptStepTest(unittest.TestCase):
    def files(self, digest: str) -> list[tuple[str, str]]:
        return [
            ("gitops/media/arr.yaml", f"image: {MIRROR}@{digest}\n"),
            ("gitops/media/registry-sources.yaml", f"image: {MIRROR}@{OLD}\n"),
        ]

    def test_rewrites_a_consumer_to_its_single_locked_declaration(self) -> None:
        rewrites, skipped = adoptions(
            self.files(OLD), {REPOSITORY: {NEW}}, {REPOSITORY: {OLD, NEW}}
        )
        self.assertEqual(
            rewrites,
            {"gitops/media/arr.yaml": {f"{MIRROR}@{OLD}": f"{MIRROR}@{NEW}"}},
        )
        self.assertEqual(skipped, [])

    def test_consumers_already_on_a_declared_digest_are_left_alone(self) -> None:
        rewrites, skipped = adoptions(
            self.files(NEW), {REPOSITORY: {NEW}}, {REPOSITORY: {NEW}}
        )
        self.assertEqual((rewrites, skipped), ({}, []))

    def test_unlocked_or_ambiguous_declarations_are_reported_not_guessed(self) -> None:
        for current, locked in (
            ({NEW}, {OLD}),
            ({NEW, INIT}, {NEW, INIT}),
        ):
            with self.subTest(current=current):
                rewrites, skipped = adoptions(
                    self.files(OLD), {REPOSITORY: current}, {REPOSITORY: locked}
                )
                self.assertEqual(rewrites, {})
                self.assertEqual(len(skipped), 1)


class ScheduledRunTest(unittest.TestCase):
    """A CronJob is checked by its next scheduled run, never run off schedule."""

    OLD = "sha256:" + "1" * 64
    NEW = "sha256:" + "2" * 64
    IMAGE = "registry.rupan.dev/upstream/ghcr.io/example/tool"
    SINCE = 1794300000.0

    def job(self, name, digest, created, condition=None, owner="sweep"):
        stamp = datetime.fromtimestamp(created, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "metadata": {
                "name": name,
                "creationTimestamp": stamp,
                "ownerReferences": [{"kind": "CronJob", "name": owner}],
            },
            "spec": {
                "template": {
                    "spec": {"containers": [{"image": f"{self.IMAGE}@{digest}"}]}
                }
            },
            "status": {
                "conditions": [{"type": condition, "status": "True"}]
                if condition
                else []
            },
        }

    def check(self, jobs):
        manifest = (
            "apiVersion: batch/v1\nkind: CronJob\nmetadata:\n  name: sweep\n"
            "  namespace: media\nspec:\n  schedule: '0 * * * *'\n  jobTemplate:\n"
            "    spec:\n      template:\n        spec:\n          containers:\n"
            f"            - name: sweep\n              image: {self.IMAGE}@{self.NEW}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "cron.yaml").write_text(manifest)
            listing = subprocess.CompletedProcess(
                [], 0, stdout=json.dumps({"items": jobs})
            )
            with patch.object(advance.subprocess, "run", return_value=listing):
                return advance.scheduled_runs(root, ["cron.yaml"], self.SINCE)

    def test_no_run_on_the_new_image_yet_waits(self) -> None:
        after = self.SINCE + 60
        for jobs in (
            [],
            # A run still on the old image (before Flux applied the change).
            [self.job("a", self.OLD, after, "Complete")],
            # A run on the new image from before this adopt merged.
            [self.job("b", self.NEW, self.SINCE - 60, "Complete")],
            # Another CronJob's run.
            [self.job("c", self.NEW, after, "Complete", owner="other")],
        ):
            with self.subTest(jobs=[job["metadata"]["name"] for job in jobs]):
                failures, waiting = self.check(jobs)
                self.assertEqual(failures, [])
                self.assertIn("no scheduled run", waiting[0])

    def test_a_running_job_waits(self) -> None:
        failures, waiting = self.check([self.job("a", self.NEW, self.SINCE + 60)])
        self.assertEqual(failures, [])
        self.assertIn("still running", waiting[0])

    def test_a_completed_run_verifies_and_a_failed_one_fails(self) -> None:
        after = self.SINCE + 60
        self.assertEqual(
            self.check([self.job("a", self.NEW, after, "Complete")]), ([], [])
        )
        failures, waiting = self.check([self.job("a", self.NEW, after, "Failed")])
        self.assertIn("media/cronjob/sweep", failures[0])
        self.assertEqual(waiting, [])
        # A later run that completed outweighs an earlier failure.
        self.assertEqual(
            self.check(
                [
                    self.job("a", self.NEW, after, "Failed"),
                    self.job("b", self.NEW, after + 3600, "Complete"),
                ]
            ),
            ([], []),
        )


class VerifyStepTest(unittest.TestCase):
    def test_workloads_carry_every_mirror_digest_including_init_containers(
        self,
    ) -> None:
        manifest = f"""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: radarr
  namespace: media
spec:
  template:
    spec:
      initContainers:
        - image: registry.rupan.dev/upstream/docker.io/library/busybox@{INIT}
      containers:
        - image: {MIRROR}@{NEW}
---
apiVersion: v1
kind: Service
metadata:
  name: radarr
"""
        self.assertEqual(
            workloads(manifest), [("Deployment", "media", "radarr", {NEW, INIT})]
        )


if __name__ == "__main__":
    unittest.main()
