import json
import stat
import tempfile
import threading
import unittest
import urllib.parse
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from setup_spotify import REDIRECT_URI, SpotifySetup
from spotify_matrix_streamer import SpotifyClient


class SpotifySetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.setup = SpotifySetup(root / ".env", root / "token.json")
        self.server = HTTPServer(("127.0.0.1", 0), self.setup.handler())
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(3)
        self.directory.cleanup()

    def connect(self) -> requests.Response:
        return requests.post(
            self.url + "/connect",
            data={
                "nonce": self.setup.form_nonce,
                "client_id": "test-id",
                "client_secret": "test-secret",
            },
            allow_redirects=False,
            timeout=3,
        )

    def test_form_rejects_foreign_host_and_missing_nonce(self) -> None:
        response = requests.get(self.url, headers={"Host": "example.org"}, timeout=3)
        self.assertEqual(response.status_code, 400)
        response = requests.post(
            self.url + "/connect",
            data={"client_id": "test-id", "client_secret": "test-secret"},
            timeout=3,
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.setup.env_path.exists())

    def test_redirect_excludes_secret_and_requests_playback_scope(self) -> None:
        response = self.connect()
        self.assertEqual(response.status_code, 303)
        location = response.headers["Location"]
        self.assertNotIn("test-secret", location)
        params = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
        self.assertEqual(params["redirect_uri"], [REDIRECT_URI])
        self.assertEqual(params["scope"], ["user-read-currently-playing"])
        self.assertEqual(params["state"], [self.setup.oauth_state])

    def test_wrong_state_cannot_exchange_code(self) -> None:
        self.connect()
        with patch("setup_spotify.requests.post") as exchange:
            response = requests.get(
                self.url + "/callback",
                params={"code": "test-code", "state": "incorrect"},
                timeout=3,
            )
            self.assertEqual(response.status_code, 400)
            exchange.assert_not_called()
        self.assertFalse(self.setup.complete)

    def test_callback_saves_private_refreshable_credentials(self) -> None:
        self.connect()
        token_response = Mock()
        token_response.json.return_value = {
            "access_token": "test-access",
            "refresh_token": "test-refresh",
            "expires_in": 3600,
        }
        with patch(
            "setup_spotify.requests.post", return_value=token_response
        ) as exchange:
            response = requests.get(
                self.url + "/callback",
                params={"code": "test-code", "state": self.setup.oauth_state},
                timeout=3,
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                exchange.call_args.kwargs["auth"], ("test-id", "test-secret")
            )
        self.assertTrue(self.setup.complete)
        self.assertNotIn("test-secret", response.text)
        self.assertNotIn("test-access", response.text)
        for path in (self.setup.env_path, self.setup.token_path):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        client = SpotifyClient(
            "test-id", "test-secret", REDIRECT_URI, self.setup.token_path
        )
        client._save_token({"access_token": "test-renewed", "expires_in": 3600})
        token = json.loads(self.setup.token_path.read_text())
        self.assertEqual(token["refresh_token"], "test-refresh")
        self.assertEqual(stat.S_IMODE(self.setup.token_path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
