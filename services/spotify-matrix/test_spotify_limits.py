import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from spotify_matrix_streamer import SpotifyClient, SpotifyQuotaExceeded


class SpotifyLimitTests(unittest.TestCase):
    def client(self, root: str) -> SpotifyClient:
        client = SpotifyClient(
            "test-id",
            "test-secret",
            "http://127.0.0.1:8888/callback",
            Path(root) / "token.json",
        )
        client.token = {"access_token": "test-access", "expires_at": time.time() + 3600}
        return client

    def test_quota_exhaustion_does_not_retry(self) -> None:
        response = Mock(status_code=429)
        response.json.return_value = {"error": {"reason": "QUOTA_EXCEEDED"}}
        with (
            tempfile.TemporaryDirectory() as root,
            patch(
                "spotify_matrix_streamer.requests.get", return_value=response
            ) as request,
            patch("spotify_matrix_streamer.time.sleep") as sleep,
        ):
            with self.assertRaises(SpotifyQuotaExceeded):
                self.client(root).get_currently_playing()
            self.assertEqual(request.call_count, 1)
            sleep.assert_not_called()

    def test_rate_limit_honors_retry_after(self) -> None:
        response = Mock(status_code=429, headers={"Retry-After": "120"})
        response.json.return_value = {"error": {"message": "Too many requests"}}
        with (
            tempfile.TemporaryDirectory() as root,
            patch(
                "spotify_matrix_streamer.requests.get", return_value=response
            ) as request,
            patch("spotify_matrix_streamer.time.sleep") as sleep,
        ):
            self.assertIsNone(self.client(root).get_currently_playing())
            self.assertEqual(request.call_count, 1)
            sleep.assert_called_once_with(120)

    def test_unauthorized_refresh_retries_only_once(self) -> None:
        response = Mock(status_code=401)
        response.raise_for_status.side_effect = RuntimeError("Unauthorized")
        with (
            tempfile.TemporaryDirectory() as root,
            patch(
                "spotify_matrix_streamer.requests.get", return_value=response
            ) as request,
        ):
            client = self.client(root)
            with patch.object(client, "_refresh_token") as refresh:
                self.assertIsNone(client.get_currently_playing())
                self.assertEqual(request.call_count, 2)
                refresh.assert_called_once()


if __name__ == "__main__":
    unittest.main()
