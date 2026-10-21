from __future__ import annotations

import pathlib
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
PRUNE = "kustomize.toolkit.fluxcd.io/prune"


def documents() -> list[tuple[pathlib.Path, dict]]:
    found = []
    for path in sorted((ROOT / "gitops").rglob("*.yaml")):
        if "flux-system" in path.parts:
            continue
        for doc in yaml.safe_load_all(path.read_text()):
            if isinstance(doc, dict) and "kind" in doc and "metadata" in doc:
                found.append((path, doc))
    return found


def holds_state(doc: dict) -> bool:
    kind = doc["kind"]
    if kind in {"PersistentVolumeClaim", "PersistentVolume"}:
        return True
    return kind == "Cluster" and doc["apiVersion"].startswith("postgresql.cnpg.io/")


class FluxPruneProtectionContract(unittest.TestCase):
    """Removing a manifest or a Kustomization must never delete stored data."""

    def test_stateful_objects_and_their_namespaces_are_never_pruned(self) -> None:
        docs = documents()
        stateful_namespaces = {
            doc["metadata"]["namespace"]
            for _, doc in docs
            if holds_state(doc) and "namespace" in doc["metadata"]
        }
        for path, doc in docs:
            metadata = doc["metadata"]
            protected = holds_state(doc) or (
                doc["kind"] == "Namespace" and metadata["name"] in stateful_namespaces
            )
            if not protected:
                continue
            with self.subTest(kind=doc["kind"], name=metadata["name"], path=path):
                annotations = metadata.get("annotations") or {}
                self.assertEqual(annotations.get(PRUNE), "disabled")


if __name__ == "__main__":
    unittest.main()
