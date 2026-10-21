from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .git_state import collect_git_state
from .redact import redact


def write_evidence(
    root: Path, probe: str, target: str, result: str, details: str
) -> Path:
    timestamp = datetime.now(UTC)
    directory = root / ".agent-state" / "evidence" / probe
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{timestamp.strftime('%Y%m%dT%H%M%S.%fZ')}.json"
    try:
        fingerprint = collect_git_state(root).fingerprint
    except RuntimeError:
        fingerprint = "unavailable"
    payload = {
        "schema_version": 1,
        "timestamp": timestamp.isoformat(),
        "probe": probe,
        "target": target,
        "source_fingerprint": fingerprint,
        "result": result,
        "details": redact(details),
    }
    descriptor, temporary = tempfile.mkstemp(prefix=".evidence-", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            Path(temporary).unlink()
        except FileNotFoundError:
            pass
    return path
