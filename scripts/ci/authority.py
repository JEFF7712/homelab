from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def select(database: Path, authority: str) -> None:
    if authority not in {"woodpecker", "gitlab", "disabled"}:
        raise ValueError("Invalid production authority")
    with closing(sqlite3.connect(database, isolation_level="IMMEDIATE")) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT COUNT(*) FROM locks").fetchone()[0]:
            raise ValueError("Cannot change authority while state is locked")
        db.execute("CREATE TABLE IF NOT EXISTS authority(value TEXT NOT NULL)")
        db.execute("DELETE FROM authority")
        db.execute("INSERT INTO authority VALUES(?)", (authority,))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--select", choices=["woodpecker", "gitlab", "disabled"])
    args = parser.parse_args()
    if args.select:
        select(args.database, args.select)
    with closing(sqlite3.connect(f"file:{args.database}?mode=ro", uri=True)) as db:
        print(
            json.dumps(
                {
                    "authority": db.execute("SELECT value FROM authority").fetchone()[
                        0
                    ],
                    "locks": db.execute("SELECT COUNT(*) FROM locks").fetchone()[0],
                }
            )
        )


if __name__ == "__main__":
    main()
