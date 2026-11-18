from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import importlib
import io
import json
import os
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import traceback
from pathlib import Path
from typing import Any

STATE = Path(os.environ.get("OUTREACH_ROOT", "/state/outreach"))
DEPS = Path(os.environ.get("OUTREACH_DEPS", "/deps"))
for dependency in json.loads((DEPS / "manifest.json").read_text()):
    wheel = DEPS / dependency["filename"]
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != dependency["sha256"]:
        raise RuntimeError(f"dependency digest mismatch: {wheel.name}")
    sys.path.insert(0, str(wheel))
os.environ["OUTREACH_ROOT"] = str(STATE)
os.environ["OUTREACH_RESUMES"] = str(STATE / "resumes")
SEND_INTERVAL = int(os.environ.get("OUTREACH_SEND_INTERVAL", "60"))
engine = importlib.import_module("outreach")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    try:
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def workspace_lock():
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / ".workspace.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def decode(value: str) -> bytes:
    return base64.b64decode(value, validate=True)


def workbook_bytes(value: str) -> bytes:
    data = decode(value)
    workbook = engine.openpyxl.load_workbook(io.BytesIO(data), read_only=True)
    workbook.close()
    return data


def snapshot() -> None:
    target = STATE.parent / "snapshots/latest.tar"
    with io.BytesIO() as output:
        with tarfile.open(fileobj=output, mode="w") as archive:
            for path in sorted(STATE.rglob("*")):
                if path.is_file() and not path.name.endswith((".lock", ".tmp")):
                    archive.add(
                        path, arcname=str(path.relative_to(STATE)), recursive=False
                    )
        atomic_write(target, output.getvalue())


def export_state() -> dict[str, str]:
    master = engine.MASTER.read_bytes()
    return {
        "master": encode(master),
        "master_sha256": hashlib.sha256(master).hexdigest(),
        "queue": encode(engine.QUEUE.read_bytes()),
    }


def invoke(arguments: list[str]) -> int:
    with contextlib.ExitStack() as stack:
        stack.enter_context(contextlib.chdir(STATE))
        previous = sys.argv
        try:
            sys.argv = ["outreach", *arguments]
            engine.main()
            return 0
        except SystemExit as exc:
            return exc.code if isinstance(exc.code, int) else 1
        finally:
            sys.argv = previous


def rpc(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("version") != 1:
        raise ValueError("unsupported outreach protocol")
    args = request["args"]
    allowed = {
        "status",
        "sync",
        "doctor",
        "check",
        "discover",
        "guess",
        "lint",
        "render",
        "queue",
        "reschedule",
        "cancel",
        "send-due",
        "bootstrap",
    }
    if not isinstance(args, list) or not args or args[0] not in allowed:
        raise ValueError("unsupported outreach command")
    if args[0] == "send-due" and "--dry-run" not in args:
        raise ValueError("live send-due runs only in the outreach-control sender loop")
    if args[0] == "bootstrap":
        if engine.MASTER.exists() or engine.QUEUE.exists():
            raise ValueError(
                "remote state already initialized; bootstrap never overwrites it"
            )
        data = request["bootstrap"]
        master = workbook_bytes(data["master"])
        queue = decode(data["queue"])
        if not isinstance(json.loads(queue).get("items"), list):
            raise ValueError("invalid initial queue")
        atomic_write(engine.MASTER, master)
        atomic_write(engine.QUEUE, queue)
    if not engine.MASTER.exists() or not engine.QUEUE.exists():
        raise ValueError("remote state is not initialized")
    update = request.get("master_update")
    if update:
        actual = hashlib.sha256(engine.MASTER.read_bytes()).hexdigest()
        if update["base_sha256"] != actual:
            raise ValueError(
                "tracker conflict: remote tracker changed; preserve local edits and reconcile before retrying"
            )
        atomic_write(engine.MASTER, workbook_bytes(update["data"]))
    for variant, value in request.get("resumes", {}).items():
        if variant not in engine.RESUMES:
            raise ValueError("unknown resume variant")
        data = decode(value)
        if not data.startswith(b"%PDF-"):
            raise ValueError("resume is not a PDF")
        atomic_write(engine.resume_path(variant), data)
    stdout, stderr = io.StringIO(), io.StringIO()
    artifacts = {}
    with (
        tempfile.TemporaryDirectory() as temp,
        contextlib.redirect_stdout(stdout),
        contextlib.redirect_stderr(stderr),
    ):
        if args[0] in {"lint", "queue", "render"}:
            if "spec" not in request:
                raise ValueError("batch spec is required")
            spec = Path(temp) / "batch.json"
            spec.write_text(json.dumps(request["spec"]))
            args = [args[0], str(spec)]
            if args[0] == "render":
                args += ["--out", temp]
        code = invoke(args) if args[0] not in {"sync", "bootstrap"} else 0
        if args[0] == "render":
            artifacts = {
                path.name: encode(path.read_bytes())
                for path in Path(temp).glob("*.eml")
            }
    snapshot()
    return {
        "version": 1,
        "code": code,
        "stdout": stdout.getvalue(),
        "stderr": stderr.getvalue(),
        "artifacts": artifacts,
        **export_state(),
    }


def send_loop(shutdown: threading.Event) -> None:
    # Each pass runs in a fresh interpreter so engine module state never
    # carries over between sends. Only a transition into failure notifies.
    failing = False
    while SEND_INTERVAL > 0 and not shutdown.is_set():
        code = subprocess.run([sys.executable, __file__, "send-due"]).returncode
        if code and not failing:
            engine.notify(
                "Outreach sender failed",
                f"send-due exited {code}; check outreach-control logs",
            )
        failing = code != 0
        shutdown.wait(SEND_INTERVAL)
    shutdown.wait()


def main() -> int:
    command = sys.argv[1]
    if command == "serve":
        shutdown = threading.Event()
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: shutdown.set())
        send_loop(shutdown)
        return 0
    if command == "health":
        STATE.mkdir(parents=True, exist_ok=True)
        return 0 if os.access(STATE, os.W_OK) else 1
    if command == "rpc":
        try:
            request = json.load(sys.stdin)
            with workspace_lock():
                response = rpc(request)
        except Exception as exc:
            response = {
                "version": 1,
                "code": 1,
                "stderr": str(exc) + "\n",
                "stdout": "",
            }
        print(json.dumps(response))
        return 0
    if command == "send-due":
        with workspace_lock():
            if not engine.MASTER.exists() or not engine.QUEUE.exists():
                print("Outreach awaits initial state import; no sends attempted")
                return 0
            try:
                code = invoke(["send-due"])
                atomic_write(STATE / "last-success", engine.now().isoformat().encode())
                return code
            except Exception:
                traceback.print_exc()
                return 1
            finally:
                snapshot()
    raise ValueError("unknown runner command")


if __name__ == "__main__":
    raise SystemExit(main())
