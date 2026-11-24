import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "gitops/observability/ntfy/release.yaml"
STORAGE = ROOT / "gitops/observability/ntfy/local-storage.yaml"
KUSTOMIZATION = ROOT / "gitops/observability/kustomization.yaml"
HOMELAB_05 = ROOT / "flake/hosts/homelab-05/default.nix"


def release() -> dict:
    return next(
        doc
        for doc in yaml.safe_load_all(RELEASE.read_text())
        if doc and doc["kind"] == "HelmRelease"
    )


class LocalClaimTests(unittest.TestCase):
    """SQLite on the nfs-cluster claim stalled ntfy with "database is
    locked" and HTTP 500s after restarts (2026-11-18 to 2026-11-24)."""

    def test_ntfy_uses_the_local_claim_on_a_prepared_host_directory(self) -> None:
        docs = {doc["kind"]: doc for doc in yaml.safe_load_all(STORAGE.read_text())}
        pv, pvc = docs["PersistentVolume"], docs["PersistentVolumeClaim"]
        values = release()["spec"]["values"]
        self.assertEqual(
            values["persistence"]["existingClaim"], pvc["metadata"]["name"]
        )
        self.assertEqual(pvc["spec"]["volumeName"], pv["metadata"]["name"])
        self.assertEqual(pv["spec"]["storageClassName"], "local-nvme")
        self.assertEqual(pv["spec"]["persistentVolumeReclaimPolicy"], "Retain")
        host = pv["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"][0][
            "matchExpressions"
        ][0]["values"]
        self.assertEqual(host, ["homelab-05"])
        path = pv["spec"]["local"]["path"]
        self.assertIn(f'"d {path} 0750 1000 1000 -"', HOMELAB_05.read_text())
        self.assertIn("ntfy/local-storage.yaml", KUSTOMIZATION.read_text())

    def test_retired_nfs_claim_is_unreferenced(self) -> None:
        sources = [RELEASE, KUSTOMIZATION, *RELEASE.parent.glob("*.yaml")]
        for path in sources:
            if path == STORAGE:
                continue
            with self.subTest(path=path.name):
                self.assertNotIn("ntfy-ntfy-data", path.read_text())
                self.assertNotIn("nfs-cluster", path.read_text())


if __name__ == "__main__":
    unittest.main()
