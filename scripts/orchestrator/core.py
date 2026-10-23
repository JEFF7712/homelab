"""Durable task state for the persistent agent's workers.

The supervisor proposes work and the workers produce evidence, but neither
owns task truth: every status change, lease, question, approval and
notification is a row here, written in one transaction with the transition
that caused it. Transport is at-least-once everywhere, so every write is keyed
for idempotency. See docs/protocols/agent-tasks.md for the contract.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PAYLOAD_LIMIT = 16 * 1024
MAX_ATTEMPTS = 3

TERMINAL = frozenset({"completed", "failed", "cancelled"})
TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"ready", "cancelled"}),
    "ready": frozenset({"claimed", "cancelled"}),
    "claimed": frozenset({"running", "retryable_failed", "failed", "cancelled"}),
    "running": frozenset(
        {"needs_input", "validating", "retryable_failed", "failed", "cancelled"}
    ),
    "needs_input": frozenset({"running", "retryable_failed", "failed", "cancelled"}),
    "validating": frozenset({"in_review", "retryable_failed", "failed", "cancelled"}),
    "in_review": frozenset({"completed", "retryable_failed", "failed", "cancelled"}),
    "retryable_failed": frozenset({"ready", "failed", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}
# States in which the worker, not the controller, drives the task.
WORKER_ACTIVE = frozenset({"claimed", "running", "needs_input"})
NOTIFY = frozenset(
    {"needs_input", "in_review", "completed", "failed", "cancelled", "claimed"}
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  principal TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  request_digest TEXT NOT NULL,
  project TEXT NOT NULL,
  title TEXT NOT NULL,
  goal TEXT NOT NULL,
  acceptance_criteria TEXT NOT NULL,
  requested_capabilities TEXT NOT NULL,
  priority INTEGER NOT NULL,
  parent_id TEXT REFERENCES tasks(id),
  status TEXT NOT NULL,
  external_issue_ref TEXT,
  policy_version TEXT NOT NULL,
  created REAL NOT NULL,
  updated REAL NOT NULL,
  UNIQUE(principal, idempotency_key)
);
CREATE TABLE IF NOT EXISTS dependencies (
  task_id TEXT NOT NULL REFERENCES tasks(id),
  depends_on TEXT NOT NULL REFERENCES tasks(id),
  PRIMARY KEY(task_id, depends_on)
);
CREATE TABLE IF NOT EXISTS transitions (
  task_id TEXT NOT NULL REFERENCES tasks(id),
  seq INTEGER NOT NULL,
  from_status TEXT,
  to_status TEXT NOT NULL,
  actor TEXT NOT NULL,
  reason TEXT NOT NULL,
  at REAL NOT NULL,
  PRIMARY KEY(task_id, seq)
);
CREATE TABLE IF NOT EXISTS attempts (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  number INTEGER NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  worker_backend TEXT NOT NULL,
  lease_owner TEXT NOT NULL,
  lease_expires REAL NOT NULL,
  session_ref TEXT,
  workspace_ref TEXT,
  token_digest TEXT,
  started REAL NOT NULL,
  finished REAL,
  exit_reason TEXT,
  UNIQUE(task_id, number)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_live_attempt ON attempts(task_id)
  WHERE finished IS NULL;
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  attempt_id TEXT REFERENCES attempts(id),
  type TEXT NOT NULL,
  actor TEXT NOT NULL,
  correlation_id TEXT,
  payload TEXT NOT NULL,
  at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS questions (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  attempt_id TEXT NOT NULL REFERENCES attempts(id),
  prompt TEXT NOT NULL,
  choices TEXT NOT NULL,
  blocking INTEGER NOT NULL,
  status TEXT NOT NULL,
  revision INTEGER NOT NULL,
  answer TEXT,
  answered_by TEXT,
  deadline REAL NOT NULL,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  attempt_id TEXT REFERENCES attempts(id),
  action_digest TEXT NOT NULL,
  action TEXT NOT NULL,
  principal TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  status TEXT NOT NULL,
  decided_by TEXT,
  expires REAL NOT NULL,
  created REAL NOT NULL,
  used REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_approval ON approvals(action_digest)
  WHERE status = 'pending';
CREATE TABLE IF NOT EXISTS outbox (
  key TEXT PRIMARY KEY,
  topic TEXT NOT NULL,
  task_id TEXT REFERENCES tasks(id),
  attempt_id TEXT REFERENCES attempts(id),
  payload TEXT NOT NULL,
  created REAL NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  delivered REAL,
  last_error TEXT
);
CREATE INDEX IF NOT EXISTS outbox_attempt ON outbox(attempt_id, topic)
  WHERE delivered IS NULL;
CREATE TABLE IF NOT EXISTS verifications (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  pull_request TEXT NOT NULL,
  head_sha TEXT NOT NULL,
  ci_state TEXT NOT NULL,
  criteria_met TEXT NOT NULL,
  summary TEXT NOT NULL,
  verifier TEXT NOT NULL,
  at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
INSERT OR IGNORE INTO settings (key, value) VALUES ('max_running', '1');
"""


# Ordered, append-only. Each entry upgrades the schema to its index + 1 inside
# one transaction; never edit a released entry, add a new one.
MIGRATIONS: tuple[str, ...] = (SCHEMA,)
SCHEMA_VERSION = len(MIGRATIONS)
NETWORK_FILESYSTEMS = frozenset(
    {
        "nfs",
        "nfs4",
        "cifs",
        "smb3",
        "smbfs",
        "9p",
        "ceph",
        "glusterfs",
        "afs",
        "davfs",
        "fuse.sshfs",
        "fuse.rclone",
        "fuse.s3fs",
        "fuse.glusterfs",
    }
)


