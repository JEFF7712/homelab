from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from scripts.ci import chart_advance as ca

OLD = "sha256:" + "1" * 64
NEW = "sha256:" + "2" * 64
RELEASE = f"""apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: alloy
  namespace: observability
spec:
  chart:
    spec:
      chart: alloy
      version: 1.15.0
  postRenderers:
    - kustomize:
        images:
          - name: docker.io/grafana/alloy
            newName: registry.rupan.dev/upstream/docker.io/grafana/alloy  # yamllint disable-line rule:line-length
            digest: {OLD}
"""
DIFF = """diff --git a/gitops/observability/alloy/release.yaml b/gitops/observability/alloy/release.yaml
--- a/gitops/observability/alloy/release.yaml
+++ b/gitops/observability/alloy/release.yaml
@@ -10,1 +10,1 @@
-      version: 1.15.0
+      version: 1.16.0
"""


def deployment(image: str = "docker.io/grafana/alloy:v1.21.3", **pod) -> dict:
    container = {"name": "alloy", "image": image, **pod}
    return {
        "kind": "DaemonSet",
        "metadata": {
            "name": "alloy",
            "namespace": "observability",
            "labels": {"app.kubernetes.io/version": image.rsplit(":", 1)[1]},
        },
        "spec": {"template": {"spec": {"containers": [container]}}},
    }


def crd(properties: dict, required: list[str] | None = None) -> dict:
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return {
        "kind": "CustomResourceDefinition",
        "metadata": {"name": "things.example.io"},
        "spec": {"versions": [{"name": "v1", "schema": {"openAPIV3Schema": schema}}]},
    }


PINNED = {"docker.io/grafana/alloy"}


class ChartBumpTest(unittest.TestCase):
    def test_reads_a_renovate_version_bump_of_one_release(self) -> None:
        bump = ca.chart_bump(7, ["gitops/observability/alloy/release.yaml"], DIFF)
        self.assertEqual(
            bump,
            ca.ChartBump(
                7, "gitops/observability/alloy/release.yaml", "1.15.0", "1.16.0"
            ),
        )

    def test_other_changes_or_files_are_not_a_chart_bump(self) -> None:
        self.assertIsNone(
            ca.chart_bump(7, ["gitops/a/release.yaml", "gitops/b/release.yaml"], DIFF)
        )
        self.assertIsNone(
            ca.chart_bump(7, ["gitops/a/release.yaml"], DIFF + "+      tag: v2\n")
        )


class ClassifyTest(unittest.TestCase):
    def test_a_pinned_image_tag_move_and_labels_are_all_that_may_differ(self) -> None:
        moves, reasons = ca.classify(
            [deployment()], [deployment("docker.io/grafana/alloy:v1.22.0")], PINNED
        )
        self.assertEqual(moves, {"docker.io/grafana/alloy": ("v1.21.3", "v1.22.0")})
        self.assertEqual(reasons, [])

    def test_pod_spec_unpinned_images_and_resource_changes_need_a_person(self) -> None:
        cases = {
            "probe": [deployment(livenessProbe={"timeoutSeconds": 5})],
            "unpinned image": [deployment("quay.io/other/sidecar:v2")],
            "new resource": [
                deployment(),
                {"kind": "ConfigMap", "metadata": {"name": "x"}},
            ],
        }
        for name, new in cases.items():
            with self.subTest(case=name):
                old = (
                    [deployment(livenessProbe={"timeoutSeconds": 1})]
                    if name == "probe"
                    else [deployment()]
                )
                if name == "unpinned image":
                    old = [deployment("quay.io/other/sidecar:v1")]
                _, reasons = ca.classify(old, new, PINNED)
                self.assertTrue(reasons)

    def test_crds_may_only_grow(self) -> None:
        base = crd({"a": {"type": "string", "description": "old words"}})
        grown = crd(
            {
                "a": {"type": "string", "description": "new words"},
                "b": {"type": "string"},
            }
        )
        self.assertEqual(ca.classify([base], [grown], PINNED)[1], [])
        shrunk = crd({})
        self.assertTrue(ca.classify([base], [shrunk], PINNED)[1])
        required = crd({"a": {"type": "string"}}, required=["a"])
        self.assertTrue(ca.classify([base], [required], PINNED)[1])

    def test_regenerated_secrets_and_ca_bundles_are_ignored(self) -> None:
        secret = {
            "kind": "Secret",
            "metadata": {"name": "tls"},
            "data": {"tls.crt": "a"},
        }
        regenerated = copy.deepcopy(secret)
        regenerated["data"]["tls.crt"] = "b"
        self.assertEqual(ca.classify([secret], [regenerated], PINNED)[1], [])


class EditTest(unittest.TestCase):
    def test_bump_moves_version_and_pinned_digest_together(self) -> None:
        bump = ca.ChartBump(7, "release.yaml", "1.15.0", "1.16.0")
        release = ca.helm_release(RELEASE)
        assert release is not None
        pinned = ca.pinned_images(release)
        text = ca.bump_release(RELEASE, bump, pinned, {"docker.io/grafana/alloy": NEW})
        self.assertIn("version: 1.16.0", text)
        self.assertIn(NEW, text)
        self.assertNotIn(OLD, text)
        changes = ca.bump_changes(
            "+++ b/release.yaml\n-      version: 1.15.0\n+      version: 1.16.0\n"
            f"-            digest: {OLD}\n+            digest: {NEW}\n"
        )
        self.assertEqual(changes, ("release.yaml", "1.15.0", "1.16.0", {OLD: NEW}))

    def test_declare_replaces_the_line_for_the_same_image(self) -> None:
        text = "items:\n  - image: docker.io/grafana/alloy:v1.21.3@" + OLD + "\n"
        updated = ca.declare(text, {"x": f"docker.io/grafana/alloy:v1.22.0@{NEW}"})
        self.assertNotIn(OLD, updated)
        self.assertEqual(updated.count("- image:"), 1)
        added = ca.declare(text, {"x": f"docker.io/grafana/loki:3.6@{NEW}"})
        self.assertEqual(added.count("- image:"), 2)

    def test_the_nearest_declaration_file_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            area = root / "gitops" / "platform"
            (area / "cnpg").mkdir(parents=True)
            (area / "registry-sources.yaml").write_text("items: []\n")
            self.assertEqual(
                ca.declaration_file(root, "gitops/platform/cnpg/release.yaml"),
                area / "registry-sources.yaml",
            )
            (area / "cnpg" / "registry-sources.yaml").write_text("items: []\n")
            self.assertEqual(
                ca.declaration_file(root, "gitops/platform/cnpg/release.yaml"),
                area / "cnpg" / "registry-sources.yaml",
            )


