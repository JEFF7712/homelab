from __future__ import annotations

import copy
import hashlib
import io
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from scripts.ci.release_mirror import (
    API,
    APIError,
    Redirects,
    asset_matches,
    synchronize,
    tag_commit,
    version,
)

REPOSITORY = "JEFF7712/nix-agent"
COMMIT = "a" * 40


class Forgejo(API):
    def __init__(self) -> None:
        super().__init__("http://forgejo.test", "source-token", "/api/v1")
        self.releases = [
            {
                "id": 1,
                "tag_name": "v0.13.0",
                "name": "v0.13.0",
                "body": "Release notes\n\nDetails.",
                "draft": False,
                "prerelease": False,
                "published_at": "2026-10-14T21:47:41Z",
            }
        ]
        self.assets = [
            {"name": "package.whl", "size": 13},
            {"name": "SHA256SUMS", "size": 9},
        ]
        self.content = {"package.whl": b"package bytes", "SHA256SUMS": b"checksums"}

    def json(
        self, path: str, method: str = "GET", data: dict[str, Any] | None = None
    ) -> Any:
        if method == "GET" and "/tags/" in path:
            return {"commit": {"sha": COMMIT}}
        raise AssertionError((method, path, data))

    def pages(self, path: str, size_key: str = "per_page") -> list[dict[str, Any]]:
        self.assert_size_key(size_key)
        return copy.deepcopy(self.assets if path.endswith("/assets") else self.releases)

    def assert_size_key(self, size_key: str) -> None:
        if size_key != "limit":
            raise AssertionError(size_key)

    def download(
        self, path: str, destination: Path, *, binary_api: bool = False
    ) -> str:
        content = self.content[path.rsplit("/", 1)[1]]
        destination.write_bytes(content)
        return "sha256:" + hashlib.sha256(content).hexdigest()


class GitHub(API):
    def __init__(self) -> None:
        super().__init__("https://api.github.com", "destination-token")
        self.releases: list[dict[str, Any]] = []
        self.assets: list[dict[str, Any]] = []
        self.writes: list[tuple[str, str, Any]] = []
        self.commit = COMMIT
        self.annotated = True
        self.missing_tag = False
        self.fail_upload = False
        self.bad_digest = False

    def pages(self, path: str, size_key: str = "per_page") -> list[dict[str, Any]]:
        return copy.deepcopy(self.assets if path.endswith("/assets") else self.releases)

    def json(
        self, path: str, method: str = "GET", data: dict[str, Any] | None = None
    ) -> Any:
        if method == "GET" and "/git/ref/tags/" in path:
            if self.missing_tag:
                raise APIError(method, path, 404)
            return {
                "object": {
                    "type": "tag" if self.annotated else "commit",
                    "sha": "b" * 40 if self.annotated else self.commit,
                }
            }
        if method == "GET" and "/git/tags/" in path:
            return {"object": {"type": "commit", "sha": self.commit}}
        if method == "GET" and "/releases/tags/" in path:
            tag = urllib.parse.unquote(path.rsplit("/", 1)[1])
            for release in self.releases:
                if release["tag_name"] == tag and not release.get("draft"):
                    return copy.deepcopy(release)
            raise APIError(method, path, 404)
        self.writes.append((method, path, copy.deepcopy(data)))
        if method == "POST" and path.endswith("/releases"):
            release = {"id": 100, **(data or {})}
            self.releases.append(release)
            return copy.deepcopy(release)
        if method == "PATCH" and data is not None:
            release_id = int(path.rsplit("/", 1)[1])
            release = next(r for r in self.releases if r["id"] == release_id)
            release.update(data)
            return copy.deepcopy(release)
        if method == "DELETE":
            self.assets = [
                asset
                for asset in self.assets
                if str(asset["id"]) != path.rsplit("/", 1)[1]
            ]
            return None
        raise AssertionError((method, path, data))

    def upload(
        self, repository: str, release_id: int, name: str, path: Path
    ) -> dict[str, Any]:
        self.writes.append(("UPLOAD", name, None))
        if self.fail_upload:
            raise RuntimeError("Upload unavailable")
        asset = {
            "id": len(self.assets) + 200,
            "name": name,
            "state": "uploaded",
            "size": path.stat().st_size,
            "digest": "sha256:"
            + (
                "0" * 64
                if self.bad_digest
                else hashlib.sha256(path.read_bytes()).hexdigest()
            ),
        }
        self.assets.append(asset)
        return copy.deepcopy(asset)


class MirrorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.forgejo = Forgejo()
        self.github = GitHub()

    def run_sync(self):
        return synchronize(self.forgejo, self.github, REPOSITORY, "0.13.0")

    def test_create_draft_upload_verify_then_publish(self) -> None:
        result = self.run_sync()
        self.assertEqual(
            result,
            [
                {
                    "tag": "v0.13.0",
                    "created": True,
                    "updated": True,
                    "uploaded_assets": 2,
                }
            ],
        )
        self.assertTrue(self.github.writes[0][2]["draft"])
        self.assertEqual(
            [entry[0] for entry in self.github.writes],
            ["POST", "UPLOAD", "UPLOAD", "PATCH"],
        )
        self.assertFalse(self.github.releases[0]["draft"])
        self.assertEqual(
            self.github.releases[0]["body"], self.forgejo.releases[0]["body"]
        )

    def test_published_release_wins_over_a_stray_draft_for_its_tag(self) -> None:
        self.run_sync()
        published = copy.deepcopy(self.github.releases[0])
        self.github.releases.insert(
            0, {**published, "id": 99, "draft": True, "published_at": None}
        )
        self.github.writes.clear()
        result = self.run_sync()
        self.assertEqual(self.github.writes, [])
        self.assertFalse(result[0]["created"])
        self.assertFalse(result[0]["updated"])

    def test_second_run_does_not_write(self) -> None:
        self.run_sync()
        self.github.writes.clear()
        result = self.run_sync()
        self.assertEqual(result[0]["uploaded_assets"], 0)
        self.assertFalse(result[0]["updated"])
        self.assertFalse(self.github.writes)

    def test_lightweight_tag_parity(self) -> None:
        self.github.annotated = False
        self.assertEqual(tag_commit(self.github, REPOSITORY, "v0.13.0"), COMMIT)

    def test_missing_or_divergent_tag_never_creates_release(self) -> None:
        for missing in (False, True):
            with self.subTest(missing=missing):
                self.github.missing_tag = missing
                self.github.commit = "c" * 40
                with self.assertRaises(RuntimeError):
                    self.run_sync()
                self.assertFalse(self.github.writes)

    def test_ignore_drafts_historical_versions_and_nonversion_tags(self) -> None:
        source = self.forgejo.releases[0]
        self.forgejo.releases = [
            source | change
            for change in (
                {"draft": True},
                {"tag_name": "v0.12.0"},
                {"tag_name": "ci-lifecycle-smoke"},
                {"published_at": None},
            )
        ]
        self.assertEqual(self.run_sync(), [])
        self.assertFalse(self.github.writes)

    def test_publish_prerelease_flag_and_notes(self) -> None:
        self.forgejo.releases[0].update(tag_name="v0.14.0-rc.1", prerelease=True)
        self.run_sync()
        self.assertTrue(self.github.releases[0]["prerelease"])

    def test_partial_upload_leaves_draft_then_resumes(self) -> None:
        self.github.fail_upload = True
        with self.assertRaisesRegex(RuntimeError, "Upload unavailable"):
            self.run_sync()
        self.assertTrue(self.github.releases[0]["draft"])
        self.github.fail_upload = False
        self.run_sync()
        self.assertFalse(self.github.releases[0]["draft"])
        self.assertEqual(len(self.github.releases), 1)

    def test_wrong_uploaded_hash_never_publishes(self) -> None:
        self.github.bad_digest = True
        with self.assertRaisesRegex(RuntimeError, "uploaded asset hash differs"):
            self.run_sync()
        self.assertTrue(self.github.releases[0]["draft"])
        self.github.bad_digest = False
        self.run_sync()
        self.assertFalse(self.github.releases[0]["draft"])

    def test_wrong_source_size_never_publishes(self) -> None:
        self.forgejo.assets[0]["size"] = 1
        with self.assertRaisesRegex(RuntimeError, "downloaded size differs"):
            self.run_sync()
        self.assertTrue(self.github.releases[0]["draft"])
        self.assertFalse(self.github.assets)

    def test_existing_published_asset_mismatch_is_preserved(self) -> None:
        self.run_sync()
        before = copy.deepcopy(self.github.assets)
        self.forgejo.content["package.whl"] = b"changed bytes"
        self.github.writes.clear()
        with self.assertRaisesRegex(RuntimeError, "refusing to replace"):
            self.run_sync()
        self.assertEqual(self.github.assets, before)
        self.assertFalse(self.github.writes)

    def test_existing_metadata_follows_source_and_extra_assets_remain(self) -> None:
        self.run_sync()
        self.github.assets.append({"id": 300, "name": "extra.txt"})
        self.forgejo.releases[0].update(
            body="Updated source notes", name="Updated title"
        )
        self.github.writes.clear()
        self.run_sync()
        self.assertEqual([entry[0] for entry in self.github.writes], ["PATCH"])
        self.assertEqual(self.github.releases[0]["body"], "Updated source notes")
        self.assertTrue(
            any(asset["name"] == "extra.txt" for asset in self.github.assets)
        )

    def test_duplicate_and_unsafe_names_fail_before_writes(self) -> None:
        original = copy.deepcopy(self.forgejo.assets)
        for names in (
            ("same", "same"),
            ("../outside", "safe"),
            ("a\\b", "safe"),
            (".", "safe"),
            ("a\nb", "safe"),
        ):
            with self.subTest(names=names):
                self.forgejo.assets = [
                    asset | {"name": name}
                    for asset, name in zip(original, names, strict=True)
                ]
                with self.assertRaisesRegex(RuntimeError, "asset name"):
                    self.run_sync()
                self.assertFalse(self.github.writes)

    def test_invalid_repository_and_floor(self) -> None:
        for repository, minimum in (
            ("../owner/repo", "0.13.0"),
            (REPOSITORY, "latest"),
        ):
            with (
                self.subTest(repository=repository, minimum=minimum),
                self.assertRaises(ValueError),
            ):
                synchronize(self.forgejo, self.github, repository, minimum)


