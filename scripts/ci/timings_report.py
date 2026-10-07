"""Print a slowest-first summary of scripts.ci.run timing reports.

Reads every timings.jsonl under the CI timing roots and prints one line
per recorded command with elapsed seconds and exit code. Missing or
empty reports are not an error: the gate result is what matters.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def roots() -> list[Path]:
    configured = os.environ.get("CI_TIMING_DIR")
    if configured:
        return [Path(configured)]
    base = Path("artifacts/ci")
    if not base.is_dir():
        return []
    return sorted(path for path in base.iterdir() if path.is_dir())


def main() -> int:
    records: list[tuple[str, dict[str, Any]]] = []
    for root in roots():
        for report in sorted(root.glob("timings.jsonl")):
            try:
                lines = report.read_text().splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    records.append((str(root), record))
    if not records:
        print("no CI timing reports found")
        return 0
    records.sort(
        key=lambda item: float(item[1].get("elapsed_seconds", 0.0)),
        reverse=True,
    )
    for root_name, record in records:
        print(
            f"{float(record.get('elapsed_seconds', 0.0)):8.1f}s "
            f"exit={record.get('exit_code', '?')} "
            f"{root_name}/{record.get('label', '?')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