class PolicyAndSettleTest(unittest.TestCase):
    def test_policy_holds_named_charts_denied_and_windowed_namespaces(self) -> None:
        release = ca.helm_release(RELEASE)
        assert release is not None
        self.assertIsNone(ca.policy_hold(release, {}))
        self.assertIn(
            "held",
            ca.policy_hold(release, {"hold_charts": ["observability/alloy"]}) or "",
        )
        self.assertIn(
            "denied",
            ca.policy_hold(release, {"deny_namespaces": ["observability"]}) or "",
        )
        self.assertIn(
            "01:00",
            ca.policy_hold(
                release,
                {"held_namespaces": {"observability": "adopts only between 01:00"}},
            )
            or "",
        )
        release["spec"]["valuesFrom"] = [{"kind": "ConfigMap", "name": "values"}]
        self.assertIn("other objects", ca.policy_hold(release, {}) or "")

    def test_a_bump_is_verified_only_when_ready_at_its_version_on_the_new_digest(
        self,
    ) -> None:
        def live(revision: str, status: str, reason: str = "") -> dict:
            return {
                "status": {
                    "lastAttemptedRevision": revision,
                    "conditions": [
                        {"type": "Ready", "status": status, "reason": reason}
                    ],
                }
            }

        image = "registry.rupan.dev/upstream/docker.io/grafana/alloy@"
        moved = {OLD: NEW}
        rolled = [(image + NEW, True), (image + NEW, True)]
        # Pods decide, because the schedule cannot read HelmReleases.
        self.assertEqual(
            ca.settle_verdict(None, "1.16.0", rolled, moved)[0], "verified"
        )
        midway = [(image + NEW, True), (image + OLD, True)]
        self.assertEqual(ca.settle_verdict(None, "1.16.0", midway, moved)[0], "waiting")
        starting = [(image + NEW, False)]
        self.assertEqual(
            ca.settle_verdict(None, "1.16.0", starting, moved)[0], "waiting"
        )
        # A readable failed upgrade fails at once, even with old pods left.
        self.assertEqual(
            ca.settle_verdict(
                live("1.16.0", "False", "UpgradeFailed"), "1.16.0", midway, moved
            )[0],
            "failed",
        )
        # A failure of the previous version is not this bump's.
        self.assertEqual(
            ca.settle_verdict(
                live("1.15.0", "False", "UpgradeFailed"), "1.16.0", rolled, moved
            )[0],
            "verified",
        )


class SettleBumpsTest(unittest.TestCase):
    def test_a_failed_bump_is_reverted_and_a_waiting_one_holds_its_release(
        self,
    ) -> None:
        import datetime
        from unittest.mock import patch

        now = 1_800_000_000.0
        bumped = RELEASE.replace("version: 1.15.0", "version: 1.16.0").replace(OLD, NEW)
        diff = (
            "+++ b/gitops/observability/alloy/release.yaml\n"
            "-      version: 1.15.0\n+      version: 1.16.0\n"
            f"-            digest: {OLD}\n+            digest: {NEW}\n"
        )
        merged = datetime.datetime.fromtimestamp(now - 600, datetime.UTC).isoformat()
        pull = {
            "number": 9,
            "head": {"ref": ca.BUMP_BRANCH + "abc"},
            "merged_at": merged,
        }
        comments: list[dict] = []

        def api(token, path, data=None, raw=False):
            if path.endswith(".diff"):
                return diff
            if data is not None:
                comments.append(data)
            return []

        for status, reason, expected in (
            ("False", "UpgradeFailed", "reverted"),
            ("Unknown", "Progressing", None),
        ):
            with (
                self.subTest(reason=reason),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                path = root / "gitops/observability/alloy/release.yaml"
                path.parent.mkdir(parents=True)
                path.write_text(bumped)
                published: list[tuple] = []
                live = {
                    "status": {
                        "lastAttemptedRevision": "1.16.0",
                        "conditions": [
                            {"type": "Ready", "status": status, "reason": reason}
                        ],
                    }
                }
                with (
                    patch.object(ca, "kubectl_json", return_value=live),
                    patch.object(ca, "running_images", return_value=[]),
                ):
                    outcome, busy = ca.settle_bumps(
                        root,
                        "token",
                        [pull],
                        now,
                        api,
                        lambda *args: published.append(args),
                    )
                self.assertEqual(outcome, expected)
                if expected == "reverted":
                    self.assertEqual(path.read_text(), RELEASE)
                    self.assertEqual(published[0][1], ca.REVERT_BRANCH + "9")
                    self.assertEqual(busy, set())
                else:
                    self.assertEqual(path.read_text(), bumped)
                    self.assertEqual(published, [])
                    self.assertEqual(busy, {"observability/alloy"})


if __name__ == "__main__":
    unittest.main()
