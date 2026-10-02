"""Import-failure hygiene for the Lidarr download chain.

Covers the hygiene script embedded in gitops/media/arr-queue-hygiene.yaml
(loaded from the ConfigMap so tests track the shipped code) plus the
manifest wiring (CronJob, PVC mount, kustomization).
"""

import datetime
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
HYGIENE_MANIFEST = ROOT / "gitops" / "media" / "arr-queue-hygiene.yaml"


def load_module():
    docs = list(yaml.safe_load_all(HYGIENE_MANIFEST.read_text(encoding="utf-8")))
    cm = next(d for d in docs if d["kind"] == "ConfigMap")
    source = cm["data"]["hygiene.py"]
    compile(source, "hygiene.py", "exec")
    ns: dict = {"__name__": "hygiene_under_test"}
    exec(source, ns)
    return ns


MOD = load_module()
UTC = datetime.timezone.utc


def record(
    rid=1,
    state="importFailed",
    completed="2026-09-20T00:00:00Z",
    title="Artist-Album (2024)",
    output="/config/extended/import/X",
):
    return {
        "id": rid,
        "title": title,
        "status": "completed",
        "trackedDownloadState": state,
        "trackedDownloadStatus": "warning",
        "estimatedCompletionTime": completed,
        "outputPath": output,
    }


class TestSelection(unittest.TestCase):
    def test_import_failed_and_old_is_selected(self):
        now = datetime.datetime(2026, 10, 2, tzinfo=UTC)
        got = MOD["select_stale"]([record()], now, 3)
        self.assertEqual(len(got), 1)

    def test_fresh_import_failed_is_kept_for_manual_review(self):
        now = datetime.datetime(2026, 10, 2, tzinfo=UTC)
        rec = record(completed="2026-10-01T12:00:00Z")
        self.assertEqual(MOD["select_stale"]([rec], now, 3), [])

    def test_non_failed_states_ignored(self):
        now = datetime.datetime(2026, 10, 2, tzinfo=UTC)
        recs = [
            record(rid=1, state="imported"),
            record(rid=2, state="downloading"),
            record(rid=3, state="ImportFailed"),
        ]
        got = MOD["select_stale"](recs, now, 3)
        self.assertEqual([r["id"] for r in got], [3])

    def test_missing_timestamp_never_selected(self):
        now = datetime.datetime(2026, 10, 2, tzinfo=UTC)
        rec = record(completed=None)
        self.assertEqual(MOD["select_stale"]([rec], now, 3), [])

    def test_parse_time_rejects_garbage(self):
        self.assertIsNone(MOD["parse_time"]("not-a-date"))
        self.assertIsNone(MOD["parse_time"](None))
        parsed = MOD["parse_time"]("2026-10-01T22:21:00Z")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.tzinfo, UTC)


class TestQuarantinePath(unittest.TestCase):
    def test_import_dir_maps_into_quarantine(self):
        dest = MOD["quarantine_dir"](
            "/config/extended/import/Yandel-DND (2026)", "2026-10-02"
        )
        self.assertEqual(
            dest,
            "/lidarr-config/extended/import-quarantine-2026-10-02/Yandel-DND (2026)",
        )

    def test_other_paths_refused(self):
        self.assertIsNone(MOD["quarantine_dir"]("/data/music/Tool", "2026-10-02"))
        self.assertIsNone(MOD["quarantine_dir"]("/config/extended/import/", "x"))
        self.assertIsNone(MOD["quarantine_dir"]("/config/extended/import/a/b", "x"))
        self.assertIsNone(MOD["quarantine_dir"](None, "x"))


class TestRun(unittest.TestCase):
    def run_hygiene(self, queue_payload, **env):
        calls = []

        def fake_api(method, base, api_key, path, body=None):
            calls.append((method, path, body))
            if method == "GET":
                return 200, queue_payload
            return 202, {}

        mod_api = MOD["api"]
        MOD["api"] = fake_api
        mod_move = MOD["move_to_quarantine"]
        MOD["move_to_quarantine"] = lambda src, dest: None
        mod_env = MOD["os"].environ
        saved = dict(mod_env)
        mod_env.clear()
        mod_env.update({"LIDARR_API_KEY": "k", "RETENTION_DAYS": "3"})
        mod_env.update(env)
        try:
            rc = MOD["run"]()
        finally:
            MOD["api"] = mod_api
            MOD["move_to_quarantine"] = mod_move
            mod_env.clear()
            mod_env.update(saved)
        return rc, calls

    def test_empty_queue_is_ok_without_delete(self):
        rc, calls = self.run_hygiene({"records": [], "totalRecords": 0})
        self.assertEqual(rc, 0)
        self.assertEqual([m for m, _, _ in calls], ["GET"])

    def test_stale_items_deleted_without_blocklist(self):
        payload = {
            "records": [record(rid=7), record(rid=8, state="imported")],
            "totalRecords": 2,
        }
        rc, calls = self.run_hygiene(payload)
        self.assertEqual(rc, 0)
        deletes = [c for c in calls if c[0] == "DELETE"]
        self.assertEqual(len(deletes), 1)
        method, path, body = deletes[0]
        self.assertIn("/api/v1/queue/bulk", path)
        self.assertIn("blocklist=false", path)
        self.assertIn("removeFromClient=false", path)
        self.assertEqual(body, {"ids": [7]})

    def test_api_failure_exits_nonzero(self):
        mod_api = MOD["api"]
        MOD["api"] = lambda *a, **k: (_ for _ in ()).throw(MOD["ApiError"]("down"))
        mod_env = MOD["os"].environ
        saved = dict(mod_env)
        mod_env.clear()
        mod_env.update({"LIDARR_API_KEY": "k"})
        try:
            self.assertEqual(MOD["run"](), 1)
        finally:
            MOD["api"] = mod_api
            mod_env.clear()
            mod_env.update(saved)

    def test_missing_key_fails(self):
        mod_env = MOD["os"].environ
        saved = dict(mod_env)
        mod_env.clear()
        try:
            self.assertEqual(MOD["run"](), 1)
        finally:
            mod_env.update(saved)


class TestManifests(unittest.TestCase):
    def docs(self):
        return list(yaml.safe_load_all(HYGIENE_MANIFEST.read_text(encoding="utf-8")))

    def test_cronjob_wiring(self):
        cj = next(d for d in self.docs() if d["kind"] == "CronJob")
        self.assertEqual(cj["metadata"]["name"], "arr-queue-hygiene")
        self.assertEqual(cj["spec"]["timeZone"], "America/Chicago")
        container = cj["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][
            0
        ]
        self.assertTrue(
            container["image"].startswith(
                "registry.rupan.dev/upstream/docker.io/library/python@sha256:"
            )
        )
        self.assertEqual(container["command"], ["python3", "/app/hygiene.py"])
        self.assertIn({"secretRef": {"name": "media-app"}}, container["envFrom"])
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        mounts = {m["mountPath"]: m["name"] for m in container["volumeMounts"]}
        self.assertEqual(mounts["/lidarr-config"], "lidarr-config")

    def test_pvc_mount_matches_defined_claim(self):
        volumes_yaml = (ROOT / "gitops" / "media" / "volumes.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("name: lidarr-config", volumes_yaml)

    def test_kustomization_includes_manifest(self):
        kust = (ROOT / "gitops" / "media" / "kustomization.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("arr-queue-hygiene.yaml", kust)


if __name__ == "__main__":
    unittest.main()
