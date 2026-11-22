"""Publish a checked online SQLite backup and archived bookmark assets."""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path


def backup(database: Path, assets: Path, destination: Path) -> Path:
    """Retain seven days only after a complete, validated archive is published."""
    if not database.is_file() or not assets.is_dir():
        raise FileNotFoundError("Karakeep database and assets must both exist")
    destination.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    archive = destination / f"karakeep-{stamp}.tar.gz"
    deadline = time.monotonic() + 300

    def progress(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() > deadline:
            raise TimeoutError("SQLite online backup exceeded five minutes")

    with tempfile.TemporaryDirectory() as temporary:
        snapshot = Path(temporary) / "db.db"
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as source:
            with sqlite3.connect(snapshot) as target:
                source.backup(target, pages=256, progress=progress)
                result = target.execute("PRAGMA integrity_check").fetchall()
                if result != [("ok",)]:
                    raise RuntimeError(f"Backup integrity failed: {result}")
        pending = archive.with_suffix(".tmp")
        try:
            with tarfile.open(pending, "w:gz") as output:
                output.add(snapshot, arcname="db.db")
                output.add(assets, arcname="assets")
            with tarfile.open(pending, "r:gz") as check:
                if not {"db.db", "assets"}.issubset(check.getnames()):
                    raise RuntimeError("Backup archive is incomplete")
                # Reading every file checks gzip/truncation, not just tar headers.
                for member in check:
                    if member.isfile():
                        stream = check.extractfile(member)
                        assert stream is not None
                        while stream.read(1024 * 1024):
                            pass
            os.replace(pending, archive)
        finally:
            pending.unlink(missing_ok=True)
    cutoff = time.time() - 7 * 86400
    for previous in destination.glob("karakeep-*.tar.gz"):
        if previous != archive and previous.stat().st_mtime < cutoff:
            previous.unlink()
    print(f"Published checked Karakeep backup: {archive.name}")
    return archive


if __name__ == "__main__":
    backup(Path("/data/db.db"), Path("/assets"), Path("/dumps/karakeep"))
