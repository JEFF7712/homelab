"""Book downloads reach Calibre through the books-handoff scripts.

qBittorrent copies finished books into a handoff folder and a calibre-web
sidecar renames them into the ingest folder, because NFS writes from another
pod never wake Calibre's inotify watcher. These tests run the rendered
scripts and the qBittorrent config init against temporary directories.
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.gitops_render import config_map, rendered

SCRIPTS = config_map("media", "books-handoff")["data"]


def deployment(name: str) -> dict:
    return next(
        d
        for d in rendered("media")
        if d["kind"] == "Deployment" and d["metadata"]["name"] == name
    )


def run_script(name: str, *args: str, env: dict[str, str]) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
        f.write(SCRIPTS[name])
    try:
        return subprocess.run(
            ["sh", f.name, *args],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, **env},
        ).stdout
    finally:
        os.unlink(f.name)


class QbitFinishedHook(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.handoff = self.tmp / "handoff"
        self.env = {"BOOKS_HANDOFF_DIR": str(self.handoff)}

    def handed_off(self) -> list[str]:
        return sorted(p.name for p in self.handoff.iterdir() if p.is_file())

    def test_other_categories_are_ignored(self):
        book = self.tmp / "Dune.epub"
        book.write_text("x")
        run_script("qbit-finished.sh", "tv", str(book), env=self.env)
        self.assertFalse(self.handoff.exists())

    def test_single_file_torrent(self):
        book = self.tmp / "Consider Phlebas.epub"
        book.write_text("x")
        run_script("qbit-finished.sh", "books", str(book), env=self.env)
        self.assertEqual(self.handed_off(), ["Consider Phlebas.epub"])
        self.assertTrue(book.exists(), "the seeding copy must stay in place")

    def test_prefers_epub_and_skips_other_formats(self):
        folder = self.tmp / "Superintelligence by Nick Bostrom EPUB"
        folder.mkdir()
        for name in ("Superintelligence.epub", "Superintelligence.MOBI", "notes.txt"):
            (folder / name).write_text("x")
        run_script("qbit-finished.sh", "books", str(folder), env=self.env)
        self.assertEqual(self.handed_off(), ["Superintelligence.epub"])

    def test_falls_back_to_azw3_then_mobi(self):
        folder = self.tmp / "Foundation"
        folder.mkdir()
        (folder / "Foundation.mobi").write_text("x")
        (folder / "Foundation.azw3").write_text("x")
        run_script("qbit-finished.sh", "books", str(folder), env=self.env)
        self.assertEqual(self.handed_off(), ["Foundation.azw3"])

    def test_collection_hands_off_every_book(self):
        folder = self.tmp / "Culture"
        (folder / "sub").mkdir(parents=True)
        (folder / "Excession.epub").write_text("x")
        (folder / "sub" / "Matter.epub").write_text("x")
        run_script("qbit-finished.sh", "books", str(folder), env=self.env)
        self.assertEqual(self.handed_off(), ["Excession.epub", "Matter.epub"])

    def test_existing_handoff_is_not_overwritten(self):
        self.handoff.mkdir()
        (self.handoff / "Matter.epub").write_text("old")
        book = self.tmp / "Matter.epub"
        book.write_text("new")
        run_script("qbit-finished.sh", "books", str(book), env=self.env)
        self.assertEqual((self.handoff / "Matter.epub").read_text(), "old")


class IngestMover(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.handoff = tmp / "handoff"
        self.ingest = tmp / "ingest"
        (self.handoff / ".staging").mkdir(parents=True)
        self.ingest.mkdir()
        self.env = {
            "BOOKS_HANDOFF_DIR": str(self.handoff),
            "BOOKS_INGEST_DIR": str(self.ingest),
        }

    def test_moves_ready_files_only(self):
        (self.handoff / "Matter.epub").write_text("x")
        (self.handoff / ".staging" / "Partial.epub").write_text("x")
        out = run_script("ingest-mover.sh", "--once", env=self.env)
        self.assertIn("handed off Matter.epub", out)
        self.assertEqual(sorted(p.name for p in self.ingest.iterdir()), ["Matter.epub"])
        self.assertTrue((self.handoff / ".staging" / "Partial.epub").exists())

    def test_name_collision_waits(self):
        (self.handoff / "Matter.epub").write_text("new")
        (self.ingest / "Matter.epub").write_text("old")
        out = run_script("ingest-mover.sh", "--once", env=self.env)
        self.assertIn("skip Matter.epub", out)
        self.assertEqual((self.ingest / "Matter.epub").read_text(), "old")
        self.assertTrue((self.handoff / "Matter.epub").exists())


class QbitConfigInit(unittest.TestCase):
    def run_init(self, root: Path) -> None:
        init = next(
            c
            for c in deployment("qbittorrent")["spec"]["template"]["spec"][
                "initContainers"
            ]
            if c["name"] == "configure-qbittorrent"
        )
        script = init["args"][0].replace("/config-qbit", str(root))
        subprocess.run(["sh", "-c", script], check=True)

    def test_autorun_and_books_category_are_managed(self):
        root = Path(tempfile.mkdtemp())
        conf_dir = root / "qBittorrent"
        conf_dir.mkdir()
        (conf_dir / "qBittorrent.conf").write_text(
            "[AutoRun]\nenabled=false\nprogram=old\n\n[Preferences]\nWebUI\\Port=8080\n"
        )
        (conf_dir / "categories.json").write_text(
            '{"books": {"save_path": "/data/books/ingest"}, "tv": {"save_path": ""}}'
        )
        self.run_init(root)
        self.run_init(root)
        conf = (conf_dir / "qBittorrent.conf").read_text()
        self.assertEqual(conf.count("[AutoRun]"), 1)
        self.assertNotIn("program=old", conf)
        self.assertIn(
            'program=/bin/sh /books-handoff/qbit-finished.sh \\"%L\\" \\"%F\\"', conf
        )
        self.assertIn("WebUI\\Port=8080", conf)
        cats = (conf_dir / "categories.json").read_text()
        self.assertIn('"save_path": "/data/downloads/books"', cats)


class Wiring(unittest.TestCase):
    def test_qbittorrent_mounts_hook(self):
        spec = deployment("qbittorrent")["spec"]["template"]["spec"]
        qbit = next(c for c in spec["containers"] if c["name"] == "qbittorrent")
        self.assertIn("/books-handoff", [m["mountPath"] for m in qbit["volumeMounts"]])

    def test_calibre_sidecar_runs_as_library_owner(self):
        spec = deployment("calibre-web")["spec"]["template"]["spec"]
        mover = next(c for c in spec["containers"] if c["name"] == "ingest-mover")
        ctx = mover["securityContext"]
        self.assertEqual((ctx["runAsUser"], ctx["runAsGroup"]), (911, 1001))
        books = next(m for m in mover["volumeMounts"] if m["name"] == "media")
        self.assertEqual((books["mountPath"], books["subPath"]), ("/books", "books"))


class CalibreDirs(unittest.TestCase):
    def test_dirs_json_has_ingest_processor_keys(self):
        dirs = json.loads(config_map("media", "calibre-web-dirs")["data"]["dirs.json"])
        self.assertEqual(
            dirs,
            {
                "ingest_folder": "/cwa-book-ingest",
                "calibre_library_dir": "/calibre-library",
                "tmp_conversion_dir": "/config/.cwa_conversion_tmp",
            },
        )

    def test_bundled_calibre_is_on_path(self):
        spec = deployment("calibre-web")["spec"]["template"]["spec"]
        cwa = next(c for c in spec["containers"] if c["name"] == "calibre-web")
        env = {e["name"]: e.get("value") for e in cwa["env"]}
        self.assertIn("/app/calibre", env["PATH"].split(":"))
        self.assertIn("/usr/bin", env["PATH"].split(":"))


if __name__ == "__main__":
    unittest.main()
