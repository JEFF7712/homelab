"""Operator commands for the controller store.

    python -m scripts.orchestrator check  --db PATH
    python -m scripts.orchestrator backup --db PATH DESTINATION
    python -m scripts.orchestrator migrate --db PATH
    python -m scripts.orchestrator supervisor-audit --letta-dir DIR [--approve NAME]
        [--allow-chatgpt-sign-in]
    python -m scripts.orchestrator serve --config FILE [--allow-local-jobs]
    python -m scripts.orchestrator render-manifests --params FILE
    python -m scripts.orchestrator bridge --letta-url URL --agent-id ID
        --conversation-id ID --controller-url URL --token-file FILE

serve runs the controller daemon (scripts/orchestrator/daemon.py); bridge
connects a Letta conversation to the client API. The store commands each
print one JSON object. Backup and check run alongside a live
controller. Migrate takes the controller lock, so it refuses to run while a
controller is active, and snapshots the store before every schema step.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from scripts.orchestrator.core import (
    SCHEMA_VERSION,
    OrchestratorError,
    Store,
    filesystem_type,
)
from scripts.orchestrator.supervisor_policy import audit_letta

POLICY = "operator-cli"


BACKUP_PREFIX = "orchestrator-"


def rotate(
    store: Store, directory: Path, keep: int, group: int | None
) -> dict[str, object]:
    """Write a dated, integrity-checked snapshot and keep the newest `keep`.

    The store is 0600 for its owner. With `group`, the snapshot is handed to
    that group read-only so the off-site shipper can read it without any
    access to the live store.
    """
    if keep < 1:
        raise OrchestratorError("INVALID_SPEC", "keep at least one snapshot")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    destination = directory / f"{BACKUP_PREFIX}{stamp}.sqlite"
    store.backup(destination)
    if group is not None:
        os.chown(destination, -1, group)
        destination.chmod(0o640)
    snapshots = sorted(directory.glob(f"{BACKUP_PREFIX}*.sqlite"))
    removed = snapshots[:-keep]
    for old in removed:
        old.unlink()
    return {"backup": str(destination), "removed": [str(old) for old in removed]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.orchestrator")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "migrate", "backup", "backup-rotate"):
        command = commands.add_parser(name)
        command.add_argument("--db", type=Path, required=True)
        if name == "backup":
            command.add_argument("destination", type=Path)
        if name == "backup-rotate":
            command.add_argument("--dir", type=Path, required=True)
            command.add_argument("--keep", type=int, default=7)
            command.add_argument("--group", type=int)
    supervisor = commands.add_parser("supervisor-audit")
    supervisor.add_argument("--letta-dir", type=Path, required=True)
    supervisor.add_argument("--approve", action="append", default=[])
    supervisor.add_argument("--allow-chatgpt-sign-in", action="store_true")
    manifests = commands.add_parser("render-manifests")
    manifests.add_argument("--params", type=Path, required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--config", type=Path, required=True)
    serve.add_argument("--allow-local-jobs", action="store_true")
    bridge = commands.add_parser("bridge")
    for option in ("letta-url", "agent-id", "conversation-id", "controller-url"):
        bridge.add_argument(f"--{option}", required=True)
    bridge.add_argument("--token-file", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "render-manifests":
        import yaml

        from scripts.orchestrator.manifests import DeployParams, IntakeParams, render

        raw = json.loads(args.params.read_text())
        raw["required_contexts"] = tuple(raw.get("required_contexts") or ())
        raw["api_endpoints"] = tuple(
            tuple(item) for item in raw.get("api_endpoints") or ()
        )
        if raw.get("intake"):
            raw["intake"] = IntakeParams(**raw["intake"])
        print(yaml.safe_dump_all(render(DeployParams(**raw)), sort_keys=False), end="")
        return 0
    if args.command == "serve":
        from scripts.orchestrator.daemon import main as serve_main

        return serve_main(args.config, args.allow_local_jobs)
    if args.command == "bridge":
        import asyncio
        import logging

        from scripts.orchestrator.letta_bridge import (
            BridgeConfig,
            ControllerClient,
            LettaBridge,
        )

        logging.basicConfig(level=logging.INFO)
        bridge_runner = LettaBridge(
            BridgeConfig(args.letta_url, args.agent_id, args.conversation_id),
            ControllerClient(args.controller_url, args.token_file.read_text().strip()),
        )
        asyncio.run(bridge_runner.run_forever())
        return 0
    if args.command == "supervisor-audit":
        audit = audit_letta(
            args.letta_dir, os.environ, args.approve, args.allow_chatgpt_sign_in
        )
        print(
            json.dumps(
                {
                    "ok": audit.ok,
                    "status": audit.status,
                    "providers": list(audit.providers),
                    "violations": list(audit.violations),
                }
            )
        )
        return 0 if audit.ok else 1
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
        elif args.command == "backup-rotate":
            result.update(rotate(store, args.dir, args.keep, args.group))
    except OrchestratorError as error:
        print(json.dumps({"ok": False, "code": error.code, "error": str(error)}))
        return 1
    finally:
        store.close()
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
