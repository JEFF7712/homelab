"""Offline Kiwix library: the update script and its manifest wiring.

update.py is loaded from the rendered kiwix-update ConfigMap so the tests
track the shipped code. Every test is offline: catalog, metalink and
download are fakes.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from tests.gitops_render import config_map, rendered

ROOT = Path(__file__).resolve().parents[1]
CATALOG_URL = "https://lb.download.kiwix.org/zim"


def load_module() -> dict[str, Any]:
    source = config_map("offline", "kiwix-update")["data"]["update.py"]
    # dataclasses resolves annotations through sys.modules, so the code runs
    # as a registered module rather than in a bare namespace.
    module = types.ModuleType("kiwix_update_under_test")
    sys.modules[module.__name__] = module
    exec(compile(source, "update.py", "exec"), module.__dict__)
    return module.__dict__


MOD = load_module()
Book = MOD["Book"]
UpdateError = MOD["UpdateError"]


def zim_bytes(payload: bytes) -> bytes:
    return (0x044D495A).to_bytes(4, "little") + payload


def feed(*hrefs: str) -> bytes:
    links = "".join(
        f'<entry><link rel="http://opds-spec.org/acquisition/open-access" '
        f'href="{href}"/></entry>'
        for href in hrefs
    )
    return f'<feed xmlns="http://www.w3.org/2005/Atom">{links}</feed>'.encode()


def metalink(
    filename: str, body: bytes, digest: str | None = None, piece: int = 0
) -> bytes:
    digest = digest or hashlib.sha256(body).hexdigest()
    pieces = ""
    if piece:
        hashes = "".join(
            f"<hash>{hashlib.sha1(body[i : i + piece]).hexdigest()}</hash>"
            for i in range(0, len(body), piece)
        )
        pieces = f'<pieces length="{piece}" type="sha-1">{hashes}</pieces>'
    return (
        '<metalink xmlns="urn:ietf:params:xml:ns:metalink">'
        f'<file name="{filename}"><size>{len(body)}</size>'
        f'<hash type="md5">00</hash><hash type="sha-256">{digest}</hash>'
        f"{pieces}</file></metalink>"
    ).encode()


class Archive:
    """A temporary /data tree plus fake catalog, metalink and transfer."""

    def __init__(self, root: Path) -> None:
        self.root = root
        for name in ("zim", "previous", "staging"):
            (root / name).mkdir()
        self.responses: dict[str, bytes] = {}
        self.bodies: dict[str, bytes] = {}
        self.served: dict[str, bytes] = {}
        self.fetched: list[str] = []
        self.repaired: list[tuple[int, int]] = []

    def publish(
        self,
        stem: str,
        date: str,
        body: bytes,
        digest: str | None = None,
        piece: int = 0,
        served: bytes | None = None,
    ):
        """Publish a release; `served` is what a full download returns."""
        filename = f"{stem}_{date}.zim"
        url = f"{CATALOG_URL}/test/{filename}"
        self.responses[f"{url}.meta4"] = metalink(filename, body, digest, piece)
        self.bodies[url] = body
        self.served[url] = body if served is None else served
        return f"{url}.meta4"

    def catalog(self, *meta4: str) -> None:
        self.responses["catalog"] = feed(*meta4)

    def fetch(self, url: str) -> bytes:
        self.fetched.append(url)
        if url.startswith(MOD["CATALOG"]):
            return self.responses["catalog"]
        return self.responses[url]

    def transfer(self, url: str, part: Path, size: int) -> None:
        part.write_bytes(self.served[url][:size])

    def repair(self, url: str, part: Path, offset: int, length: int) -> None:
        self.repaired.append((offset, length))
        with part.open("r+b") as output:
            output.seek(offset)
            output.write(self.bodies[url][offset : offset + length])

    def update(self, book: Any, reserve: int = 0) -> str:
        return MOD["update_book"](
            self.root,
            book,
            fetch=self.fetch,
            transfer=self.transfer,
            repair=self.repair,
            reserve=reserve,
        )

    def names(self, directory: str) -> list[str]:
        return sorted(path.name for path in (self.root / directory).iterdir())


class ReleaseSelectionTest(unittest.TestCase):
    def test_picks_newest_release_of_exact_name_and_flavour(self) -> None:
        book = Book("wikipedia_en_all", "maxi")
        release = MOD["latest_release"](
            book,
            feed(
                f"{CATALOG_URL}/wikipedia/wikipedia_en_all_maxi_2026-02.zim.meta4",
                f"{CATALOG_URL}/wikipedia/wikipedia_en_all_maxi_2026-08.zim.meta4",
                f"{CATALOG_URL}/wikipedia/wikipedia_en_all_nopic_2026-10.zim.meta4",
                f"{CATALOG_URL}/wikipedia/wikipedia_en_all_maxi_extra_2026-11.zim.meta4",
            ),
        )
        self.assertEqual(release.filename, "wikipedia_en_all_maxi_2026-08.zim")
        self.assertEqual(
            release.url, f"{CATALOG_URL}/wikipedia/wikipedia_en_all_maxi_2026-08.zim"
        )

    def test_missing_book_is_an_error(self) -> None:
        with self.assertRaises(UpdateError):
            MOD["latest_release"](Book("devdocs_en_nix"), feed())

    def test_metalink_requires_a_sha256(self) -> None:
        document = (
            b'<metalink xmlns="urn:ietf:params:xml:ns:metalink">'
            b'<file name="a_2026-01.zim"><size>3</size>'
            b'<hash type="md5">00</hash></file></metalink>'
        )
        with self.assertRaises(UpdateError):
            MOD["parse_metalink"](document, "a_2026-01.zim")
        parsed = MOD["parse_metalink"](
            metalink("a_2026-01.zim", b"abc"), "a_2026-01.zim"
        )
        self.assertEqual(parsed.size, 3)
        self.assertEqual(parsed.sha256, hashlib.sha256(b"abc").hexdigest())
        self.assertEqual(parsed.pieces, ())

    def test_metalink_piece_hashes_are_read_only_when_complete(self) -> None:
        body = b"abcdefgh"
        parsed = MOD["parse_metalink"](
            metalink("a_2026-01.zim", body, piece=3), "a_2026-01.zim"
        )
        self.assertEqual(parsed.piece_length, 3)
        self.assertEqual(parsed.pieces[2], hashlib.sha1(b"gh").hexdigest())
        self.assertEqual(parsed.piece(2), (6, 2))
        short = metalink("a_2026-01.zim", body, piece=3).replace(
            f"<hash>{hashlib.sha1(b'gh').hexdigest()}</hash>".encode(), b""
        )
        self.assertEqual(MOD["parse_metalink"](short, "a_2026-01.zim").pieces, ())

    def test_books_manifest_is_valid_and_unique(self) -> None:
        source = config_map("offline", "kiwix-update")["data"]["books.json"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "books.json"
            path.write_text(source)
            books = MOD["load_books"](path)
            self.assertIn("wikipedia_en_all_maxi", {book.stem for book in books})
            path.write_text(json.dumps({"books": [{"name": "a"}, {"name": "a"}]}))
            with self.assertRaises(UpdateError):
                MOD["load_books"](path)


class UpdateBookTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.archive = Archive(Path(directory.name))
        self.book = Book("devdocs_en_nix")

    def test_installs_verified_release_beside_the_served_one(self) -> None:
        (self.archive.root / "zim/devdocs_en_nix_2026-01.zim").write_bytes(
            zim_bytes(b"old")
        )
        body = zim_bytes(b"new")
        self.archive.catalog(self.archive.publish("devdocs_en_nix", "2026-10", body))
        result = self.archive.update(self.book)
        self.assertIn("installed devdocs_en_nix_2026-10.zim", result)
        self.assertEqual(
            self.archive.names("zim"),
            ["devdocs_en_nix_2026-01.zim", "devdocs_en_nix_2026-10.zim"],
        )
        self.assertEqual(
            (self.archive.root / "zim/devdocs_en_nix_2026-10.zim").read_bytes(), body
        )
        self.assertEqual(self.archive.names("staging"), [])

    def test_current_release_downloads_nothing(self) -> None:
        (self.archive.root / "zim/devdocs_en_nix_2026-10.zim").write_bytes(
            zim_bytes(b"x")
        )
        meta4 = self.archive.publish("devdocs_en_nix", "2026-10", zim_bytes(b"x"))
        self.archive.catalog(meta4)
        self.assertIn("current", self.archive.update(self.book))
        self.assertNotIn(meta4, self.archive.fetched)

    def test_checksum_mismatch_keeps_served_release_and_drops_download(self) -> None:
        served = self.archive.root / "zim/devdocs_en_nix_2026-01.zim"
        served.write_bytes(zim_bytes(b"old"))
        self.archive.catalog(
            self.archive.publish(
                "devdocs_en_nix", "2026-10", zim_bytes(b"new"), "0" * 64
            )
        )
        with self.assertRaisesRegex(UpdateError, "SHA-256"):
            self.archive.update(self.book)
        self.assertEqual(self.archive.names("zim"), ["devdocs_en_nix_2026-01.zim"])
        self.assertEqual(self.archive.names("staging"), [])

    def test_damaged_pieces_are_refetched_instead_of_the_whole_file(self) -> None:
        body = zim_bytes(bytes(range(256)) * 4)
        damaged = bytearray(body)
        damaged[600] ^= 0xFF
        self.archive.catalog(
            self.archive.publish(
                "devdocs_en_nix", "2026-10", body, piece=256, served=bytes(damaged)
            )
        )
        self.assertIn("installed", self.archive.update(self.book))
        self.assertEqual(self.archive.repaired, [(512, 256)])
        self.assertEqual(
            (self.archive.root / "zim/devdocs_en_nix_2026-10.zim").read_bytes(), body
        )

    def test_wrong_checksum_with_matching_pieces_is_not_repaired(self) -> None:
        self.archive.catalog(
            self.archive.publish(
                "devdocs_en_nix", "2026-10", zim_bytes(b"new"), "0" * 64, piece=4
            )
        )
        with self.assertRaisesRegex(UpdateError, "SHA-256"):
            self.archive.update(self.book)
        self.assertEqual(self.archive.repaired, [])
        self.assertEqual(self.archive.names("staging"), [])

    def test_non_zim_download_is_rejected(self) -> None:
        self.archive.catalog(
            self.archive.publish("devdocs_en_nix", "2026-10", b"<html>")
        )
        with self.assertRaisesRegex(UpdateError, "not a ZIM"):
            self.archive.update(self.book)
        self.assertEqual(self.archive.names("zim"), [])

    def test_insufficient_space_refuses_before_downloading(self) -> None:
        body = zim_bytes(b"new")
        url = self.archive.publish("devdocs_en_nix", "2026-10", body)
        self.archive.catalog(url)
        usage = SimpleNamespace(free=len(body) + 10)
        with mock.patch.object(MOD["shutil"], "disk_usage", return_value=usage):
            with self.assertRaisesRegex(UpdateError, "GB free"):
                self.archive.update(self.book, reserve=100)
        self.assertEqual(self.archive.names("staging"), [])

    def test_partial_download_of_a_superseded_release_is_removed(self) -> None:
        stale = self.archive.root / "staging/devdocs_en_nix_2026-08.zim.part"
        stale.write_bytes(b"partial")
        other = self.archive.root / "staging/devdocs_en_nix_extra_2026-08.zim.part"
        other.write_bytes(b"other book")
        self.archive.catalog(
            self.archive.publish("devdocs_en_nix", "2026-10", zim_bytes(b"new"))
        )
        self.archive.update(self.book)
        self.assertFalse(stale.exists())
        self.assertTrue(other.exists())


class PageCacheTest(unittest.TestCase):
    """Large downloads must not keep their page cache (OOM-killed 2026-11-22)."""

    def test_download_and_checksum_evict_cache_as_they_go(self) -> None:
        body = zim_bytes(bytes(10 * 1024))
        evicted: list[int] = []

        class Response(io.BytesIO):
            status = 200

        def fadvise(fd: int, offset: int, length: int, advice: int) -> None:
            evicted.append(advice)

        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.dict(MOD, {"FLUSH_EVERY": 2048, "CHUNK": 512}),
            mock.patch.object(
                MOD["urllib"].request, "urlopen", lambda *a, **k: Response(body)
            ),
            mock.patch.object(MOD["os"], "posix_fadvise", fadvise),
        ):
            part = Path(directory) / "a_2026-01.zim.part"
            MOD["download"]("https://example/a_2026-01.zim", part, len(body), 1 << 40)
            self.assertEqual(part.read_bytes(), body)
            downloaded = len(evicted)
            self.assertGreaterEqual(downloaded, len(body) // 2048)
            expected = MOD["Metalink"](len(body), hashlib.sha256(body).hexdigest())
            MOD["verify_file"](part, expected)
            self.assertGreaterEqual(len(evicted) - downloaded, len(body) // 2048)
        self.assertEqual(set(evicted), {MOD["os"].POSIX_FADV_DONTNEED})


class TransferTest(unittest.TestCase):
    def test_short_response_is_resumed_in_the_same_run(self) -> None:
        body = zim_bytes(bytes(1000))
        ranges: list[str] = []

        class Response(io.BytesIO):
            status = 206

        def urlopen(request: Any, timeout: float) -> Response:
            ranges.append(request.get_header("Range"))
            start = int(request.get_header("Range")[6:].split("-")[0])
            return Response(body[start : start + 600])

        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(MOD["urllib"].request, "urlopen", urlopen),
        ):
            part = Path(directory) / "a_2026-01.zim.part"
            MOD["download"]("https://example/a.zim", part, len(body), 1 << 40)
            self.assertEqual(part.read_bytes(), body)
        self.assertEqual(ranges, ["bytes=0-", "bytes=600-"])

    def test_stalled_mirror_is_resumed_and_persistent_failure_raises(self) -> None:
        body = zim_bytes(bytes(1000))
        calls: list[str] = []

        class Response(io.BytesIO):
            status = 206

        def urlopen(request: Any, timeout: float) -> Response:
            calls.append(request.get_header("Range"))
            if len(calls) == 1:
                raise TimeoutError("The read operation timed out")
            return Response(body)

        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(MOD["urllib"].request, "urlopen", urlopen),
            mock.patch.object(MOD["time"], "sleep"),
        ):
            part = Path(directory) / "a_2026-01.zim.part"
            MOD["download"]("https://example/a.zim", part, len(body), 1 << 40)
            self.assertEqual(part.read_bytes(), body)
            self.assertEqual(calls, ["bytes=0-", "bytes=0-"])

            def stalled(request: Any, timeout: float) -> Response:
                raise TimeoutError("The read operation timed out")

            part.unlink()
            with (
                mock.patch.object(MOD["urllib"].request, "urlopen", stalled),
                self.assertRaises(TimeoutError),
            ):
                MOD["download"]("https://example/a.zim", part, len(body), 1 << 40)

    def test_range_fetch_overwrites_one_piece_in_place(self) -> None:
        class Response(io.BytesIO):
            status = 206

        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(
                MOD["urllib"].request, "urlopen", lambda *a, **k: Response(b"XY")
            ),
        ):
            part = Path(directory) / "a.zim.part"
            part.write_bytes(b"abcdef")
            MOD["fetch_range"]("https://example/a.zim", part, 2, 2)
            self.assertEqual(part.read_bytes(), b"abXYef")

    def test_server_checks_are_retried_until_a_lagging_replica_catches_up(
        self,
    ) -> None:
        feed_xml = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
            <id>urn:uuid:aaa</id><tags>_ftindex:no</tags>
            <link type="text/html" href="/content/a_2026-01"/></entry></feed>"""
        attempts: list[str] = []

        def http_get(url: str, timeout: float = 60) -> bytes:
            if "/catalog/" in url:
                return feed_xml
            attempts.append(url)
            if len(attempts) == 1:
                raise MOD["urllib"].error.HTTPError(url, 404, "Not Found", {}, None)
            return b"ok"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "library.xml").write_text('<library><book id="aaa"/></library>')
            with (
                mock.patch.dict(MOD, {"http_get": http_get}),
                mock.patch.object(MOD["time"], "sleep"),
            ):
                self.assertEqual(MOD["verify_server"](root, "http://x", 60), [])
        self.assertEqual(attempts, ["http://x/content/a_2026-01"] * 2)


class LibraryAndRotationTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.archive = Archive(Path(directory.name))
        self.books = [Book("devdocs_en_nix"), Book("wikipedia_en_all", "maxi")]
        for name in (
            "devdocs_en_nix_2026-01.zim",
            "devdocs_en_nix_2026-10.zim",
            "wikipedia_en_all_maxi_2026-08.zim",
            "handmade_2020-01.zim",
        ):
            (self.archive.root / "zim" / name).write_bytes(zim_bytes(b""))

    def test_library_lists_newest_release_of_each_book_and_unclaimed_files(
        self,
    ) -> None:
        files = [
            path.name for path in MOD["library_files"](self.archive.root, self.books)
        ]
        self.assertEqual(
            files,
            [
                "devdocs_en_nix_2026-10.zim",
                "wikipedia_en_all_maxi_2026-08.zim",
                "handmade_2020-01.zim",
            ],
        )

    def test_rotation_keeps_one_previous_release_per_book(self) -> None:
        (self.archive.root / "previous/devdocs_en_nix_2025-06.zim").write_bytes(b"")
        MOD["rotate"](self.archive.root, self.books)
        self.assertEqual(
            self.archive.names("zim"),
            [
                "devdocs_en_nix_2026-10.zim",
                "handmade_2020-01.zim",
                "wikipedia_en_all_maxi_2026-08.zim",
            ],
        )
        self.assertEqual(self.archive.names("previous"), ["devdocs_en_nix_2026-01.zim"])

    def run_finish(self, problems: list[str], failed: list[str]) -> int:
        books = self.archive.root / "books.json"
        books.write_text(
            json.dumps(
                {
                    "books": [
                        {"name": "devdocs_en_nix"},
                        {"name": "wikipedia_en_all", "flavour": "maxi"},
                    ]
                }
            )
        )
        (self.archive.root / MOD["FAILED"]).write_text(json.dumps(failed))
        args = SimpleNamespace(
            root=self.archive.root, books=books, url="http://x", timeout=1
        )
        with mock.patch.dict(MOD, {"verify_server": lambda *_: problems}):
            return MOD["finish"](args)

    def test_failed_verification_keeps_superseded_release_served(self) -> None:
        self.assertEqual(self.run_finish(["missing book"], []), 1)
        self.assertIn("devdocs_en_nix_2026-01.zim", self.archive.names("zim"))
        self.assertEqual(self.archive.names("previous"), [])

    def test_fetch_failure_still_rotates_verified_books_but_fails_the_job(self) -> None:
        self.assertEqual(self.run_finish([], ["wikipedia_en_all_maxi"]), 1)
        self.assertNotIn("devdocs_en_nix_2026-01.zim", self.archive.names("zim"))
        self.assertEqual(self.run_finish([], []), 0)


