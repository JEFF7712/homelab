from __future__ import annotations

import argparse
import base64
import hmac
import json
import sqlite3
import ssl
import time
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

STATES = {"opnsense", "cloudflare"}


class Store:
    def __init__(self, path: Path):
        self.path = path
        with self.connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS states(name TEXT PRIMARY KEY, body BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS locks(name TEXT PRIMARY KEY, id TEXT NOT NULL, owner TEXT NOT NULL, body BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS versions(sequence INTEGER PRIMARY KEY, name TEXT NOT NULL, body BLOB NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS artifacts(key TEXT PRIMARY KEY, body BLOB NOT NULL, created INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS authority(value TEXT NOT NULL);
                INSERT INTO authority(value) SELECT 'disabled' WHERE NOT EXISTS(SELECT 1 FROM authority);
            """)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30, isolation_level="IMMEDIATE")
        try:
            with db:
                yield db
        finally:
            db.close()

    def authority(self) -> str:
        with self.connection() as db:
            return db.execute("SELECT value FROM authority").fetchone()[0]

    def request(
        self,
        method: str,
        name: str,
        lock: bool,
        identity: str,
        role: str,
        body: bytes,
        lock_id: str,
        authority: str | None = None,
    ) -> tuple[int, bytes]:
        if name not in STATES:
            return 404, b""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if method != "GET" and authority is not None:
                if db.execute("SELECT value FROM authority").fetchone()[0] != authority:
                    return 403, b""
            row = db.execute(
                "SELECT id,owner,body FROM locks WHERE name=?", (name,)
            ).fetchone()
            if lock:
                info = json.loads(body)
                requested_id = info.get("ID")
                if not isinstance(requested_id, str) or not requested_id:
                    return 400, b""
                if method == "LOCK":
                    if row:
                        return 423, row[2]
                    db.execute(
                        "INSERT INTO locks VALUES(?,?,?,?)",
                        (name, requested_id, identity, body),
                    )
                    return 200, b""
                if method == "UNLOCK":
                    if row and (row[0] != requested_id or row[1] != identity):
                        return 409, row[2]
                    db.execute("DELETE FROM locks WHERE name=?", (name,))
                    return 200, b""
                return 405, b""
            if method == "GET":
                state = db.execute(
                    "SELECT body FROM states WHERE name=?", (name,)
                ).fetchone()
                return (200, state[0]) if state else (404, b"")
            if method != "POST" or role != "deploy":
                return 403, b""
            if not row or row[:2] != (lock_id, identity):
                return 409, row[2] if row else b""
            state = json.loads(body)
            if not isinstance(state.get("serial"), int) or not state.get("lineage"):
                return 400, b""
            previous = db.execute(
                "SELECT body FROM states WHERE name=?", (name,)
            ).fetchone()
            if previous:
                old = json.loads(previous[0])
                if (
                    old["lineage"] != state["lineage"]
                    or old["serial"] > state["serial"]
                    or old["serial"] == state["serial"]
                    and old != state
                ):
                    return 409, b""
            db.execute("INSERT INTO versions(name,body) VALUES(?,?)", (name, body))
            db.execute("INSERT OR REPLACE INTO states VALUES(?,?)", (name, body))
            return 200, b""

    def import_state(self, name: str, body: bytes) -> None:
        state = json.loads(body)
        if (
            name not in STATES
            or not state.get("lineage")
            or not isinstance(state.get("serial"), int)
        ):
            raise ValueError("Invalid state import")
        with self.connection() as db:
            db.execute("INSERT INTO states VALUES(?,?)", (name, body))
            db.execute("INSERT INTO versions(name,body) VALUES(?,?)", (name, body))

    def artifact(
        self, method: str, key: str, body: bytes, authority: str | None = None
    ) -> tuple[int, bytes]:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if method != "GET" and authority is not None:
                if db.execute("SELECT value FROM authority").fetchone()[0] != authority:
                    return 403, b""
            if method == "GET":
                row = db.execute(
                    "SELECT body,created FROM artifacts WHERE key=?", (key,)
                ).fetchone()
                if not row:
                    return 404, b""
                if int(time.time()) - row[1] > 86400:
                    return 410, b""
                return 200, row[0]
            if method != "POST":
                return 405, b""
            db.execute(
                "DELETE FROM artifacts WHERE created < ?", (int(time.time()) - 604800,)
            )
            try:
                db.execute(
                    "INSERT INTO artifacts VALUES(?,?,?)", (key, body, int(time.time()))
                )
            except sqlite3.IntegrityError:
                return 409, b""
            return 201, b""


def handler(store: Store, credentials: dict[str, Any]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def dispatch(self) -> None:
            try:
                if self.command == "GET" and self.path == "/authority":
                    response = json.dumps({"authority": store.authority()}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(response)))
                    self.end_headers()
                    self.wfile.write(response)
                    return
                authorization = self.headers.get("Authorization", "")
                if not authorization.startswith("Basic "):
                    self.send_error(401)
                    return
                user, password = (
                    base64.b64decode(authorization[6:], validate=True)
                    .decode()
                    .split(":", 1)
                )
                entry = credentials.get(user)
                if not entry or not hmac.compare_digest(password, entry["password"]):
                    self.send_error(401)
                    return
                if self.command != "GET" and entry["authority"] != store.authority():
                    self.send_error(403, "Inactive production authority")
                    return
                if self.command == "POST" and self.path == "/permit":
                    self.send_response(200)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                parsed = urllib.parse.urlsplit(self.path)
                parts = parsed.path.strip("/").split("/")
                if len(parts) == 4 and parts[0] == "artifacts":
                    import re

                    if not re.fullmatch(
                        r"artifacts/[0-9a-f]{40}/[1-9][0-9]*/[a-z0-9-]+",
                        parsed.path.strip("/"),
                    ):
                        self.send_error(400)
                        return
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 <= length <= 33554432:
                        self.send_error(413)
                        return
                    status, response = store.artifact(
                        self.command,
                        parsed.path.strip("/"),
                        self.rfile.read(length),
                        entry["authority"],
                    )
                    self.send_response(status)
                    self.send_header("Content-Length", str(len(response)))
                    self.end_headers()
                    self.wfile.write(response)
                    return
                if (
                    len(parts) not in {2, 3}
                    or parts[0] != "state"
                    or (len(parts) == 3 and parts[2] != "lock")
                ):
                    self.send_error(404)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 33554432:
                    self.send_error(413)
                    return
                body = self.rfile.read(length)
                lock_id = urllib.parse.parse_qs(parsed.query).get("ID", [""])[0]
                status, response = store.request(
                    self.command,
                    parts[1],
                    len(parts) == 3,
                    user,
                    entry["role"],
                    body,
                    lock_id,
                    entry["authority"],
                )
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)
            except (ValueError, KeyError, TypeError):
                self.send_error(400)

        do_GET = dispatch
        do_POST = dispatch
        do_LOCK = dispatch
        do_UNLOCK = dispatch

        def log_message(self, format: str, *args: Any) -> None:
            # Lock IDs and paths need not enter the journal.
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    args = parser.parse_args()
    store = Store(args.database)
    with store.connection() as db:
        if {row[0] for row in db.execute("SELECT name FROM states")} != STATES:
            raise ValueError("Both production states must be imported before serving")
    server = ThreadingHTTPServer(
        ("0.0.0.0", 3902),
        handler(store, json.loads(args.credentials.read_text())),
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.certificate, args.key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
