import subprocess
import tempfile
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


def copy_container() -> dict:
    patches = release()["spec"]["postRenderers"][0]["kustomize"]["patches"]
    deployment = yaml.safe_load(patches[0]["patch"])
    spec = deployment["spec"]["template"]["spec"]
    (container,) = spec["initContainers"]
    return container


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


class CopyScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.data, self.nfs = root / "data", root / "nfs"
        self.data.mkdir()
        self.nfs.mkdir()
        (self.nfs / "auth.db").write_text("users")
        (self.nfs / "cache.db").write_text("messages")
        script = copy_container()["command"][2]
        self.script = script.replace("/data", str(self.data)).replace(
            "/nfs", str(self.nfs)
        )

    def run_copy(self) -> None:
        subprocess.run(["sh", "-euc", self.script], check=True, capture_output=True)

    def test_copies_once_then_leaves_local_data_alone(self) -> None:
        self.run_copy()
        self.assertEqual((self.data / "auth.db").read_text(), "users")
        self.assertEqual((self.data / "cache.db").read_text(), "messages")
        self.assertFalse((self.data / ".incoming").exists())
        (self.data / "cache.db").write_text("newer local messages")
        self.run_copy()
        self.assertEqual((self.data / "cache.db").read_text(), "newer local messages")

    def test_interrupted_copy_is_redone(self) -> None:
        (self.data / ".incoming").mkdir()
        (self.data / ".incoming" / "cache.db").write_text("partial")
        (self.data / "cache.db").write_text("partial")
        self.run_copy()
        self.assertEqual((self.data / "cache.db").read_text(), "messages")
        self.assertTrue((self.data / ".copied-from-nfs").exists())

    def test_old_claim_is_mounted_read_only(self) -> None:
        mounts = {m["mountPath"]: m for m in copy_container()["volumeMounts"]}
        self.assertTrue(mounts["/nfs"]["readOnly"])


if __name__ == "__main__":
    unittest.main()