class CatalogParsingTest(unittest.TestCase):
    def test_reads_ids_content_links_and_full_text_index(self) -> None:
        document = b"""<feed xmlns="http://www.w3.org/2005/Atom">
          <entry><id>urn:uuid:aaa</id>
            <tags>devdocs;_ftindex:no;_pictures:yes</tags>
            <link type="text/html" href="/content/devdocs_en_nix_2026-10"/></entry>
          <entry><id>urn:uuid:bbb</id>
            <tags>archlinux;_pictures:yes;_ftindex:yes</tags>
            <link type="text/html" href="/content/archlinux_en_all_maxi_2026-10"/></entry>
        </feed>"""
        self.assertEqual(
            MOD["catalog_books"](document),
            [
                ("aaa", "/content/devdocs_en_nix_2026-10", False),
                ("bbb", "/content/archlinux_en_all_maxi_2026-10", True),
            ],
        )


class ManifestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.objects = {
            (doc["kind"], doc["metadata"]["name"]): doc for doc in rendered("offline")
        }

    def test_update_job_fetches_rebuilds_then_finishes(self) -> None:
        job = self.objects[("CronJob", "kiwix-update")]["spec"]
        self.assertEqual(job["concurrencyPolicy"], "Forbid")
        pod = job["jobTemplate"]["spec"]["template"]["spec"]
        self.assertEqual(
            [c["name"] for c in pod["initContainers"]], ["fetch", "library"]
        )
        self.assertEqual([c["name"] for c in pod["containers"]], ["finish"])
        self.assertIn("fetch", pod["initContainers"][0]["command"])
        self.assertIn("library-files.txt", pod["initContainers"][1]["command"][-1])
        self.assertIn("finish", pod["containers"][0]["command"])

    def test_server_reads_the_archive_read_only_on_the_lan_vip(self) -> None:
        pod = self.objects[("Deployment", "kiwix")]["spec"]["template"]["spec"]
        server = pod["containers"][0]
        self.assertIn("--monitorLibrary", server["command"])
        mount = next(m for m in server["volumeMounts"] if m["name"] == "archive")
        self.assertTrue(mount["readOnly"])
        service = self.objects[("Service", "kiwix")]
        vip = service["metadata"]["annotations"]["io.cilium/lb-ipam-ips"]
        dns = (ROOT / "inventory/dns.nix").read_text()
        record = re.search(r'name = "kiwix\.homelab";\s*address = "([0-9.]+)";', dns)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.group(1), vip)
        pv = self.objects[("PersistentVolume", "offline-archive-nfs")]["spec"]["nfs"]
        self.assertEqual(pv["path"], "/tank/offline")

    def test_every_image_is_a_mirrored_digest(self) -> None:
        for doc in self.objects.values():
            spec = doc.get("spec", {})
            pod = spec.get(
                "template",
                spec.get("jobTemplate", {}).get("spec", {}).get("template", {}),
            ).get("spec", {})
            for container in pod.get("initContainers", []) + pod.get("containers", []):
                self.assertRegex(
                    container["image"],
                    r"^registry\.rupan\.dev/upstream/.+@sha256:[0-9a-f]{64}$",
                )


if __name__ == "__main__":
    unittest.main()
