"""Recover real backup artifacts and reject incomplete inputs."""

import gzip
import importlib.util
import os
import sqlite3
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(app):
    spec = importlib.util.spec_from_file_location(
        f"{app}_backup", ROOT / "gitops" / app / "backup.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PersonalAppsBackupTests(unittest.TestCase):
    def test_karakeep_recovers_committed_wal_data_and_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets = root / "assets"
            assets.mkdir()
            (assets / "saved.html").write_text("Archived page")
            database = root / "db.db"
            # Keep this connection open: the committed row lives in the WAL.
            with sqlite3.connect(database) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("CREATE TABLE bookmarks (url TEXT)")
                connection.execute(
                    "INSERT INTO bookmarks VALUES ('https://example.com')"
                )
                connection.commit()
                archive = load("karakeep").backup(database, assets, root / "backups")
                restored = root / "restored"
                with tarfile.open(archive) as source:
                    source.extractall(restored, filter="data")
                with sqlite3.connect(restored / "db.db") as recovered:
                    self.assertEqual(
                        recovered.execute("SELECT url FROM bookmarks").fetchall(),
                        [("https://example.com",)],
                    )
                self.assertEqual(
                    (restored / "assets/saved.html").read_text(), "Archived page"
                )

    def test_failed_karakeep_backup_preserves_previous_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / "karakeep-old.tar.gz"
            old.write_bytes(b"previous backup")
            os.utime(old, (0, 0))
            with self.assertRaises(FileNotFoundError):
                load("karakeep").backup(root / "missing.db", root / "assets", root)
            self.assertEqual(old.read_bytes(), b"previous backup")

    def test_romm_archive_contains_sql_and_saves_but_not_rom_library(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            for name in ("assets", "config", "library"):
                (state / name).mkdir(parents=True)
            (state / "assets/save.sav").write_bytes(b"save state")
            (state / "library/game.rom").write_bytes(b"bulk ROM")
            dumps = root / "dumps"
            dumps.mkdir()
            sql = b"CREATE TABLE roms (id INT);\n-- Dump completed on today\n"
            with gzip.open(dumps / "20261121.sql.gz", "wb") as output:
                output.write(sql)
            archive = load("romm").backup(state, dumps)
            with tarfile.open(archive) as source:
                self.assertIn("assets/save.sav", source.getnames())
                self.assertNotIn("library/game.rom", source.getnames())
                self.assertEqual(
                    gzip.decompress(source.extractfile("database.sql.gz").read()), sql
                )

    def test_romm_rejects_truncated_or_stale_dump(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("assets", "config"):
                (root / name).mkdir()
            dump = root / "20261121.sql.gz"
            with gzip.open(dump, "wb") as output:
                output.write(b"CREATE TABLE roms (id INT);")
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                load("romm").backup(root, root)
            os.utime(dump, (time.time() - 7200,) * 2)
            with self.assertRaisesRegex(RuntimeError, "fresh"):
                load("romm").backup(root, root)
            self.assertFalse(list(root.glob("romm-*.tar.gz")))


class PersonalAppsStartupTests(unittest.TestCase):
    def rendered(self, app):
        import subprocess

        import yaml

        return list(
            yaml.safe_load_all(
                subprocess.check_output(
                    ["kubectl", "kustomize", str(ROOT / "gitops" / app)], text=True
                )
            )
        )

    def test_romm_service_environment_cannot_replace_nginx_listen_port(self):
        docs = self.rendered("romm")
        deployment = next(
            d
            for d in docs
            if d["kind"] == "Deployment" and d["metadata"]["name"] == "romm"
        )
        pod = deployment["spec"]["template"]["spec"]
        container = pod["containers"][0]
        # Reproduce the service link Kubernetes injects when not disabled.
        effective = {"ROMM_PORT": "tcp://10.43.167.168:80"}
        effective.update(
            {v["name"]: v["value"] for v in container["env"] if "value" in v}
        )
        self.assertEqual(
            int(effective["ROMM_PORT"]), container["ports"][0]["containerPort"]
        )
        self.assertFalse(pod["enableServiceLinks"])

    def test_meilisearch_minor_update_requires_manual_database_migration(self):
        import json

        from scripts.ci.registry_advance import ineligible

        policy = json.loads((ROOT / "config/registry/advance-policy.json").read_text())
        docs = self.rendered("karakeep")
        deployment = next(
            d
            for d in docs
            if d["kind"] == "Deployment" and d["metadata"]["name"] == "meilisearch"
        )
        reference = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
        reason = ineligible(
            "upstream/docker.io/getmeili/meilisearch",
            "v1.55.0",
            "v1.57.0",
            [("gitops/karakeep/search.yaml", deployment)],
            reference,
            policy,
        )
        self.assertEqual(reason, "repository is denied")
