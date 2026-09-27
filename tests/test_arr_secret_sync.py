"""Credential sync for the media download chain.

Covers the sync script embedded in gitops/media/arr-secret-sync.yaml
(loaded from the ConfigMap so tests track the shipped code) plus the
manifest wiring (CronJob, ExternalSecret keys, kustomization).
"""

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SYNC_MANIFEST = ROOT / "gitops" / "media" / "arr-secret-sync.yaml"

EXPECTED_SECRET_KEYS = {
    "QBITTORRENT_USERNAME": "QBITTORRENT_USERNAME",
    "QBITTORRENT_PASSWORD": "QBITTORRENT_PASSWORD",
    "SONARR_API_KEY": "SONARR_API_KEY",
    "RADARR_API_KEY": "RADARR_API_KEY",
    "LIDARR_API_KEY": "LIDARR_API_KEY",
    "SEERR_API_KEY": "SEERR_API_KEY",
    "JELLYFIN_SEERR_API_KEY": "JELLYFIN_SEERR_API_KEY",
}


def load_module():
    docs = list(yaml.safe_load_all(SYNC_MANIFEST.read_text(encoding="utf-8")))
    cm = next(d for d in docs if d["kind"] == "ConfigMap")
    source = cm["data"]["sync.py"]
    compile(source, "sync.py", "exec")
    ns: dict = {"__name__": "sync_under_test"}
    exec(source, ns)
    return ns


MOD = load_module()


def qbit_client(cid=1, password="old-pw"):
    return {
        "id": cid,
        "implementation": "QBittorrent",
        "fields": [
            {"name": "host", "value": "qbittorrent.media"},
            {"name": "password", "value": password},
        ],
    }


class TestPayloadHelpers(unittest.TestCase):
    def test_find_qbit_clients_filters_implementation(self):
        clients = [qbit_client(), {"id": 9, "implementation": "Sabnzbd"}]
        self.assertEqual(MOD["find_qbit_clients"](clients), [qbit_client()])

    def test_find_qbit_clients_empty(self):
        self.assertEqual(MOD["find_qbit_clients"]([]), [])

    def test_with_password_replaces_only_password(self):
        updated = MOD["with_password"](qbit_client(), "new-pw")
        values = {f["name"]: f["value"] for f in updated["fields"]}
        self.assertEqual(values["password"], "new-pw")
        self.assertEqual(values["host"], "qbittorrent.media")

    def test_with_password_does_not_mutate_input(self):
        original = qbit_client()
        MOD["with_password"](original, "new-pw")
        self.assertEqual(original["fields"][1]["value"], "old-pw")