class TransportTest(unittest.TestCase):
    def test_http_errors_do_not_include_response_body_or_token(self) -> None:
        api = API("https://api.github.com", "private-token")
        error = urllib.error.HTTPError(
            "https://api.github.com/asset",
            401,
            "Unauthorized",
            {},
            io.BytesIO(b"sensitive response body"),
        )
        with patch.object(api.opener, "open", side_effect=error):
            with self.assertRaises(APIError) as raised:
                api.json("/asset")
        self.assertEqual(raised.exception.status, 401)
        self.assertNotIn("private-token", str(raised.exception))
        self.assertNotIn("sensitive", str(raised.exception))

    def test_legacy_asset_without_digest_is_downloaded_for_verification(self) -> None:
        api = API("https://api.github.com", "token")
        asset = {"id": 1, "state": "uploaded"}
        with patch.object(api, "download", return_value="sha256:verified") as download:
            self.assertTrue(asset_matches(api, REPOSITORY, asset, "sha256:verified"))
            self.assertTrue(download.call_args.kwargs["binary_api"])

    def test_cross_origin_redirect_drops_authorization(self) -> None:
        request = urllib.request.Request(
            "https://api.github.com/asset", headers={"Authorization": "Bearer secret"}
        )
        redirected = Redirects().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://objects.githubusercontent.com/asset",
        )
        self.assertIsNotNone(redirected)
        self.assertFalse(redirected.has_header("Authorization"))

    def test_same_origin_redirect_preserves_authorization(self) -> None:
        request = urllib.request.Request(
            "http://forgejo.test/asset", headers={"Authorization": "Bearer secret"}
        )
        redirected = Redirects().redirect_request(
            request, None, 302, "Found", {}, "http://forgejo.test/download"
        )
        self.assertEqual(redirected.get_header("Authorization"), "Bearer secret")

    def test_cross_origin_insecure_redirect_fails(self) -> None:
        request = urllib.request.Request("https://api.github.com/asset")
        with self.assertRaisesRegex(RuntimeError, "insecure origin"):
            Redirects().redirect_request(
                request, None, 302, "Found", {}, "http://other.test/asset"
            )

    def test_paginate_source_and_destination(self) -> None:
        for key in ("limit", "per_page"):
            api = API("https://api.github.com", "token")
            with patch.object(
                api,
                "json",
                side_effect=[[{"id": i} for i in range(100)], [{"id": 100}]],
            ) as get:
                self.assertEqual(len(api.pages("/releases", key)), 101)
                self.assertEqual(
                    get.call_args_list[1].args[0], f"/releases?{key}=100&page=2"
                )

    def test_upload_streams_file_with_explicit_size(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "package.whl"
            path.write_bytes(b"package bytes")
            response = Mock(status=201)
            response.read.return_value = b'{"id": 1}'
            connection = Mock()
            connection.getresponse.return_value = response

            def receive(method, url, *, body, headers):
                self.assertEqual(body.read(), b"package bytes")
                self.assertEqual(headers["Content-Length"], "13")
                self.assertIn("name=package.whl", url)

            connection.request.side_effect = receive
            with patch(
                "scripts.ci.release_mirror.http.client.HTTPSConnection",
                return_value=connection,
            ):
                self.assertEqual(
                    API("https://api.github.com", "token").upload(
                        REPOSITORY, 1, path.name, path
                    ),
                    {"id": 1},
                )
            connection.close.assert_called_once()

    def test_download_hashes_stream_and_requests_binary_media(self) -> None:
        api = API("https://api.github.com", "token")
        response = io.BytesIO(b"package bytes")
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(api.opener, "open", return_value=response) as get,
        ):
            digest = api.download("/asset", Path(directory) / "asset", binary_api=True)
            self.assertEqual(
                digest, "sha256:" + hashlib.sha256(b"package bytes").hexdigest()
            )
            self.assertEqual(
                get.call_args.args[0].get_header("Accept"), "application/octet-stream"
            )

    def test_reject_origin_credentials_paths_and_queries(self) -> None:
        for origin in (
            "https://user:password@example.test",
            "https://example.test/path",
            "https://example.test?token=value",
        ):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                API(origin, "token")

    def test_version_tags(self) -> None:
        self.assertEqual(version("v0.13.0"), (0, 13, 0))
        self.assertEqual(version("v0.14.0-rc.1"), (0, 14, 0))
        self.assertIsNone(version("ci-smoke"))


if __name__ == "__main__":
    unittest.main()