class OrchestratorError(ValueError):
    """A request the controller refuses; reported to the caller, never retried."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


SHA = re.compile(r"^[0-9a-f]{40}$")


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value).encode()).hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


@dataclass(frozen=True)
class TaskSpec:
    project: str
    title: str
    goal: str
    acceptance_criteria: tuple[str, ...]
    requested_capabilities: tuple[str, ...] = ()
    priority: int = 0
    parent_id: str | None = None
    depends_on: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.project or not self.title or not self.goal:
            raise OrchestratorError(
                "INVALID_SPEC", "project, title and goal are required"
            )
        if not self.acceptance_criteria or not all(
            item.strip() for item in self.acceptance_criteria
        ):
            raise OrchestratorError(
                "INVALID_SPEC",
                "at least one non-empty acceptance criterion is required",
            )

    def request(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "title": self.title,
            "goal": self.goal,
            "acceptance_criteria": list(self.acceptance_criteria),
            "requested_capabilities": sorted(self.requested_capabilities),
            "priority": self.priority,
            "parent_id": self.parent_id,
            "depends_on": sorted(self.depends_on),
        }


@dataclass(frozen=True)
class Task:
    id: str
    principal: str
    project: str
    title: str
    goal: str
    acceptance_criteria: tuple[str, ...]
    requested_capabilities: tuple[str, ...]
    priority: int
    parent_id: str | None
    status: str
    external_issue_ref: str | None
    policy_version: str
    created: float
    updated: float

    def public(self) -> dict[str, Any]:
        return {
            **{key: getattr(self, key) for key in self.__dataclass_fields__},
            "acceptance_criteria": list(self.acceptance_criteria),
            "requested_capabilities": list(self.requested_capabilities),
        }


@dataclass(frozen=True)
class Attempt:
    id: str
    task_id: str
    number: int
    provider: str
    model: str
    worker_backend: str
    lease_owner: str
    lease_expires: float
    session_ref: str | None
    workspace_ref: str | None
    token_digest: str | None
    started: float
    finished: float | None
    exit_reason: str | None


@dataclass(frozen=True)
class Question:
    id: str
    task_id: str
    attempt_id: str
    prompt: str
    choices: tuple[str, ...]
    blocking: bool
    status: str
    revision: int
    answer: str | None
    answered_by: str | None
    deadline: float


@dataclass(frozen=True)
class Approval:
    id: str
    task_id: str
    attempt_id: str | None
    action_digest: str
    principal: str
    policy_version: str
    status: str
    decided_by: str | None
    expires: float
    used: float | None


@dataclass(frozen=True)
class Message:
    key: str
    topic: str
    task_id: str | None
    payload: dict[str, Any]
    attempts: int


@dataclass(frozen=True)
class Evidence:
    """What the controller's verifier observed in Forgejo and Woodpecker.

    Never built from worker output. Recording it is the verification stage;
    complete() later finalizes from the stored record, not from a caller.
    """

    pull_request: str
    head_sha: str
    ci_state: str
    criteria_met: tuple[str, ...]
    summary: str


def filesystem_type(path: Path, mounts: Path = Path("/proc/self/mounts")) -> str:
    """Return the filesystem type of the mount that holds path."""
    target = path.resolve()
    best, kind = "", "unknown"
    for line in mounts.read_text().splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        point = fields[1].replace("\\040", " ")
        inside = target == Path(point) or Path(point) in target.parents
        if inside and len(point) >= len(best):
            best, kind = point, fields[2]
    return kind


def require_local(path: Path, mounts: Path = Path("/proc/self/mounts")) -> None:
    """Refuse network filesystems, whose locking breaks SQLite WAL."""
    kind = filesystem_type(path.parent, mounts)
    if kind in NETWORK_FILESYSTEMS:
        raise OrchestratorError(
            "STORAGE", f"{path} is on {kind}; the store needs a local filesystem"
        )


class ControllerLock:
    """Exclusive advisory lock that admits one active controller per store."""

    def __init__(self, database: Path) -> None:
        self.path = database.with_name(database.name + ".controller.lock")
        self.fd: int | None = None

    def __enter__(self) -> ControllerLock:
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise OrchestratorError(
                "CONTROLLER_ACTIVE", f"another controller holds {self.path}"
            ) from None
        self.fd = fd
        return self

    def __exit__(self, *_: object) -> None:
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None


def backup(source: sqlite3.Connection, destination: Path) -> None:
    """Write a consistent, integrity-checked copy that only the owner can read."""
    if destination.exists():
        raise OrchestratorError("EXISTS", f"{destination} already exists")
    partial = destination.with_name(
        f".{destination.name}.{secrets.token_hex(4)}.partial"
    )
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        target = sqlite3.connect(partial)
        try:
            source.backup(target)
            result = target.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            target.close()
        if result != "ok":
            raise OrchestratorError(
                "INTEGRITY", f"backup {destination} failed: {result}"
            )
        os.link(partial, destination)
    finally:
        partial.unlink(missing_ok=True)


class Store:
    def __init__(
        self,
        path: Path,
        policy_version: str,
        mounts: Path = Path("/proc/self/mounts"),
        controller: bool = False,
    ) -> None:
        """Open the store; `controller=True` also takes the controller lock.

        Only the controller may claim or reap. Operator commands open the
        store without the lock and stay read-mostly.
        """
        require_local(path, mounts)
        self.path = path
        self.policy_version = policy_version
        self.controller_lock: ControllerLock | None = None
        if controller:
            self.controller_lock = ControllerLock(path).__enter__()
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        try:
            current = self.schema_version()
            if 0 < current < SCHEMA_VERSION and not controller:
                raise OrchestratorError(
                    "SCHEMA",
                    f"store is at schema {current}; stop the controller and run "
                    "`python -m scripts.orchestrator migrate`",
                )
            self.migrate()
        except BaseException:
            self.close()
            raise

    def schema_version(self) -> int:
        return self.db.execute("PRAGMA user_version").fetchone()[0]

    def migrate(self) -> list[Path]:
        """Apply pending migrations, snapshotting an existing store first."""
        lock_path = self.path.with_name(self.path.name + ".migrate.lock")
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            return self._migrate()
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _migrate(self) -> list[Path]:
        with self.lock:
            current = self.schema_version()
            if current > SCHEMA_VERSION:
                raise OrchestratorError(
                    "SCHEMA",
                    f"database schema {current} is newer than {SCHEMA_VERSION}",
                )
            snapshots = []
            for version in range(current + 1, SCHEMA_VERSION + 1):
                if version > 1 or current >= 1:
                    snapshot = self.path.with_name(
                        f"{self.path.name}.pre-v{version}-{secrets.token_hex(4)}.sqlite"
                    )
                    backup(self.db, snapshot)
                    snapshots.append(snapshot)
                try:
                    self.db.executescript(
                        "BEGIN IMMEDIATE;\n"
                        + MIGRATIONS[version - 1]
                        + f"\nPRAGMA user_version={version};\nCOMMIT;"
                    )
                except sqlite3.Error:
                    if self.db.in_transaction:
                        self.db.execute("ROLLBACK")
                    raise
            return snapshots

    def backup(self, destination: Path) -> None:
        with self.lock:
            backup(self.db, destination)

    def integrity(self) -> str:
        with self.lock:
            return self.db.execute("PRAGMA integrity_check").fetchone()[0]

    def close(self) -> None:
        if hasattr(self, "db"):
            self.db.close()
        if self.controller_lock is not None:
            self.controller_lock.__exit__()
            self.controller_lock = None

    def _require_controller(self, action: str = "claim or reap attempts") -> None:
        if self.controller_lock is None:
            raise OrchestratorError(
                "NOT_CONTROLLER", f"only the controller may {action}"
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
                self.db.execute("COMMIT")
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def max_running(self) -> int:
        with self.lock:
            row = self.db.execute(
                "SELECT value FROM settings WHERE key = 'max_running'"
            ).fetchone()
        return int(row["value"])

    def set_max_running(self, value: int) -> None:
        """Set the global concurrency cap that every claim enforces."""
        if value < 0:
            raise OrchestratorError("INVALID_SPEC", "max_running must be >= 0")
        with self.transaction() as db:
            db.execute(
                "UPDATE settings SET value = ? WHERE key = 'max_running'", (str(value),)
            )

    # Tasks

    def create_task(
        self,
        principal: str,
        idempotency_key: str,
        spec: TaskSpec,
        now: float,
        draft: bool = False,
    ) -> tuple[Task, bool]:
        """Create a task, or return the one this key already created.

        Reusing a key with a different request is a conflict rather than a
        silent replacement, so a retried tool call cannot mutate a task.
        """
        spec.validate()
        request_digest = digest(spec.request())
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM tasks WHERE principal = ? AND idempotency_key = ?",
                (principal, idempotency_key),
            ).fetchone()
            if row is not None:
                if row["request_digest"] != request_digest:
                    raise OrchestratorError(
                        "IDEMPOTENCY_CONFLICT",
                        f"key {idempotency_key} was used for a different task",
                    )
                return self._task(row), False
            for reference in (spec.parent_id, *spec.depends_on):
                if reference is not None and self._row(db, reference) is None:
                    raise OrchestratorError("NOT_FOUND", f"unknown task {reference}")
            identifier = new_id("task")
            status = "draft" if draft else "ready"
            db.execute(
                "INSERT INTO tasks (id, principal, idempotency_key, request_digest, "
                "project, title, goal, acceptance_criteria, requested_capabilities, "
                "priority, parent_id, status, policy_version, created, updated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    identifier,
                    principal,
                    idempotency_key,
                    request_digest,
                    spec.project,
                    spec.title,
                    spec.goal,
                    json.dumps(list(spec.acceptance_criteria)),
                    json.dumps(sorted(spec.requested_capabilities)),
                    spec.priority,
                    spec.parent_id,
                    status,
                    self.policy_version,
                    now,
                    now,
                ),
            )
            db.executemany(
                "INSERT INTO dependencies (task_id, depends_on) VALUES (?, ?)",
                [(identifier, item) for item in sorted(set(spec.depends_on))],
            )
            self._record(db, identifier, None, status, principal, "created", now)
            return self._task(self._row(db, identifier)), True  # type: ignore[arg-type]

    def add_dependency(self, task_id: str, depends_on: str) -> None:
        with self.transaction() as db:
            for reference in (task_id, depends_on):
                if self._row(db, reference) is None:
                    raise OrchestratorError("NOT_FOUND", f"unknown task {reference}")
            if task_id in self._ancestors(db, depends_on) | {depends_on}:
                raise OrchestratorError(
                    "DEPENDENCY_CYCLE", f"{task_id} cannot depend on {depends_on}"
                )
            db.execute(
                "INSERT OR IGNORE INTO dependencies (task_id, depends_on) VALUES (?, ?)",
                (task_id, depends_on),
            )

    def get_task(self, task_id: str) -> Task:
        with self.lock:
            row = self._row(self.db, task_id)
        if row is None:
            raise OrchestratorError("NOT_FOUND", f"unknown task {task_id}")
        return self._task(row)

    def list_tasks(
        self, principal: str | None = None, status: str | None = None, limit: int = 50
    ) -> list[Task]:
        clauses, values = [], []
        if principal is not None:
            clauses.append("principal = ?")
            values.append(principal)
        if status is not None:
            clauses.append("status = ?")
            values.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.lock:
            rows = self.db.execute(
                f"SELECT * FROM tasks {where} ORDER BY updated DESC, id LIMIT ?",
                (*values, limit),
            ).fetchall()
        return [self._task(row) for row in rows]

    def history(self, task_id: str) -> list[tuple[str | None, str, str, str]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT from_status, to_status, actor, reason FROM transitions "
                "WHERE task_id = ? ORDER BY seq",
                (task_id,),
            ).fetchall()
        return [tuple(row) for row in rows]  # type: ignore[misc]

    def transition(
        self,
        task_id: str,
        to_status: str,
        actor: str,
        reason: str,
        now: float,
        expected: str | None = None,
    ) -> Task:
        """Apply one validated status change.

        `expected` turns a stale or out-of-order caller into a refusal instead
        of a transition from a status it never saw.
        """
        if to_status in {"in_review", "completed"}:
            raise OrchestratorError(
                "EVIDENCE_REQUIRED",
                "use record_verification() and complete() for review and completion",
            )
        with self.transaction() as db:
            self._move(db, task_id, to_status, actor, reason, now, expected)
            return self._task(self._row(db, task_id))  # type: ignore[arg-type]

    def cancel(self, task_id: str, actor: str, reason: str, now: float) -> Task:
        """Cancel a task and ask its live worker, if any, to stop.

        The task is cancelled at once; the attempt stays open until the worker
        adapter confirms termination through finish_attempt().
        """
        with self.transaction() as db:
            task = self._move(db, task_id, "cancelled", actor, reason, now, None)
            live = db.execute(
                "SELECT id FROM attempts WHERE task_id = ? AND finished IS NULL",
                (task_id,),
            ).fetchone()
            if live is not None:
                self._enqueue(
                    db,
                    f"worker.cancel:{live['id']}",
                    "worker.cancel",
                    task_id,
                    {"attempt_id": live["id"], "reason": reason},
                    now,
                    live["id"],
                )
            return task

    def record_verification(
        self, task_id: str, evidence: Evidence, verifier: str, now: float
    ) -> Task:
        """Verified completion: store the controller's CI and criteria evidence.

        A settled run only reaches validating; this moves it to in_review when
        the controller's own Forgejo and Woodpecker reads satisfy every check.
        Controller-only.
        """
        self._require_controller("verify tasks")
        with self.transaction() as db:
            row = self._row(db, task_id)
            if row is None:
                raise OrchestratorError("NOT_FOUND", f"unknown task {task_id}")
            self._check_evidence(row, evidence)
            db.execute(
                "INSERT INTO verifications (id, task_id, pull_request, head_sha, "
                "ci_state, criteria_met, summary, verifier, at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id("ver"),
                    task_id,
                    evidence.pull_request,
                    evidence.head_sha,
                    evidence.ci_state,
                    json.dumps(sorted(evidence.criteria_met)),
                    evidence.summary[:PAYLOAD_LIMIT],
                    verifier,
                    now,
                ),
            )
            return self._move(
                db,
                task_id,
                "in_review",
                verifier,
                "evidence verified",
                now,
                "validating",
            )

    def complete(self, task_id: str, actor: str, now: float) -> Task:
        """Authorized finalization: complete from the stored verification only.

        No caller-supplied evidence is accepted here. Controller-only.
        """
        self._require_controller("complete tasks")
        with self.transaction() as db:
            row = self._row(db, task_id)
            if row is None:
                raise OrchestratorError("NOT_FOUND", f"unknown task {task_id}")
            verification = db.execute(
                "SELECT * FROM verifications WHERE task_id = ? ORDER BY at DESC, rowid "
                "DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            if verification is None:
                raise OrchestratorError("EVIDENCE_REQUIRED", "task has no verification")
            evidence = Evidence(
                verification["pull_request"],
                verification["head_sha"],
                verification["ci_state"],
                tuple(json.loads(verification["criteria_met"])),
                verification["summary"],
            )
            self._check_evidence(row, evidence)
            task = self._move(
                db, task_id, "completed", actor, "finalized", now, "in_review"
            )
            self._event(
                db,
                f"completed_{task_id}",
                task_id,
                None,
                "task.completed",
                actor,
                {
                    "verification": verification["id"],
                    "verifier": verification["verifier"],
                    "pull_request": evidence.pull_request,
                    "head_sha": evidence.head_sha,
                    "ci_state": evidence.ci_state,
                    "criteria_met": sorted(evidence.criteria_met),
                    "summary": evidence.summary,
                },
                None,
                now,
            )
            return task

    def _check_evidence(self, row: sqlite3.Row, evidence: Evidence) -> None:
        if evidence.ci_state != "success":
            raise OrchestratorError(
                "EVIDENCE_REJECTED", f"CI state is {evidence.ci_state}, not success"
            )
        if not evidence.pull_request or not SHA.match(evidence.head_sha):
            raise OrchestratorError(
                "EVIDENCE_REJECTED",
                "a pull request and a full head commit are required",
            )
        missing = set(json.loads(row["acceptance_criteria"])) - set(
            evidence.criteria_met
        )
        if missing:
            raise OrchestratorError(
                "EVIDENCE_REJECTED",
                "unmet acceptance criteria: " + "; ".join(sorted(missing)),
            )

    # Scheduling

    def claim(
        self,
        owner: str,
        provider: str,
        model: str,
        worker_backend: str,
        lease_seconds: float,
        now: float,
        token_digest: str | None = None,
    ) -> tuple[Task, Attempt] | None:
        """Lease the highest-priority ready task whose dependencies completed.

        Only the lock-holding controller claims, and the stored global cap is
        read inside the claiming transaction, so no second scheduler and no
        per-call cap can start work past it. `token_digest` binds the worker
        API credential issued for this attempt.
        """
        self._require_controller()
        with self.transaction() as db:
            running = db.execute(
                "SELECT COUNT(*) FROM attempts WHERE finished IS NULL"
            ).fetchone()[0]
            cap = int(
                db.execute(
                    "SELECT value FROM settings WHERE key = 'max_running'"
                ).fetchone()["value"]
            )
            if running >= cap:
                return None
            row = db.execute(
                "SELECT t.* FROM tasks t WHERE t.status = 'ready' AND NOT EXISTS ("
                "  SELECT 1 FROM dependencies d JOIN tasks p ON p.id = d.depends_on "
                "  WHERE d.task_id = t.id AND p.status != 'completed') "
                "ORDER BY t.priority DESC, t.created, t.id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            number = (
                db.execute(
                    "SELECT COALESCE(MAX(number), 0) FROM attempts WHERE task_id = ?",
                    (row["id"],),
                ).fetchone()[0]
                + 1
            )
            attempt_id = new_id("att")
            db.execute(
                "INSERT INTO attempts (id, task_id, number, provider, model, "
                "worker_backend, lease_owner, lease_expires, token_digest, started) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    attempt_id,
                    row["id"],
                    number,
                    provider,
                    model,
                    worker_backend,
                    owner,
                    now + lease_seconds,
                    token_digest,
                    now,
                ),
            )
            task = self._move(
                db, row["id"], "claimed", owner, f"attempt {number}", now, "ready"
            )
            return task, self._attempt(db, attempt_id)

    def renew_lease(
        self, attempt_id: str, owner: str, lease_seconds: float, now: float
    ) -> Attempt:
        with self.transaction() as db:
            updated = db.execute(
                "UPDATE attempts SET lease_expires = ? WHERE id = ? AND lease_owner = ? "
                "AND finished IS NULL AND lease_expires > ? AND task_id IN ("
                "  SELECT id FROM tasks WHERE status NOT IN "
                "  ('cancelled', 'failed', 'completed'))",
                (now + lease_seconds, attempt_id, owner, now),
            ).rowcount
            if not updated:
                raise OrchestratorError(
                    "LEASE_LOST", f"{owner} no longer holds attempt {attempt_id}"
                )
            return self._attempt(db, attempt_id)

    def record_session(
        self, attempt_id: str, owner: str, session_ref: str, workspace_ref: str
    ) -> None:
        with self.transaction() as db:
            updated = db.execute(
                "UPDATE attempts SET session_ref = ?, workspace_ref = ? "
                "WHERE id = ? AND lease_owner = ? AND finished IS NULL",
                (session_ref, workspace_ref, attempt_id, owner),
            ).rowcount
            if not updated:
                raise OrchestratorError(
                    "LEASE_LOST", f"attempt {attempt_id} is not live"
                )

    def finish_attempt(
        self, attempt_id: str, exit_reason: str, retryable: bool, actor: str, now: float
    ) -> Task:
        """Close an attempt and move its task out of active work if needed.

        A retryable failure returns the task to ready until MAX_ATTEMPTS is
        reached. Finishing an attempt whose task already moved on (validation,
        review, cancellation) only closes the attempt.
        """
        with self.transaction() as db:
            return self._finish(db, attempt_id, exit_reason, retryable, actor, now)

    def _finish(
        self,
        db: sqlite3.Connection,
        attempt_id: str,
        exit_reason: str,
        retryable: bool,
        actor: str,
        now: float,
    ) -> Task:
        attempt = self._attempt(db, attempt_id)
        if attempt.finished is not None:
            return self._task(self._row(db, attempt.task_id))  # type: ignore[arg-type]
        db.execute(
            "UPDATE attempts SET finished = ?, exit_reason = ? WHERE id = ?",
            (now, exit_reason, attempt_id),
        )
        self._expire_questions(db, attempt_id)
        task = self._task(self._row(db, attempt.task_id))  # type: ignore[arg-type]
        if task.status not in WORKER_ACTIVE:
            return task
        if retryable and attempt.number < MAX_ATTEMPTS:
            self._move(db, task.id, "retryable_failed", actor, exit_reason, now, None)
            return self._move(db, task.id, "ready", actor, "retry admitted", now, None)
        return self._move(db, task.id, "failed", actor, exit_reason, now, None)

    def settle_attempt(
        self,
        attempt_id: str,
        owner: str,
        payload: dict[str, Any],
        now: float,
    ) -> Task:
        """Record a settled run, move its task to validating and close the attempt.

        One transaction, so a crash can never leave a live attempt attached to
        a validating task. Only the current, unexpired lease holder may settle.
        """
        with self.transaction() as db:
            attempt = self._attempt(db, attempt_id)
            if (
                attempt.finished is not None
                or attempt.lease_owner != owner
                or attempt.lease_expires <= now
            ):
                raise OrchestratorError(
                    "LEASE_LOST", f"{owner} no longer holds attempt {attempt_id}"
                )
            self._event(
                db,
                f"settled_{attempt_id}",
                attempt.task_id,
                attempt_id,
                "worker.settled",
                owner,
                payload,
                None,
                now,
            )
            self._move(
                db, attempt.task_id, "validating", owner, "run settled", now, "running"
            )
            db.execute(
                "UPDATE attempts SET finished = ?, exit_reason = 'settled' WHERE id = ?",
                (now, attempt_id),
            )
            self._expire_questions(db, attempt_id)
            return self._task(self._row(db, attempt.task_id))  # type: ignore[arg-type]

    def reap(self, now: float, actor: str = "reconciler") -> list[str]:
        """Finish every attempt whose lease expired and fence its worker.

        Reaping cannot stop a process that is still running, so each reaped
        attempt gets a `worker.cancel` message for the Job manager to delete
        its pod, and the attempt's worker API credential stops working at once.
        """
        self._require_controller()
        with self.lock:
            candidates = [
                row["id"]
                for row in self.db.execute(
                    "SELECT id FROM attempts WHERE finished IS NULL AND lease_expires <= ?",
                    (now,),
                ).fetchall()
            ]
        expired = []
        for attempt_id in candidates:
            with self.transaction() as db:
                row = db.execute(
                    "SELECT task_id FROM attempts WHERE id = ? AND finished IS NULL "
                    "AND lease_expires <= ?",
                    (attempt_id, now),
                ).fetchone()
                if row is None:
                    continue
                self._finish(db, attempt_id, "lease expired", True, actor, now)
                self._enqueue(
                    db,
                    f"worker.cancel:{attempt_id}",
                    "worker.cancel",
                    row["task_id"],
                    {"attempt_id": attempt_id, "reason": "lease expired"},
                    now,
                    attempt_id,
                )
                expired.append(attempt_id)
        return expired

    def attempt(self, attempt_id: str) -> Attempt:
        with self.lock:
            return self._attempt(self.db, attempt_id)

    def attempt_for_token(self, token_digest: str, now: float) -> Attempt | None:
        """The live, unexpired attempt a worker API credential belongs to, if any."""
        with self.lock:
            row = self.db.execute(
                "SELECT id FROM attempts WHERE token_digest = ? AND finished IS NULL "
                "AND lease_expires > ?",
                (token_digest, now),
            ).fetchone()
            return None if row is None else self._attempt(self.db, row["id"])

    # Events

    def record_event(
        self,
        event_id: str,
        task_id: str,
        attempt_id: str | None,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
        now: float,
        correlation_id: str | None = None,
    ) -> bool:
        """Append an immutable event; a redelivered event_id is a no-op."""
        with self.transaction() as db:
            return self._event(
                db,
                event_id,
                task_id,
                attempt_id,
                event_type,
                actor,
                payload,
                correlation_id,
                now,
            )

    def events(self, task_id: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM events WHERE task_id = ? ORDER BY at, rowid", (task_id,)
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    # Questions

    def ask(
        self,
        question_id: str,
        attempt_id: str,
        prompt: str,
        choices: tuple[str, ...],
        blocking: bool,
        ttl_seconds: float,
        now: float,
    ) -> Question:
        """Persist a worker question before anyone is notified of it.

        A redelivered question_id returns the stored question. A blocking
        question parks the task in needs_input.
        """
        with self.transaction() as db:
            existing = db.execute(
                "SELECT * FROM questions WHERE id = ?", (question_id,)
            ).fetchone()
            if existing is not None:
                return self._question(existing)
            attempt = self._attempt(db, attempt_id)
            if attempt.finished is not None:
                raise OrchestratorError("STALE", f"attempt {attempt_id} is finished")
            db.execute(
                "INSERT INTO questions (id, task_id, attempt_id, prompt, choices, "
                "blocking, status, revision, deadline, created) "
                "VALUES (?, ?, ?, ?, ?, ?, 'open', 1, ?, ?)",
                (
                    question_id,
                    attempt.task_id,
                    attempt_id,
                    prompt,
                    json.dumps(list(choices)),
                    int(blocking),
                    now + ttl_seconds,
                    now,
                ),
            )
            task = self._row(db, attempt.task_id)
            if blocking and task is not None and task["status"] != "needs_input":
                self._move(
                    db,
                    attempt.task_id,
                    "needs_input",
                    "worker",
                    f"question {question_id}",
                    now,
                    "running",
                )
            self._enqueue(
                db,
                f"question:{question_id}",
                "question.open",
                attempt.task_id,
                {
                    "question_id": question_id,
                    "prompt": prompt,
                    "choices": list(choices),
                    "blocking": blocking,
                    "revision": 1,
                },
                now,
            )
            return self._question(
                db.execute(
                    "SELECT * FROM questions WHERE id = ?", (question_id,)
                ).fetchone()
            )

    def answer(
        self, question_id: str, revision: int, answer: str, answered_by: str, now: float
    ) -> Question:
        """Accept the first valid answer for this revision.

        A second answer, an answer to an old revision, or one after the
        deadline is refused, so the caller can raise a follow-up instead of
        silently overriding.
        """
        self.expire_questions(now)
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM questions WHERE id = ?", (question_id,)
            ).fetchone()
            if row is None:
                raise OrchestratorError("NOT_FOUND", f"unknown question {question_id}")
            question = self._question(row)
            if question.status != "open":
                raise OrchestratorError(
                    "ALREADY_ANSWERED" if question.status == "answered" else "STALE",
                    f"question {question_id} is {question.status}",
                )
            if self._attempt(db, question.attempt_id).finished is not None:
                raise OrchestratorError(
                    "STALE", f"attempt {question.attempt_id} is finished"
                )
            if revision != question.revision:
                raise OrchestratorError(
                    "STALE",
                    f"question {question_id} is at revision {question.revision}",
                )
            if question.choices and answer not in question.choices:
                raise OrchestratorError(
                    "INVALID_ANSWER", f"answer must be one of {list(question.choices)}"
                )
            db.execute(
                "UPDATE questions SET status = 'answered', answer = ?, answered_by = ? "
                "WHERE id = ?",
                (answer, answered_by, question_id),
            )
            self._enqueue(
                db,
                f"answer:{question_id}",
                "worker.answer",
                question.task_id,
                {
                    "attempt_id": question.attempt_id,
                    "question_id": question_id,
                    "answer": answer,
                },
                now,
                question.attempt_id,
            )
            self._resume_if_unblocked(db, question, now)
            return self._question(
                db.execute(
                    "SELECT * FROM questions WHERE id = ?", (question_id,)
                ).fetchone()
            )

    def revise(
        self,
        question_id: str,
        prompt: str,
        choices: tuple[str, ...],
        ttl_seconds: float,
        now: float,
    ) -> Question:
        """Replace an open question's wording; answers to older revisions fail.

        An overdue question is expired, never revived.
        """
        self.expire_questions(now)
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM questions WHERE id = ?", (question_id,)
            ).fetchone()
            if row is None or row["status"] != "open":
                raise OrchestratorError("STALE", f"question {question_id} is not open")
            revision = row["revision"] + 1
            db.execute(
                "UPDATE questions SET prompt = ?, choices = ?, revision = ?, deadline = ? "
                "WHERE id = ?",
                (
                    prompt,
                    json.dumps(list(choices)),
                    revision,
                    now + ttl_seconds,
                    question_id,
                ),
            )
            self._enqueue(
                db,
                f"question:{question_id}:{revision}",
                "question.open",
                row["task_id"],
                {
                    "question_id": question_id,
                    "prompt": prompt,
                    "choices": list(choices),
                    "blocking": bool(row["blocking"]),
                    "revision": revision,
                },
                now,
            )
            return self._question(
                db.execute(
                    "SELECT * FROM questions WHERE id = ?", (question_id,)
                ).fetchone()
            )

    def expire_questions(self, now: float) -> list[str]:
        """Expire overdue questions and stop the attempts they were blocking.

        The task stays in needs_input until the worker adapter confirms the
        attempt ended, so no compute keeps running on a question nobody answered.
        """
        with self.transaction() as db:
            rows = db.execute(
                "SELECT * FROM questions WHERE status = 'open' AND deadline <= ?",
                (now,),
            ).fetchall()
            for row in rows:
                db.execute(
                    "UPDATE questions SET status = 'expired' WHERE id = ?", (row["id"],)
                )
                self._enqueue(
                    db,
                    f"question.expired:{row['id']}",
                    "question.expired",
                    row["task_id"],
                    {"question_id": row["id"], "attempt_id": row["attempt_id"]},
                    now,
                )
                if row["blocking"]:
                    self._enqueue(
                        db,
                        f"worker.cancel:{row['attempt_id']}",
                        "worker.cancel",
                        row["task_id"],
                        {
                            "attempt_id": row["attempt_id"],
                            "reason": f"question {row['id']} expired",
                        },
                        now,
                        row["attempt_id"],
                    )
            return [row["id"] for row in rows]

    # Approvals

    def request_approval(
        self,
        task_id: str,
        attempt_id: str,
        action: dict[str, Any],
        principal: str,
        ttl: float,
        now: float,
    ) -> tuple[Approval, bool]:
        """File an approval bound to the exact action, principal, policy and attempt."""
        with self.transaction() as db:
            if self._attempt(db, attempt_id).task_id != task_id:
                raise OrchestratorError(
                    "NOT_FOUND", f"{attempt_id} is not of {task_id}"
                )
            action_digest = self._action_digest(task_id, action, principal, attempt_id)
            db.execute(
                "UPDATE approvals SET status = 'expired' WHERE status = 'pending' "
                "AND expires <= ?",
                (now,),
            )
            existing = db.execute(
                "SELECT * FROM approvals WHERE action_digest = ? AND status = 'pending'",
                (action_digest,),
            ).fetchone()
            if existing is not None:
                return self._approval(existing), False
            identifier = new_id("apr")
            db.execute(
                "INSERT INTO approvals (id, task_id, attempt_id, action_digest, action, "
                "principal, policy_version, status, expires, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (
                    identifier,
                    task_id,
                    attempt_id,
                    action_digest,
                    canonical(action),
                    principal,
                    self.policy_version,
                    now + ttl,
                    now,
                ),
            )
            self._enqueue(
                db,
                f"approval:{identifier}",
                "approval.open",
                task_id,
                {"approval_id": identifier, "action": action, "expires": now + ttl},
                now,
            )
            return self._approval(
                db.execute(
                    "SELECT * FROM approvals WHERE id = ?", (identifier,)
                ).fetchone()
            ), True

    def decide(
        self, approval_id: str, approve: bool, decided_by: str, now: float
    ) -> Approval:
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                raise OrchestratorError("NOT_FOUND", f"unknown approval {approval_id}")
            if row["status"] != "pending" or row["expires"] <= now:
                state = "expired" if row["status"] == "pending" else row["status"]
                raise OrchestratorError("STALE", f"approval is {state}")
            if decided_by != row["principal"]:
                raise OrchestratorError(
                    "FORBIDDEN", f"{decided_by} cannot decide for {row['principal']}"
                )
            db.execute(
                "UPDATE approvals SET status = ?, decided_by = ? WHERE id = ?",
                ("approved" if approve else "declined", decided_by, approval_id),
            )
            return self._approval(
                db.execute(
                    "SELECT * FROM approvals WHERE id = ?", (approval_id,)
                ).fetchone()
            )

    def consume(
        self,
        task_id: str,
        attempt_id: str,
        action: dict[str, Any],
        principal: str,
        now: float,
    ) -> bool:
        """Spend an approval for exactly this action; each approval works once.

        It must be approved, unexpired, under the current policy, bound to the
        same attempt, that attempt must still be live, and its task open.
        """
        action_digest = self._action_digest(task_id, action, principal, attempt_id)
        with self.transaction() as db:
            task = self._row(db, task_id)
            if task is None or task["status"] in TERMINAL:
                return False
            attempt = db.execute(
                "SELECT finished FROM attempts WHERE id = ? AND task_id = ?",
                (attempt_id, task_id),
            ).fetchone()
            if attempt is None or attempt["finished"] is not None:
                return False
            used = db.execute(
                "UPDATE approvals SET status = 'used', used = ? WHERE id = ("
                "  SELECT id FROM approvals WHERE action_digest = ? "
                "  AND status = 'approved' AND expires > ? AND policy_version = ? "
                "  ORDER BY created LIMIT 1)",
                (now, action_digest, now, self.policy_version),
            ).rowcount
        return bool(used)

    # Outbox

    def pending_messages(
        self, topic: str | None = None, limit: int = 50
    ) -> list[Message]:
        query = "SELECT * FROM outbox WHERE delivered IS NULL"
        values: tuple[Any, ...] = ()
        if topic is not None:
            query += " AND topic = ?"
            values = (topic,)
        with self.lock:
            rows = self.db.execute(
                query + " ORDER BY created, key LIMIT ?", (*values, limit)
            ).fetchall()
        return [
            Message(
                row["key"],
                row["topic"],
                row["task_id"],
                json.loads(row["payload"]),
                row["attempts"],
            )
            for row in rows
        ]

    def attempt_messages(self, attempt_id: str, topic: str) -> list[Message]:
        """Undelivered messages for one attempt, so no other attempt starves it."""
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM outbox WHERE delivered IS NULL AND attempt_id = ? "
                "AND topic = ? ORDER BY created, key",
                (attempt_id, topic),
            ).fetchall()
        return [
            Message(
                row["key"],
                row["topic"],
                row["task_id"],
                json.loads(row["payload"]),
                row["attempts"],
            )
            for row in rows
        ]

    def acknowledge(self, key: str, now: float) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE outbox SET delivered = ?, attempts = attempts + 1 "
                "WHERE key = ? AND delivered IS NULL",
                (now, key),
            )

    def delivery_failed(self, key: str, error: str) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE outbox SET attempts = attempts + 1, last_error = ? WHERE key = ?",
                (error[:PAYLOAD_LIMIT], key),
            )

    # Internals

    def _row(self, db: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
        return db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()

    def _task(self, row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            principal=row["principal"],
            project=row["project"],
            title=row["title"],
            goal=row["goal"],
            acceptance_criteria=tuple(json.loads(row["acceptance_criteria"])),
            requested_capabilities=tuple(json.loads(row["requested_capabilities"])),
            priority=row["priority"],
            parent_id=row["parent_id"],
            status=row["status"],
            external_issue_ref=row["external_issue_ref"],
            policy_version=row["policy_version"],
            created=row["created"],
            updated=row["updated"],
        )

    def _attempt(self, db: sqlite3.Connection, attempt_id: str) -> Attempt:
        row = db.execute(
            "SELECT * FROM attempts WHERE id = ?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise OrchestratorError("NOT_FOUND", f"unknown attempt {attempt_id}")
        return Attempt(**{key: row[key] for key in Attempt.__dataclass_fields__})

    def _question(self, row: sqlite3.Row) -> Question:
        return Question(
            id=row["id"],
            task_id=row["task_id"],
            attempt_id=row["attempt_id"],
            prompt=row["prompt"],
            choices=tuple(json.loads(row["choices"])),
            blocking=bool(row["blocking"]),
            status=row["status"],
            revision=row["revision"],
            answer=row["answer"],
            answered_by=row["answered_by"],
            deadline=row["deadline"],
        )

    def _approval(self, row: sqlite3.Row) -> Approval:
        return Approval(**{key: row[key] for key in Approval.__dataclass_fields__})

    def _action_digest(
        self,
        task_id: str,
        action: dict[str, Any],
        principal: str,
        attempt_id: str,
    ) -> str:
        return digest(
            {
                "task_id": task_id,
                "attempt_id": attempt_id,
                "action": action,
                "principal": principal,
                "policy_version": self.policy_version,
            }
        )

    def _ancestors(self, db: sqlite3.Connection, task_id: str) -> set[str]:
        rows = db.execute(
            "WITH RECURSIVE up(id) AS (SELECT depends_on FROM dependencies "
            "WHERE task_id = ? UNION SELECT d.depends_on FROM dependencies d "
            "JOIN up ON d.task_id = up.id) SELECT id FROM up",
            (task_id,),
        ).fetchall()
        return {row["id"] for row in rows}

    def _move(
        self,
        db: sqlite3.Connection,
        task_id: str,
        to_status: str,
        actor: str,
        reason: str,
        now: float,
        expected: str | None,
    ) -> Task:
        row = self._row(db, task_id)
        if row is None:
            raise OrchestratorError("NOT_FOUND", f"unknown task {task_id}")
        current = row["status"]
        if expected is not None and current != expected:
            raise OrchestratorError(
                "STALE", f"task {task_id} is {current}, expected {expected}"
            )
        if to_status not in TRANSITIONS.get(current, frozenset()):
            raise OrchestratorError(
                "INVALID_TRANSITION",
                f"task {task_id} cannot go {current} -> {to_status}",
            )
        db.execute(
            "UPDATE tasks SET status = ?, updated = ? WHERE id = ?",
            (to_status, now, task_id),
        )
        self._record(db, task_id, current, to_status, actor, reason, now)
        if to_status in NOTIFY:
            self._enqueue(
                db,
                f"status:{task_id}:{self._seq(db, task_id)}",
                "task.status",
                task_id,
                {
                    "task_id": task_id,
                    "from": current,
                    "to": to_status,
                    "reason": reason,
                },
                now,
            )
        return self._task(self._row(db, task_id))  # type: ignore[arg-type]

    def _seq(self, db: sqlite3.Connection, task_id: str) -> int:
        return db.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM transitions WHERE task_id = ?",
            (task_id,),
        ).fetchone()[0]

    def _record(
        self,
        db: sqlite3.Connection,
        task_id: str,
        from_status: str | None,
        to_status: str,
        actor: str,
        reason: str,
        now: float,
    ) -> None:
        db.execute(
            "INSERT INTO transitions (task_id, seq, from_status, to_status, actor, "
            "reason, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                self._seq(db, task_id) + 1,
                from_status,
                to_status,
                actor,
                reason,
                now,
            ),
        )

    def _event(
        self,
        db: sqlite3.Connection,
        event_id: str,
        task_id: str,
        attempt_id: str | None,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
        correlation_id: str | None,
        now: float,
    ) -> bool:
        encoded = canonical(payload)
        if len(encoded) > PAYLOAD_LIMIT:
            encoded = canonical({"truncated": True, "bytes": len(encoded)})
        inserted = db.execute(
            "INSERT OR IGNORE INTO events (id, task_id, attempt_id, type, actor, "
            "correlation_id, payload, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                task_id,
                attempt_id,
                event_type,
                actor,
                correlation_id,
                encoded,
                now,
            ),
        ).rowcount
        return bool(inserted)

    def _enqueue(
        self,
        db: sqlite3.Connection,
        key: str,
        topic: str,
        task_id: str | None,
        payload: dict[str, Any],
        now: float,
        attempt_id: str | None = None,
    ) -> None:
        db.execute(
            "INSERT OR IGNORE INTO outbox (key, topic, task_id, attempt_id, payload, "
            "created) VALUES (?, ?, ?, ?, ?, ?)",
            (key, topic, task_id, attempt_id, canonical(payload), now),
        )

    def _expire_questions(self, db: sqlite3.Connection, attempt_id: str) -> None:
        db.execute(
            "UPDATE questions SET status = 'expired' WHERE attempt_id = ? AND status = 'open'",
            (attempt_id,),
        )

    def _resume_if_unblocked(
        self, db: sqlite3.Connection, question: Question, now: float
    ) -> None:
        row = self._row(db, question.task_id)
        if row is None or row["status"] != "needs_input":
            return
        blocked = db.execute(
            "SELECT 1 FROM questions WHERE attempt_id = ? AND status = 'open' "
            "AND blocking = 1",
            (question.attempt_id,),
        ).fetchone()
        if blocked is None:
            self._move(
                db,
                question.task_id,
                "running",
                "controller",
                f"question {question.id} answered",
                now,
                "needs_input",
            )
