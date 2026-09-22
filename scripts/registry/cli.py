from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Sequence

from .core import (
    DEFAULT_REGISTRY,
    OciClient,
    RegistryError,
    atomic_write_json,
    atomic_write_private,
    check_consumers,
    copy_lock,
    copy_plan,
    discover_inventory,
    load_inventory,
    load_lock,
    refresh_live_snapshot,
    render_access_control,
    render_node_config,
    resolve_inventory,
    verify_lock,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.registry")
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser("inventory", help="discover image inputs offline")
    inventory.add_argument("--output", type=pathlib.Path)

    subparsers.add_parser(
        "snapshot-live", help="refresh sanitized live Kubernetes image observations"
    )

    resolve = subparsers.add_parser(
        "resolve", help="resolve source manifests into a lock candidate"
    )
    resolve.add_argument("--inventory", type=pathlib.Path, required=True)
    resolve.add_argument("--output", type=pathlib.Path, required=True)
    resolve.add_argument("--destination-registry", default=DEFAULT_REGISTRY)
    _network_options(resolve)

    plan = subparsers.add_parser("plan", help="emit a deterministic copy plan")
    plan.add_argument("--lock", type=pathlib.Path, required=True)

    copy = subparsers.add_parser("copy", help="copy and verify locked content")
    copy.add_argument("--lock", type=pathlib.Path, required=True)
    copy.add_argument("--report", type=pathlib.Path, required=True)
    copy.add_argument("--kind", choices=("first-party", "upstream"))
    copy.add_argument("--concurrency", type=int, default=3)
    copy.add_argument("--image-timeout", type=float, default=900)
    _network_options(copy)

    verify = subparsers.add_parser("verify", help="verify locked destination content")
    verify.add_argument("--lock", type=pathlib.Path, required=True)
    verify.add_argument("--report", type=pathlib.Path, required=True)
    verify.add_argument("--kind", choices=("first-party", "upstream"))
    _network_options(verify)

    check = subparsers.add_parser("check", help="check consumer image policy offline")
    check.add_argument("--lock", type=pathlib.Path, required=True)

    node_config = subparsers.add_parser(
        "node-config", help="write a protected k3s registries.yaml"
    )
    node_config.add_argument("--lock", type=pathlib.Path, required=True)
    node_config.add_argument("--username", required=True)
    node_config.add_argument("--password-file", type=pathlib.Path, required=True)
    node_config.add_argument("--output", type=pathlib.Path, required=True)

    access_control = subparsers.add_parser(
        "access-control", help="write the lock-derived zot authorization policy"
    )
    access_control.add_argument("--lock", type=pathlib.Path, required=True)
    access_control.add_argument("--output", type=pathlib.Path, required=True)
    return parser


def _network_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=2)


def _print(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "snapshot-live":
            snapshot = refresh_live_snapshot(args.root)
            _print(
                {
                    "schema_version": 1,
                    "command": "snapshot-live",
                    "status": "ok",
                    "observed_at": snapshot["observed_at"],
                    "images": len(snapshot["images"]),
                }
            )
            return 0
        if args.command == "inventory":
            inventory = discover_inventory(args.root)
            if args.output:
                atomic_write_json(args.output, inventory)
            _print(inventory)
            return (
                2
                if any(
                    item.get("blocking", True)
                    for item in inventory["unresolved_inputs"]
                )
                else 0
            )
        if args.command == "resolve":
            inventory = load_inventory(args.inventory)
            client = OciClient(timeout=args.timeout, retries=args.retries)
            lock, unresolved = resolve_inventory(
                inventory, client, destination_registry=args.destination_registry
            )
            atomic_write_json(args.output, lock)
            _print(
                {
                    "schema_version": 1,
                    "command": "resolve",
                    "status": (
                        "incomplete"
                        if any(item.get("blocking", True) for item in unresolved)
                        else "ok"
                    ),
                    "output": str(args.output),
                    "resolved": len(lock["images"]),
                    "unresolved": len(unresolved),
                }
            )
            return 2 if any(item.get("blocking", True) for item in unresolved) else 0
        if args.command == "plan":
            lock = load_lock(args.lock)
            _print(copy_plan(lock))
            return 0
        if args.command in {"copy", "verify"}:
            lock = load_lock(args.lock)
            client = OciClient(timeout=args.timeout, retries=args.retries)
            report = (
                copy_lock(
                    client,
                    lock,
                    kind=args.kind,
                    concurrency=args.concurrency,
                    image_timeout=args.image_timeout,
                    progress=lambda message: print(
                        f"[registry-copy] {message}", file=sys.stderr
                    ),
                )
                if args.command == "copy"
                else verify_lock(client, lock, kind=args.kind)
            )
            atomic_write_json(args.report, report)
            _print(report)
            return 0 if report["status"] == "ok" else 1
        if args.command == "check":
            lock = load_lock(args.lock)
            report = check_consumers(args.root, lock)
            _print(report)
            return 0 if report["status"] == "ok" else 1
        if args.command == "node-config":
            lock = load_lock(args.lock)
            try:
                password = args.password_file.read_text(encoding="utf-8")
            except OSError as error:
                raise RegistryError("cannot read registry password file") from error
            password = password.removesuffix("\n")
            payload = render_node_config(lock, args.username, password)
            atomic_write_private(args.output, payload)
            _print(
                {
                    "schema_version": 1,
                    "command": "node-config",
                    "status": "ok",
                    "output": str(args.output),
                    "mode": "0600",
                }
            )
            return 0
        if args.command == "access-control":
            lock = load_lock(args.lock)
            atomic_write_json(args.output, render_access_control(lock))
            _print(
                {
                    "schema_version": 1,
                    "command": "access-control",
                    "status": "ok",
                    "output": str(args.output),
                    "mode": "0600",
                }
            )
            return 0
        raise AssertionError("unreachable")
    except RegistryError as error:
        _print(
            {
                "schema_version": 1,
                "command": args.command,
                "status": "error",
                "error_type": "registry_error",
                "message": str(error),
            }
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
