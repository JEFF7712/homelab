"""Offline Kiwix library: the update script and its manifest wiring.

update.py is loaded from the rendered kiwix-update ConfigMap so the tests
track the shipped code. Every test is offline: catalog, metalink and
download are fakes.
"""

from __future__ import annotations

import hashlib
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


def metalink(filename: str, body: bytes, digest: str | None = None) -> bytes:
    digest = digest or hashlib.sha256(body).hexdigest()
    return (
        '<metalink xmlns="urn:ietf:params:xml:ns:metalink">'
        f'<file name="{filename}"><size>{len(body)}</size>'
        f'<hash type="md5">00</hash><hash type="sha-256">{digest}</hash>'
        "</file></metalink>"
    ).encode()


class Archive:
    """A temporary /data tree plus fake catalog, metalink and transfer."""

    def __init__(self, root: Path) -> None:
        self.root = root
        for name in ("zim", "previous", "staging"):
            (root / name).mkdir()
        self.responses: dict[str, bytes] = {}
        self.bodies: dict[str, bytes] = {}
        self.fetched: list[str] = []

    def publish(self, stem: str, date: str, body: bytes, digest: str | None = None):
        filename = f"{stem}_{date}.zim"
        url = f"{CATALOG_URL}/test/{filename}"
        self.responses[f"{url}.meta4"] = metalink(filename, body, digest)
        self.bodies[url] = body
        return f"{url}.meta4"

    def catalog(self, *meta4: str) -> None:
        self.responses["catalog"] = feed(*meta4)

    def fetch(self, url: str) -> bytes:
        self.fetched.append(url)
        if url.startswith(MOD["CATALOG"]):
            return self.responses["catalog"]
        return self.responses[url]

    def transfer(self, url: str, part: Path, size: int) -> None:
        part.write_bytes(self.bodies[url][:size])

    def update(self, book: Any, reserve: int = 0) -> str:
        return MOD["update_book"](
            self.root,
            book,
            fetch=self.fetch,
            transfer=self.transfer,
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
