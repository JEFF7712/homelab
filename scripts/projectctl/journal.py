from __future__ import annotations

import fcntl
import os
import secrets
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .codec import canonical, digest, document, loads
from .errors import Code, ProtocolError
from .models import Plan, string

TERMINAL = {"cancelled", "failed", "succeeded"}
STATES = TERMINAL | {
    "awaiting_authorization",
    "queued",
    "running",
    "waiting",
    "blocked",
}


def _secure_file(path: Path) -> int:
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError:
        raise ProtocolError(Code.RESOURCE_CONFLICT) from None
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
        or info.st_nlink != 1
    ):
        os.close(descriptor)
        raise ProtocolError(Code.RESOURCE_CONFLICT)
    return descriptor


class Journal:
    def __init__(self, path: Path):
        self.path = path.absolute()
        self.path.parent.mkdir(mode=0o700, exist_ok=True)
        info = self.path.parent.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
        ):
            raise ProtocolError(Code.RESOURCE_CONFLICT)
        os.close(_secure_file(self.path))
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        os.close(_secure_file(self.lock_path))
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 2:
                return
            if version != 0:
                raise ProtocolError(Code.INVALID_SPEC)
        with self.execution(), self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, principal TEXT NOT NULL, submission_key TEXT NOT NULL,
                    project_lock TEXT NOT NULL, resource_lock TEXT NOT NULL,
                    plan_digest TEXT NOT NULL, plan TEXT NOT NULL, state TEXT NOT NULL,
                    code TEXT, UNIQUE(principal, submission_key)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS active_project ON requests(project_lock)
                    WHERE state NOT IN ('cancelled', 'failed', 'succeeded');
                CREATE UNIQUE INDEX IF NOT EXISTS active_resource ON requests(resource_lock)
                    WHERE state NOT IN ('cancelled', 'failed', 'succeeded');
                CREATE TABLE IF NOT EXISTS operations (
                    request_id TEXT NOT NULL REFERENCES requests(id), id TEXT NOT NULL,
                    state TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
                    outputs TEXT NOT NULL DEFAULT '{}', evidence TEXT NOT NULL DEFAULT '{}',
                    next_attempt_at TEXT, PRIMARY KEY(request_id, id)
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    request_id TEXT NOT NULL, operation_id TEXT NOT NULL, number INTEGER NOT NULL,
                    state TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
                    grant TEXT, outputs TEXT, evidence TEXT, code TEXT,
                    PRIMARY KEY(request_id, operation_id, number)
                );
                CREATE TABLE IF NOT EXISTS allocations (
                    request_id TEXT NOT NULL, operation_id TEXT NOT NULL, attempt INTEGER NOT NULL,
                    resource_id INTEGER NOT NULL, PRIMARY KEY(request_id, operation_id, attempt)
                );
                PRAGMA user_version=2;
            """)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def execution(self) -> Iterator[None]:
        descriptor = _secure_file(self.lock_path)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ProtocolError(Code.RESOURCE_CONFLICT) from None
            yield
        finally:
            os.close(descriptor)

    def submit(self, plan: Plan, principal: str, submission_key: str) -> str:
        plan = plan.validated()
        string(principal)
        string(submission_key)
        if plan.principal != principal:
            raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
        payload = document(plan)
        fingerprint = digest(payload)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT id,plan_digest FROM requests WHERE principal=? AND submission_key=?",
                (principal, submission_key),
            ).fetchone()
            if existing:
                if existing["plan_digest"] != fingerprint:
                    raise ProtocolError(Code.IDEMPOTENCY_CONFLICT)
                return existing["id"]
            request_id = "req-" + secrets.token_hex(16)
            project_lock = canonical(
                [plan.platform.forgejo, plan.project.project_key.lower()]
            ).decode()
            resource_lock = canonical(
                [plan.platform.forgejo, plan.project.repository.lower()]
            ).decode()
            try:
                db.execute(
                    "INSERT INTO requests VALUES(?,?,?,?,?,?,?,'awaiting_authorization',NULL)",
                    (
                        request_id,
                        principal,
                        submission_key,
                        project_lock,
                        resource_lock,
                        fingerprint,
                        canonical(payload).decode(),
                    ),
                )
            except sqlite3.IntegrityError:
                raise ProtocolError(Code.RESOURCE_CONFLICT) from None
            db.executemany(
                "INSERT INTO operations(request_id,id,state) VALUES(?,?,'pending')",
                [(request_id, operation.operation_id) for operation in plan.operations],
            )
        return request_id

    def lookup(
        self, principal: str, submission_key: str, fingerprint: str
    ) -> str | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT id,plan_digest FROM requests WHERE principal=? AND submission_key=?",
                (principal, submission_key),
            ).fetchone()
            if row is None:
                return None
            if row["plan_digest"] != fingerprint:
                raise ProtocolError(Code.IDEMPOTENCY_CONFLICT)
            return row["id"]

    def status(self, request_id: str, principal: str) -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM requests WHERE id=? AND principal=?",
                (request_id, principal),
            ).fetchone()
            if row is None:
                raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
            operations = []
            for operation in db.execute(
                "SELECT * FROM operations WHERE request_id=? ORDER BY rowid",
                (request_id,),
            ):
                item = dict(operation)
                item["outputs"], item["evidence"] = (
                    loads(item["outputs"]),
                    loads(item["evidence"]),
                )
                operations.append(item)
            return {
                "request_id": row["id"],
                "principal": row["principal"],
                "plan": loads(row["plan"]),
                "plan_digest": row["plan_digest"],
                "state": row["state"],
                "code": row["code"],
                "operations": operations,
            }

    def cancel(self, request_id: str, principal: str) -> None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state FROM requests WHERE id=? AND principal=?",
                (request_id, principal),
            ).fetchone()
            if row is None:
                raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
            if row["state"] in {"failed", "succeeded"}:
                raise ProtocolError(Code.RESOURCE_CONFLICT)
            db.execute(
                "UPDATE requests SET state='cancelled',code=NULL WHERE id=?",
                (request_id,),
            )

    def state(self, request_id: str, state: str, code: Code | None = None) -> None:
        if state not in STATES:
            raise ProtocolError(Code.INVALID_SPEC)
        with self.connection() as db:
            db.execute(
                "UPDATE requests SET state=?,code=? WHERE id=? AND state NOT IN ('cancelled','failed','succeeded')",
                (state, code.value if code else None, request_id),
            )

    def recover(self, request_id: str) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE operations SET state='uncertain' WHERE request_id=? AND state='running'",
                (request_id,),
            )
            db.execute(
                "UPDATE attempts SET state='uncertain',code=? WHERE request_id=? AND state='running'",
                (Code.OUTCOME_UNCERTAIN.value, request_id),
            )

    def begin(
        self, request_id: str, operation_id: str, started_at: str, grant: dict[str, Any]
    ) -> int:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            request = db.execute(
                "SELECT state FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            operation = db.execute(
                "SELECT state,attempt FROM operations WHERE request_id=? AND id=?",
                (request_id, operation_id),
            ).fetchone()
            if (
                request is None
                or request["state"] != "running"
                or operation is None
                or operation["state"]
                not in {"pending", "waiting", "uncertain", "blocked"}
            ):
                raise ProtocolError(Code.RESOURCE_CONFLICT, operation_id)
            attempt = operation["attempt"] + 1
            db.execute(
                "UPDATE operations SET state='running',attempt=?,next_attempt_at=NULL WHERE request_id=? AND id=?",
                (attempt, request_id, operation_id),
            )
            db.execute(
                "INSERT INTO attempts(request_id,operation_id,number,state,started_at,grant) VALUES(?,?,?,'running',?,?)",
                (
                    request_id,
                    operation_id,
                    attempt,
                    started_at,
                    canonical(grant).decode(),
                ),
            )
            return attempt

    def record(
        self,
        request_id: str,
        operation_id: str,
        state: str,
        finished_at: str,
        outputs: dict[str, Any],
        evidence: dict[str, Any],
        code: Code | None = None,
        next_attempt_at: str | None = None,
    ) -> None:
        if state not in {"verified", "uncertain", "waiting", "blocked", "failed"}:
            raise ProtocolError(Code.INVALID_SPEC)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            operation = db.execute(
                "SELECT attempt FROM operations WHERE request_id=? AND id=?",
                (request_id, operation_id),
            ).fetchone()
            if operation is None:
                raise ProtocolError(Code.RESOURCE_CONFLICT)
            serialized = (canonical(outputs).decode(), canonical(evidence).decode())
            db.execute(
                "UPDATE operations SET state=?,outputs=?,evidence=?,next_attempt_at=? WHERE request_id=? AND id=?",
                (state, *serialized, next_attempt_at, request_id, operation_id),
            )
            if operation["attempt"]:
                db.execute(
                    "UPDATE attempts SET state=?,finished_at=?,outputs=?,evidence=?,code=? WHERE request_id=? AND operation_id=? AND number=?",
                    (
                        state,
                        finished_at,
                        *serialized,
                        code.value if code else None,
                        request_id,
                        operation_id,
                        operation["attempt"],
                    ),
                )

    def attempts(self, request_id: str, principal: str) -> list[dict[str, Any]]:
        self.status(request_id, principal)
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM attempts WHERE request_id=? ORDER BY rowid",
                (request_id,),
            )
            return [dict(row) for row in rows]

    def allocate(
        self, request_id: str, operation_id: str, attempt: int, resource_id: int
    ) -> None:
        from .models import number

        number(resource_id)
        if operation_id not in {"repository", "enrollment", "ci"}:
            raise ProtocolError(Code.INVALID_SPEC)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state,attempt FROM operations WHERE request_id=? AND id=?",
                (request_id, operation_id),
            ).fetchone()
            if row is None or row["state"] != "running" or row["attempt"] != attempt:
                raise ProtocolError(Code.RESOURCE_CONFLICT)
            db.execute(
                "INSERT INTO allocations VALUES(?,?,?,?)",
                (request_id, operation_id, attempt, resource_id),
            )

    def allocation(self, request_id: str, operation_id: str) -> int | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT resource_id FROM allocations WHERE request_id=? AND operation_id=? ORDER BY attempt DESC LIMIT 1",
                (request_id, operation_id),
            ).fetchone()
            return row["resource_id"] if row else None

    def backup(self, destination: Path) -> None:
        destination.parent.mkdir(mode=0o700, exist_ok=True)
        info = destination.parent.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
        ):
            raise ProtocolError(Code.RESOURCE_CONFLICT)
        try:
            descriptor = os.open(
                destination, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
        except OSError:
            raise ProtocolError(Code.RESOURCE_CONFLICT) from None
        os.close(descriptor)
        with self.connection() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                target.close()
