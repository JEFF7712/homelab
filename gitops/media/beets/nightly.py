"""Run the scheduled importer and report durable, observable progress."""

import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml


def counts(database: Path) -> dict[str, int]:
    if not database.exists():
        return {"tracks": 0, "albums": 0}
    with sqlite3.connect(
        database.resolve().as_uri() + "?mode=ro", uri=True
    ) as connection:
        return {
            name: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for name, table in (("tracks", "items"), ("albums", "albums"))
        }


def run(config_path: Path, paths: list[str]) -> int:
    config = yaml.safe_load(config_path.read_text())
    database = Path(config["library"])
    state = Path(config["statefile"])
    database.parent.mkdir(parents=True, exist_ok=True)
    state.parent.mkdir(parents=True, exist_ok=True)
    before = counts(database)
    print(json.dumps({"event": "beets_start", "before": before}), flush=True)
    result = subprocess.run(
        [
            "beet",
            "-c",
            str(config_path),
            "import",
            "--resume",
            "--quiet",
            *(paths or [config["directory"]]),
        ],
        stdin=subprocess.DEVNULL,
        check=False,
    )
    after = counts(database)
    code = result.returncode or (1 if after["tracks"] == 0 else 0)
    summary = {
        "event": "beets_summary",
        "finished_at": datetime.now(UTC).isoformat(),
        "status": "failed" if code else "completed",
        "before": before,
        "after": after,
        "added": {key: after[key] - before[key] for key in before},
        "import_exit_code": result.returncode,
    }
    (database.parent / "last-run.json").write_text(json.dumps(summary) + "\n")
    print(json.dumps(summary), flush=True)
    if not result.returncode and not after["tracks"]:
        print(
            "beets imported no tracks into an empty library; inspect import.log and matching configuration",
            file=sys.stderr,
        )
    return code


if __name__ == "__main__":
    sys.exit(run(Path("/config/config.yaml"), sys.argv[1:]))
