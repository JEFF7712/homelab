"""Operator commands for the controller store.

    python -m scripts.orchestrator check  --db PATH
    python -m scripts.orchestrator backup --db PATH DESTINATION
    python -m scripts.orchestrator migrate --db PATH

Each prints one JSON object. Backup and check run alongside a live
controller. Migrate takes the controller lock, so it refuses to run while a
controller is active, and snapshots the store before every schema step.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts.orchestrator.core import (
    SCHEMA_VERSION,
    OrchestratorError,
    Store,
    filesystem_type,
)

POLICY = "operator-cli"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.orchestrator")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "migrate", "backup"):
        command = commands.add_parser(name)
        command.add_argument("--db", type=Path, required=True)
        if name == "backup":
            command.add_argument("destination", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "check" and not args.db.exists():
            raise OrchestratorError("NOT_FOUND", f"{args.db} does not exist")
        store = Store(args.db, POLICY, controller=args.command == "migrate")
    except OrchestratorError as error:
        print(json.dumps({"ok": False, "code": error.code, "error": str(error)}))
        return 1
    try:
        result: dict[str, object] = {
            "ok": True,
            "schema_version": store.schema_version(),
            "supported_version": SCHEMA_VERSION,
            "filesystem": filesystem_type(args.db.parent),
        }
        if args.command == "check":
            result["integrity"] = store.integrity()
            result["ok"] = result["integrity"] == "ok"
        elif args.command == "backup":
            store.backup(args.destination)
            result["backup"] = str(args.destination)
    except OrchestratorError as error:
        print(json.dumps({"ok": False, "code": error.code, "error": str(error)}))
        return 1
    finally:
        store.close()
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
