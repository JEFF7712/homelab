import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

import yaml

from scripts.navidrome_lookup import (
    Library,
    artist_names,
    clean_title,
    make_handler,
)
from tests.gitops_render import config_map

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "gitops" / "media" / "music.yaml"
NETWORK_POLICY = ROOT / "gitops" / "media" / "network-policy.yaml"
SCRIPT = ROOT / "scripts" / "navidrome_lookup.py"


def genre_tags(*values: str) -> str:
    return json.dumps([{"id": str(i), "value": v} for i, v in enumerate(values)])


def build_library(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE media_file (title TEXT, artist TEXT, album TEXT, "
        "duration REAL, bpm INTEGER, tags TEXT, missing BOOLEAN)"
    )
    rows = [
        (
            "Who Knows",
            "Daniel Caesar",
            "Son Of Spergy",
            226.28,
            None,
            json.dumps(
                {"genre": json.loads(genre_tags("R&B")), "isrc": [{"value": "US1"}]}
            ),
            0,
        ),
        ("Who Knows", "Protoje • Chronixx", "A Matter of Time", 208.4, 0, "{}", 0),
        (
            "Who Knows (feat. Someone)",
            "egobreak",
            "Single",
            227.2,
            140,
            json.dumps({"genre": json.loads(genre_tags("Rap", "Hip Hop"))}),
            0,
        ),
        ("Who Knows", "Daniel Caesar", "Gone", 226.0, 90, "{}", 1),
    ]
    connection.executemany("INSERT INTO media_file VALUES (?,?,?,?,?,?,?)", rows)
    connection.commit()
    connection.close()


class TestMatching(unittest.TestCase):
    def test_clean_title_drops_decorations(self) -> None:
        self.assertEqual(clean_title("Song (feat. X) [Remastered 2011]"), "Song")
        self.assertEqual(clean_title("Song - Radio Edit"), "Song")
        self.assertEqual(clean_title("(Interlude)"), "(Interlude)")

    def test_artist_names_split_collaborations(self) -> None:
        names = artist_names("Protoje • Chronixx feat. Lila Ike")
        self.assertTrue({"protoje", "chronixx", "lilaike"} <= names)


class TestLibrary(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "navidrome.db"
        build_library(self.database)
        self.library = Library(str(self.database))

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_isrc_match_wins(self) -> None:
        match, matched_by = self.library.lookup("us1", "", "", None)
        self.assertEqual(matched_by, "isrc")
        assert match is not None
        self.assertEqual(match.genres, ("R&B",))
        self.assertEqual(match.isrcs, ("US1",))

    def test_title_match_requires_the_same_artist(self) -> None:
        match, matched_by = self.library.lookup("", "Daniel Caesar", "Who Knows", 226)
        self.assertEqual(matched_by, "title")
        assert match is not None
        self.assertEqual(match.album, "Son Of Spergy")

    def test_feature_decoration_and_bpm_survive(self) -> None:
        match, _ = self.library.lookup("", "egobreak", "Who Knows", 227)
        assert match is not None
        self.assertEqual(match.bpm, 140)
        self.assertEqual(match.genres, ("Rap", "Hip Hop"))

    def test_wrong_duration_or_artist_is_no_match(self) -> None:
        self.assertEqual(
            self.library.lookup("", "Daniel Caesar", "Who Knows", 400), (None, None)
        )
        self.assertEqual(
            self.library.lookup("", "Someone Else", "Who Knows", 226), (None, None)
        )

    def test_missing_files_are_ignored(self) -> None:
        match, _ = self.library.lookup("", "Daniel Caesar", "Who Knows", 226)
        assert match is not None
        self.assertNotEqual(match.album, "Gone")

    def test_http_endpoint(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.library))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            query = urlencode({"artist": "Daniel Caesar", "title": "Who Knows"})
            with urllib.request.urlopen(f"{base}/lookup?{query}", timeout=5) as r:
                body = json.load(r)
            self.assertEqual(body["matched_by"], "title")
            self.assertEqual(body["match"]["genres"], ["R&B"])
            with urllib.request.urlopen(f"{base}/healthz", timeout=5) as r:
                self.assertEqual(json.load(r), {"status": "ok"})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(f"{base}/lookup?duration=x", timeout=5)
            self.assertEqual(caught.exception.code, 400)
        finally:
            server.shutdown()
            server.server_close()


class TestManifest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.documents = list(yaml.safe_load_all(MANIFEST.read_text()))

    def _doc(self, kind: str, name: str) -> dict:
        return next(
            d
            for d in self.documents
            if d["kind"] == kind and d["metadata"]["name"] == name
        )

    def test_configmap_matches_script_source(self) -> None:
        # gitops/media/navidrome-lookup/lookup.py is the copy kustomize can
        # load; the rendered ConfigMap must equal the tested source.
        rendered = config_map("media", "navidrome-lookup")
        self.assertEqual(rendered["data"]["lookup.py"], SCRIPT.read_text())

    def test_sidecar_is_read_only_and_non_root(self) -> None:
        pod = self._doc("Deployment", "navidrome")["spec"]["template"]["spec"]
        sidecar = next(c for c in pod["containers"] if c["name"] == "lookup")
        security = sidecar["securityContext"]
        self.assertTrue(security["readOnlyRootFilesystem"])
        self.assertTrue(security["runAsNonRoot"])
        self.assertFalse(security["allowPrivilegeEscalation"])
        self.assertIn("--database", sidecar["args"])
        self.assertEqual(
            sidecar["args"][sidecar["args"].index("--database") + 1],
            "/data/navidrome.db",
        )

    def test_host_network_clients_reach_only_the_lookup_port(self) -> None:
        # Home Assistant is hostNetwork, so the namespace selector in
        # media-isolate-ingress does not admit it.
        policies = list(yaml.safe_load_all(NETWORK_POLICY.read_text()))
        policy = next(
            p
            for p in policies
            if p["kind"] == "CiliumNetworkPolicy"
            and p["metadata"]["name"] == "navidrome-lookup-from-nodes"
        )
        self.assertEqual(
            policy["spec"]["endpointSelector"]["matchLabels"], {"app": "navidrome"}
        )
        (rule,) = policy["spec"]["ingress"]
        self.assertEqual(set(rule["fromEntities"]), {"host", "remote-node"})
        self.assertEqual(
            rule["toPorts"], [{"ports": [{"port": "4534", "protocol": "TCP"}]}]
        )

    def test_lookup_service_is_separate_from_the_public_one(self) -> None:
        service = self._doc("Service", "navidrome-lookup")
        self.assertEqual(service["spec"]["ports"][0]["targetPort"], 4534)
        public = self._doc("Service", "navidrome")
        self.assertNotIn(4534, [p["targetPort"] for p in public["spec"]["ports"]])


if __name__ == "__main__":
    unittest.main()
