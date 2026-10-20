from __future__ import annotations

import pathlib
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENFORCE = "pod-security.kubernetes.io/enforce"
LEVELS = {"restricted", "baseline", "privileged"}
# Each needs host networking, hostPath devices or sockets, privileged
# containers or added capabilities for a concrete workload; see
# docs/runbooks/pod-security.md before adding one.
PRIVILEGED = {
    "automation",
    "home-assistant",
    "media",
    "music-assistant",
    "observability",
    "voice",
}


def namespaces() -> dict[str, dict]:
    found: dict[str, dict] = {}
    for path in sorted((ROOT / "gitops").rglob("*.yaml")):
        if "flux-system" in path.parts:
            continue
        for doc in yaml.safe_load_all(path.read_text()):
            if isinstance(doc, dict) and doc.get("kind") == "Namespace":
                found[doc["metadata"]["name"]] = doc["metadata"].get("labels") or {}
    return found


class PodSecurityContract(unittest.TestCase):
    def test_every_namespace_declares_an_enforced_level(self) -> None:
        for name, labels in namespaces().items():
            with self.subTest(namespace=name):
                self.assertIn(labels.get(ENFORCE), LEVELS)

    def test_privileged_is_limited_to_the_reviewed_exceptions(self) -> None:
        privileged = {
            name
            for name, labels in namespaces().items()
            if labels.get(ENFORCE) == "privileged"
        }
        self.assertEqual(privileged, PRIVILEGED)

    def test_flux_system_is_patched_to_baseline(self) -> None:
        kustomization = yaml.safe_load(
            (
                ROOT / "gitops/clusters/homelab-01/flux-system/kustomization.yaml"
            ).read_text()
        )
        (patch,) = [
            item
            for item in kustomization["patches"]
            if item["target"] == {"kind": "Namespace", "name": "flux-system"}
        ]
        self.assertEqual(
            yaml.safe_load(patch["patch"]),
            [
                {
                    "op": "add",
                    "path": "/metadata/labels/pod-security.kubernetes.io~1enforce",
                    "value": "baseline",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
