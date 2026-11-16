#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(
    os.environ.get("OUTREACH_CLIENT_ROOT", Path(__file__).resolve().parent.parent)
)
MASTER = ROOT / "outputs/2026-11-13-master/outreach_master.xlsx"
RECEIPT = ROOT / "queue/remote-state.json"
CONFIG = Path.home() / ".config/outreach/remote.json"
RESUMES = {
    "biotechML": "biotechMLAI_resume.pdf",
    "scientificML": "scientificML_resume.pdf",
    "general": "general_resume.pdf",
}
ATTACHMENT = "Ramrupan_Sunderapandyan_resume.pdf"


def encoded(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def transport(request: dict[str, Any]) -> dict[str, Any]:
    if not CONFIG.exists():
        raise ValueError(
            "homelab backend is not configured: ~/.config/outreach/remote.json"
        )
    config = json.loads(CONFIG.read_text())
    command = [
        *config["kubectl"],
        "--context",
        config["context"],
        "-n",
        "automation",
        "exec",
        "-i",
        "deployment/outreach-control",
        "-c",
        "outreach",
        "--",
        "python",
        "/app/runner.py",
        "rpc",
    ]
    result = subprocess.run(
        command,
        input=json.dumps(request),
        text=True,
        capture_output=True,
        timeout=900,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"homelab command failed: {result.stderr.strip()}. "
            "A lost connection does not cancel a submitted batch; check status before retrying."
        )
    response = json.loads(result.stdout)
    if response.get("version") != 1:
        raise ValueError("unsupported homelab response")
    return response


def resume_payload(variants: set[str]) -> dict[str, str]:
    result = {}
    for variant in variants:
        if variant not in RESUMES:
            raise ValueError(f"unknown resume variant: {variant}")
        path = Path.home() / "documents/resumes" / variant / ATTACHMENT
        data = path.read_bytes()
        built = ROOT.parent / "resumes/build" / RESUMES[variant]
        if built.exists() and data != built.read_bytes():
            raise ValueError(
                f"{path} differs from {built}; validate and refresh the attachment first"
            )
        if not data.startswith(b"%PDF-"):
            raise ValueError(f"resume is not a PDF: {path}")
        result[variant] = encoded(data)
    return result


def run(args: argparse.Namespace, arguments: list[str]) -> int:
    lock = MASTER.parent / f".~lock.{MASTER.name}#"
    if lock.exists():
        raise ValueError(
            "close the local tracker workbook before syncing outreach state"
        )
    request: dict[str, Any] = {"version": 1, "args": arguments}
    if args.command in {"queue", "lint", "render"}:
        spec = json.loads(Path(args.spec).read_text())
        request["spec"] = spec
        request["resumes"] = resume_payload(
            {entry["resume"] for entry in spec["emails"] if entry.get("resume")}
        )
    if args.command in {"doctor", "bootstrap"}:
        request["resumes"] = resume_payload(set(RESUMES))
    local = MASTER.read_bytes() if MASTER.exists() else None
    local_digest = hashlib.sha256(local).hexdigest() if local is not None else None
    receipt = json.loads(RECEIPT.read_text()) if RECEIPT.exists() else {}
    pulling = args.command == "sync" and args.pull
    if args.command == "bootstrap":
        if local is None:
            raise ValueError("local tracker is missing")
        queue = ROOT / "queue/queue.json"
        request["bootstrap"] = {
            "master": encoded(local),
            "queue": encoded(queue.read_bytes() if queue.exists() else b'{"items":[]}'),
        }
    elif (
        not pulling
        and local is not None
        and local_digest != receipt.get("master_sha256")
    ):
        if not receipt:
            raise ValueError(
                "run outreach sync --pull first to initialize the local tracker mirror"
            )
        request["master_update"] = {
            "base_sha256": receipt["master_sha256"],
            "data": encoded(local),
        }
    if pulling:
        request["args"] = ["sync"]
    response = transport(request)
    if args.command != "render":
        sys.stdout.write(response.get("stdout", ""))
    sys.stderr.write(response.get("stderr", ""))
    if "master" in response:
        master = base64.b64decode(response["master"], validate=True)
        if hashlib.sha256(master).hexdigest() != response["master_sha256"]:
            raise ValueError("remote tracker digest mismatch")
        if local is not None and local != master:
            backup = ROOT / "queue/local-tracker-backups" / f"{local_digest}.xlsx"
            if not backup.exists():
                atomic_write(backup, local)
        atomic_write(MASTER, master)
        atomic_write(
            ROOT / "queue/queue.json",
            base64.b64decode(response["queue"], validate=True),
        )
        atomic_write(
            RECEIPT, json.dumps({"master_sha256": response["master_sha256"]}).encode()
        )
    if args.command == "render" and not response["code"]:
        output = Path(args.out)
        for name, value in response.get("artifacts", {}).items():
            if Path(name).name != name or not name.endswith(".eml"):
                raise ValueError("invalid rendered filename")
            atomic_write(output / name, base64.b64decode(value, validate=True))
            print(output / name)
    if args.command == "sync" and not response["code"]:
        print("Tracker and queue synchronized with homelab")
    return int(response["code"])


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="outreach", description="Laptop client for the homelab outreach queue"
    )
    sub = p.add_subparsers(dest="command", required=True)
    for command in ("lint", "queue", "render"):
        s = sub.add_parser(command)
        s.add_argument("spec")
        if command == "render":
            s.add_argument("--out", required=True)
    s = sub.add_parser("status")
    s.add_argument("--all", action="store_true")
    sub.add_parser("doctor")
    sub.add_parser("bootstrap", help="one-time import into an empty homelab store")
    s = sub.add_parser("sync", help="sync tracker edits and refresh the local mirror")
    s.add_argument(
        "--pull",
        action="store_true",
        help="pull remote state, preserving local tracker in backups",
    )
    s = sub.add_parser("check")
    s.add_argument("terms", nargs="+")
    s.add_argument("--offline", action="store_true")
    s = sub.add_parser("reschedule")
    s.add_argument("target")
    s.add_argument("--at", required=True)
    s.add_argument("--spread-minutes", type=int, default=0)
    s = sub.add_parser("cancel")
    s.add_argument("target")
    s = sub.add_parser(
        "send-due",
        help="inspect due emails on homelab; live sends belong to the CronJob",
    )
    s.add_argument("--dry-run", action="store_true", required=True)
    s.add_argument("--force", action="store_true")
    s.add_argument("--scan-bounces", action="store_true")
    s = sub.add_parser("discover")
    s.add_argument(
        "--tags", default="drug-discovery,biotech,machine-learning,generative-ai"
    )
    s.add_argument("--max-team", type=int, default=150)
    s.add_argument("--anywhere", action="store_true")
    s.add_argument("--include-known", action="store_true")
    s = sub.add_parser("guess")
    s.add_argument("name")
    s.add_argument("domain")
    s.add_argument("--known", nargs="*")
    s.add_argument("--pattern")
    s.add_argument("--team-size", type=int)
    s.add_argument("--max", type=int, default=3)
    return p


def main() -> int:
    arguments = sys.argv[1:]
    args = parser().parse_args(arguments)
    try:
        return run(args, arguments)
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
