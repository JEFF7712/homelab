from __future__ import annotations

import unittest

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