class StubApi:
    """Queue-based stub for module-level api()."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, base, api_key, path, body=None):
        self.calls.append((method, path))
        status, payload = self.responses.pop(0)
        return status, payload


class TestArrSync(unittest.TestCase):
    def run_sync(self, responses, **env):
        mod_api = MOD["api"]
        stub = StubApi(responses)
        MOD["api"] = stub
        try:
            out = MOD["sync_arr_download_client"](
                "radarr",
                "http://radarr.media",
                "k",
                env.get("pw", "new-pw"),
                "/api/v3",
            )
        finally:
            MOD["api"] = mod_api
        return out, stub.calls

    def test_healthy_client_skips_put(self):
        problems, calls = self.run_sync([(200, [qbit_client()]), (200, {})])
        self.assertEqual(problems, [])
        methods = [m for m, _ in calls]
        self.assertNotIn("PUT", methods)

    def test_failing_client_is_updated_and_retested(self):
        problems, calls = self.run_sync(
            [
                (200, [qbit_client()]),
                (400, [{"errorMessage": "Unable to connect"}]),
                (202, qbit_client()),
                (200, {}),
            ]
        )
        self.assertEqual(problems, [])
        self.assertIn(("PUT", "/api/v3/downloadclient/1"), calls)

    def test_update_put_failure_reported(self):
        problems, _ = self.run_sync(
            [
                (200, [qbit_client()]),
                (400, [{"errorMessage": "Unable to connect"}]),
                (500, "boom"),
            ]
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("PUT", problems[0])

    def test_still_failing_after_update_reported(self):
        problems, _ = self.run_sync(
            [
                (200, [qbit_client()]),
                (400, [{"errorMessage": "Unable to connect"}]),
                (202, qbit_client()),
                (400, [{"errorMessage": "Unable to connect"}]),
            ]
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("still failing", problems[0])

    def test_missing_qbit_client_reported(self):
        problems, _ = self.run_sync([(200, [{"id": 9}])])
        self.assertEqual(len(problems), 1)
        self.assertIn("no qBittorrent", problems[0])

    def test_unexpected_response_reported(self):
        problems, _ = self.run_sync([(200, {"id": 1})])
        self.assertEqual(len(problems), 1)

    def test_connection_error_raises(self):
        mod_api = MOD["api"]
        MOD["api"] = lambda *a, **k: (_ for _ in ()).throw(MOD["ApiError"]("down"))
        try:
            with self.assertRaises(MOD["ApiError"]):
                MOD["sync_arr_download_client"](
                    "radarr", "http://radarr.media", "k", "pw", "/api/v3"
                )
        finally:
            MOD["api"] = mod_api

    def test_lidarr_uses_v1_api(self):
        roots = {a["name"]: a["api_root"] for a in MOD["ARR_APPS"]}
        self.assertEqual(
            roots, {"sonarr": "/api/v3", "radarr": "/api/v3", "lidarr": "/api/v1"}
        )
        mod_api = MOD["api"]
        stub = StubApi([(200, [qbit_client()]), (200, {})])
        MOD["api"] = stub
        try:
            out = MOD["sync_arr_download_client"](
                "lidarr", "http://lidarr.media", "k", "pw", "/api/v1"
            )
        finally:
            MOD["api"] = mod_api
        self.assertEqual(out, [])
        self.assertIn(("POST", "/api/v1/downloadclient/test"), stub.calls)


class TestSeerrSync(unittest.TestCase):
    def patch_api(self, responses):
        mod_api = MOD["api"]
        stub = StubApi(responses)
        MOD["api"] = stub
        return mod_api, stub

    def test_server_in_sync_skips_post(self):
        mod_api, stub = self.patch_api([(200, [{"apiKey": "k", "name": "Sonarr"}])])
        try:
            out = MOD["sync_seerr_server"]("http://s", "sk", "sonarr", "k")
        finally:
            MOD["api"] = mod_api
        self.assertEqual(out, [])
        self.assertEqual(stub.calls, [("GET", "/api/v1/settings/sonarr")])

    def test_server_key_mismatch_posts_update(self):
        mod_api, stub = self.patch_api(
            [(200, [{"apiKey": "old", "name": "Sonarr"}]), (200, {"ok": True})]
        )
        try:
            out = MOD["sync_seerr_server"]("http://s", "sk", "sonarr", "new")
        finally:
            MOD["api"] = mod_api
        self.assertEqual(out, [])
        posted = stub.calls[1]
        self.assertEqual(posted[0], "POST")

    def test_server_post_failure_reported(self):
        mod_api, _ = self.patch_api([(200, [{"apiKey": "old"}]), (500, "boom")])
        try:
            out = MOD["sync_seerr_server"]("http://s", "sk", "sonarr", "new")
        finally:
            MOD["api"] = mod_api
        self.assertEqual(len(out), 1)

    def test_server_missing_reported(self):
        mod_api, _ = self.patch_api([(200, [])])
        try:
            out = MOD["sync_seerr_server"]("http://s", "sk", "sonarr", "new")
        finally:
            MOD["api"] = mod_api
        self.assertEqual(len(out), 1)

    def test_jellyfin_in_sync(self):
        mod_api, stub = self.patch_api([(200, {"apiKey": "k"})])
        try:
            out = MOD["sync_seerr_jellyfin"]("http://s", "sk", "k")
        finally:
            MOD["api"] = mod_api
        self.assertEqual(out, [])
        self.assertEqual(len(stub.calls), 1)

    def test_jellyfin_update_drops_readonly_fields(self):
        posted = {}
        current = {
            "name": "x",
            "serverId": "y",
            "libraries": [],
            "apiKey": "old",
            "ip": "jellyfin.media",
        }

        def fake_api(method, base, key, path, body=None):
            if method == "POST":
                posted["body"] = body
                return 200, {}
            return 200, current

        mod_api = MOD["api"]
        MOD["api"] = fake_api
        try:
            out = MOD["sync_seerr_jellyfin"]("http://s", "sk", "new")
        finally:
            MOD["api"] = mod_api
        self.assertEqual(out, [])
        self.assertNotIn("name", posted["body"])
        self.assertNotIn("serverId", posted["body"])
        self.assertNotIn("libraries", posted["body"])
        self.assertEqual(posted["body"]["apiKey"], "new")
        self.assertEqual(posted["body"]["ip"], "jellyfin.media")


class TestRun(unittest.TestCase):
    def test_missing_env_fails(self):
        mod_env = MOD["os"].environ
        saved = dict(mod_env)
        mod_env.clear()
        try:
            self.assertEqual(MOD["run"](), 1)
        finally:
            mod_env.update(saved)


class TestManifests(unittest.TestCase):
    def docs(self):
        return list(yaml.safe_load_all(SYNC_MANIFEST.read_text(encoding="utf-8")))

    def test_cronjob_wiring(self):
        cj = next(d for d in self.docs() if d["kind"] == "CronJob")
        self.assertEqual(cj["spec"]["schedule"], "*/15 * * * *")
        self.assertEqual(cj["spec"]["timeZone"], "America/Chicago")
        container = cj["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][
            0
        ]
        self.assertTrue(
            container["image"].startswith(
                "registry.rupan.dev/upstream/docker.io/library/python@sha256:"
            )
        )
        self.assertEqual(container["command"], ["python3", "/app/sync.py"])
        self.assertIn({"secretRef": {"name": "media-app"}}, container["envFrom"])
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])

    def test_kustomization_includes_manifest(self):
        kust = (ROOT / "gitops" / "media" / "kustomization.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("arr-secret-sync.yaml", kust)

    def test_externalsecret_has_keys(self):
        docs = list(
            yaml.safe_load_all(
                (ROOT / "gitops" / "media" / "secrets.yaml").read_text(encoding="utf-8")
            )
        )
        data = next(
            d for d in docs if d.get("metadata", {}).get("name") == "media-app"
        )["spec"]["data"]
        got = {e["secretKey"]: e["remoteRef"]["key"] for e in data}
        for key, remote in EXPECTED_SECRET_KEYS.items():
            self.assertEqual(got.get(key), remote)

    def test_runbook_exists(self):
        text = (ROOT / "docs" / "runbooks" / "arr-credentials.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("QBITTORRENT_PASSWORD", text)


if __name__ == "__main__":
    unittest.main()
