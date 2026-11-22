"""Archive RomM saves and configuration after a successful logical DB dump."""

from __future__ import annotations

import datetime as dt
import gzip
import os
import tarfile
import time
from pathlib import Path


def backup(state: Path, destination: Path) -> Path:
    dumps = sorted(destination.glob("*.sql.gz"))
    if not dumps or time.time() - dumps[-1].stat().st_mtime > 3600:
        raise RuntimeError("No fresh successful RomM database dump")
    # Fully decompress the dump, checking CRC and the completion footer.
    tail = b""
    with gzip.open(dumps[-1], "rb") as source:
        while chunk := source.read(1024 * 1024):
            tail = (tail + chunk)[-4096:]
    if b"Dump completed" not in tail:
        raise RuntimeError("RomM database dump is incomplete")
    for name in ("assets", "config"):
        if not (state / name).is_dir():
            raise FileNotFoundError(f"Missing RomM {name} directory")
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    archive = destination / f"romm-{stamp}.tar.gz"
    pending = archive.with_suffix(".tmp")
    try:
        with tarfile.open(pending, "w:gz") as output:
            output.add(dumps[-1], arcname="database.sql.gz")
            for name in ("assets", "config"):
                output.add(state / name, arcname=name)
        with tarfile.open(pending, "r:gz") as check:
            if not {"database.sql.gz", "assets", "config"}.issubset(check.getnames()):
                raise RuntimeError("RomM archive is incomplete")
            for member in check:
                if member.isfile():
                    stream = check.extractfile(member)
                    assert stream is not None
                    while stream.read(1024 * 1024):
                        pass
        os.replace(pending, archive)
    finally:
        pending.unlink(missing_ok=True)
    for previous in destination.glob("romm-*.tar.gz"):
        if previous != archive and previous.stat().st_mtime < time.time() - 7 * 86400:
            previous.unlink()
    print(f"Published checked RomM backup: {archive.name}")
    return archive


if __name__ == "__main__":
    backup(Path("/state"), Path("/dumps/romm"))
