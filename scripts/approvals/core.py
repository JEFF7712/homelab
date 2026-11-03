"""Approval requests for held CI operations and escalated merges.

An agent files a request for one catalog operation against one successful
main pipeline; the merge policy files one for a PR head that needs operator
authority. Only a passkey assertion made on the approvals origin moves a
request out of pending, and the transition is claimed atomically so a request
triggers its deployment at most once. This module holds state only; HTTP,
WebAuthn, Woodpecker, Forgejo and ntfy live in scripts.approvals.server.
"""

from __future__ import annotations

import secrets
import sqlite3
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REQUEST_TTL_SECONDS = 6 * 3600
# An approved grant covers exact protected file versions, not a commit, so
# unrelated merges keep it valid; it lapses once those versions are deployed
# (they join the policy baseline) or after this long.
GRANT_TTL_SECONDS = 24 * 3600
# A merge request waits for the operator while its PR is open; a newer head
# supersedes it. Once approved, its file versions also satisfy deployment
# grants for this long, so one approval covers merging and deploying.
MERGE_TTL_SECONDS = 7 * 24 * 3600
ENROLLMENT_TTL_SECONDS = 15 * 60
REASON_LIMIT = 2000
TERMINAL = {"approved", "declined", "expired", "failed", "superseded"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  id TEXT PRIMARY KEY,
  operation TEXT NOT NULL,
  parent INTEGER NOT NULL,
  commit_sha TEXT NOT NULL,
  requester TEXT NOT NULL,
  reason TEXT NOT NULL,
  evidence TEXT NOT NULL,
  state TEXT NOT NULL,
  created REAL NOT NULL,
  decided REAL,
  decided_by TEXT,
  pipeline INTEGER,
  error TEXT,
  challenge BLOB,
  digest TEXT,
  paths TEXT,
  kind TEXT,
  files TEXT
);
CREATE TABLE IF NOT EXISTS credentials (
  id BLOB PRIMARY KEY,
  public_key BLOB NOT NULL,
  sign_count INTEGER NOT NULL,
  label TEXT NOT NULL,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS enrollments (
  token TEXT PRIMARY KEY,
  expires REAL NOT NULL,
  challenge BLOB
);
"""


class ApprovalError(ValueError):
    """A request the service refuses, reported to the caller as a 4xx."""


def held(catalog: dict[str, Any], operation: str) -> bool:
    """Whether an operation can require operator approval for a risky change."""
    spec = catalog.get(operation)
    if spec is None:
        raise ApprovalError(f"Unknown operation {operation}")
    return bool(spec.get("mutation"))


@dataclass(frozen=True)
class Request:
    id: str
    operation: str
    parent: int
    commit_sha: str
    requester: str
    reason: str
    evidence: str
    state: str
    created: float
    decided: float | None
    decided_by: str | None
    pipeline: int | None
    error: str | None
    digest: str | None = None
    paths: str | None = None
    # deploy (triggers an operation), grant (approves protected file
    # versions for deployment) or merge (approves a PR head; parent is the PR
    # number and commit_sha its head).
    kind: str = "deploy"
    files: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "evidence": [int(item) for item in self.evidence.split(",") if item],
            "paths": [item for item in (self.paths or "").split("\n") if item],
            "files": parse_files(self.files),
        }


def format_files(files: dict[str, str]) -> str:
    return "\n".join(f"{path}\t{files[path]}" for path in sorted(files))


def parse_files(text: str | None) -> dict[str, str]:
    return dict(
        line.split("\t", 1) for line in (text or "").split("\n") if "\t" in line
    )


@dataclass(frozen=True)
class Credential:
    id: bytes
    public_key: bytes
    sign_count: int


class Store:
    def __init__(self, path: Path) -> None:
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        columns = {
            row["name"] for row in self.db.execute("PRAGMA table_info(requests)")
        }
        for column in ("digest", "paths", "kind", "files"):
            if column not in columns:
                self.db.execute(f"ALTER TABLE requests ADD COLUMN {column} TEXT")
        self.db.execute(
            "UPDATE requests SET kind = CASE WHEN digest IS NULL THEN 'deploy' "
            "ELSE 'grant' END WHERE kind IS NULL"
        )

    def close(self) -> None:
        self.db.close()

    def _request(self, row: sqlite3.Row | None) -> Request | None:
        if row is None:
            return None
        return Request(**{key: row[key] for key in Request.__dataclass_fields__})

    def _expire(self, now: float) -> None:
        self.db.execute(
            "UPDATE requests SET state = 'expired', decided = ?, challenge = NULL "
            "WHERE state = 'pending' AND (kind != 'merge' AND created < ? "
            "OR kind = 'merge' AND created < ?)",
            (now, now - REQUEST_TTL_SECONDS, now - MERGE_TTL_SECONDS),
        )

    def create(
        self,
        operation: str,
        parent: int,
        commit_sha: str,
        requester: str,
        reason: str,
        evidence: list[int],
        now: float,
        digest: str | None = None,
        paths: list[str] | None = None,
    ) -> tuple[Request, bool]:
        """File a request, or return the pending one it duplicates.

        A grant request duplicates any pending request for the same digest; a
        deployment request one for the same operation and parent.
        """
        with self.lock:
            self._expire(now)
            if digest:
                existing = self.db.execute(
                    "SELECT * FROM requests WHERE state = 'pending' AND digest = ?",
                    (digest,),
                ).fetchone()
            else:
                existing = self.db.execute(
                    "SELECT * FROM requests WHERE state = 'pending' AND kind = 'deploy' "
                    "AND operation = ? AND parent = ?",
                    (operation, parent),
                ).fetchone()
            if existing is not None:
                return self._request(existing), False  # type: ignore[return-value]
            identifier = secrets.token_urlsafe(9)
            self.db.execute(
                "INSERT INTO requests (id, operation, parent, commit_sha, requester, "
                "reason, evidence, state, created, digest, paths, kind) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
                (
                    identifier,
                    operation,
                    parent,
                    commit_sha,
                    requester,
                    reason[:REASON_LIMIT],
                    ",".join(str(int(item)) for item in evidence),
                    now,
                    digest,
                    "\n".join(paths or []) or None,
                    "grant" if digest else "deploy",
                ),
            )
            return self.get(identifier, now), True  # type: ignore[return-value]

    def create_merge(
        self,
        pull: int,
        head: str,
        requester: str,
        reason: str,
        files: dict[str, str],
        now: float,
    ) -> tuple[Request, bool]:
        """File a merge request for one PR head, or return the one it repeats.

        An approved or pending request for the same head is returned as is.
        Pending requests for older heads of the PR are superseded: only the
        newest head can merge, and the policy carries an approval of an
        identical change over itself.
        """
        with self.lock:
            self._expire(now)
            existing = self.db.execute(
                "SELECT * FROM requests WHERE kind = 'merge' AND parent = ? "
                "AND commit_sha = ? AND state IN ('pending', 'approving', 'approved') "
                "ORDER BY created DESC",
                (pull, head),
            ).fetchone()
            if existing is not None:
                return self._request(existing), False  # type: ignore[return-value]
            self.db.execute(
                "UPDATE requests SET state = 'superseded', decided = ?, "
                "decided_by = 'new head', error = ?, challenge = NULL "
                "WHERE kind = 'merge' AND parent = ? AND state = 'pending'",
                (now, f"PR #{pull} moved to {head[:12]}", pull),
            )
            identifier = secrets.token_urlsafe(9)
            self.db.execute(
                "INSERT INTO requests (id, operation, parent, commit_sha, requester, "
                "reason, evidence, state, created, paths, kind, files) "
                "VALUES (?, 'merge', ?, ?, ?, ?, '', 'pending', ?, ?, 'merge', ?)",
                (
                    identifier,
                    pull,
                    head,
                    requester,
                    reason[:REASON_LIMIT],
                    now,
                    "\n".join(sorted(files)) or None,
                    format_files(files) or None,
                ),
            )
            return self.get(identifier, now), True  # type: ignore[return-value]

    def merges(self, pull: int, now: float) -> list[Request]:
        """Merge requests for one PR, newest first."""
        with self.lock:
            self._expire(now)
            rows = self.db.execute(
                "SELECT * FROM requests WHERE kind = 'merge' AND parent = ? "
                "ORDER BY created DESC LIMIT 50",
                (pull,),
            ).fetchall()
        return [self._request(row) for row in rows]  # type: ignore[misc]

    def covering_merges(self, files: dict[str, str], now: float) -> list[Request]:
        """Recently approved merges that together approved every file version.

        Empty unless each path and its exact blob identity appears in some
        approved PR head, so a deployment grant derived from them approves
        nothing the operator has not seen.
        """
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM requests WHERE kind = 'merge' AND state = 'approved' "
                "AND decided > ? ORDER BY decided DESC",
                (now - MERGE_TTL_SECONDS,),
            ).fetchall()
        remaining = dict(files)
        used = []
        for row in rows:
            request = self._request(row)
            approved = parse_files(request.files)  # type: ignore[union-attr]
            matched = [p for p, ident in remaining.items() if approved.get(p) == ident]
            if matched:
                used.append(request)
                for path in matched:
                    del remaining[path]
        return used if files and not remaining else []  # type: ignore[return-value]

    def approve_by_merges(self, identifier: str, now: float, decided_by: str) -> None:
        """Approve a pending grant that approved merges already cover."""
        with self.lock:
            updated = self.db.execute(
                "UPDATE requests SET state = 'approved', decided = ?, decided_by = ?, "
                "challenge = NULL WHERE id = ? AND state = 'pending' AND kind = 'grant'",
                (now, decided_by, identifier),
            ).rowcount
        if not updated:
            raise ApprovalError("Request is not a pending grant")

    def granted(self, digest: str, now: float) -> bool:
        """Whether an approved, unexpired grant covers this digest."""
        with self.lock:
            row = self.db.execute(
                "SELECT 1 FROM requests WHERE kind = 'grant' AND digest = ? "
                "AND state = 'approved' "
                "AND decided > ?",
                (digest, now - GRANT_TTL_SECONDS),
            ).fetchone()
        return row is not None

    def get(self, identifier: str, now: float) -> Request | None:
        with self.lock:
            self._expire(now)
            row = self.db.execute(
                "SELECT * FROM requests WHERE id = ?", (identifier,)
            ).fetchone()
        return self._request(row)

    def recent(self, now: float, limit: int = 50) -> list[Request]:
        with self.lock:
            self._expire(now)
            rows = self.db.execute(
                "SELECT * FROM requests ORDER BY state != 'pending', created DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._request(row) for row in rows]  # type: ignore[misc]

    def set_challenge(self, identifier: str, challenge: bytes, now: float) -> None:
        with self.lock:
            self._expire(now)
            updated = self.db.execute(
                "UPDATE requests SET challenge = ? WHERE id = ? AND state = 'pending'",
                (challenge, identifier),
            ).rowcount
        if not updated:
            raise ApprovalError("Request is not pending")

    def claim(self, identifier: str, now: float) -> bytes:
        """Take the request's single-use challenge and move it to approving.

        Two concurrent approvals of one request cannot both pass: the second
        finds it no longer pending.
        """
        with self.lock:
            self._expire(now)
            row = self.db.execute(
                "SELECT challenge FROM requests WHERE id = ? AND state = 'pending'",
                (identifier,),
            ).fetchone()
            if row is None or row["challenge"] is None:
                raise ApprovalError("Request is not pending or has no open challenge")
            self.db.execute(
                "UPDATE requests SET state = 'approving', challenge = NULL WHERE id = ?",
                (identifier,),
            )
            return bytes(row["challenge"])

    def release(self, identifier: str) -> None:
        """Return a claimed request to pending after a failed assertion."""
        with self.lock:
            self.db.execute(
                "UPDATE requests SET state = 'pending' WHERE id = ? AND state = 'approving'",
                (identifier,),
            )

    def finish(
        self,
        identifier: str,
        state: str,
        now: float,
        decided_by: str,
        pipeline: int | None = None,
        error: str | None = None,
    ) -> None:
        if state not in TERMINAL:
            raise ValueError(f"Not a terminal state: {state}")
        source = "pending" if state in {"declined", "superseded"} else "approving"
        with self.lock:
            self._expire(now)
            updated = self.db.execute(
                "UPDATE requests SET state = ?, decided = ?, decided_by = ?, "
                "pipeline = ?, error = ?, challenge = NULL WHERE id = ? AND state = ?",
                (state, now, decided_by, pipeline, error, identifier, source),
            ).rowcount
        if not updated:
            raise ApprovalError(f"Request is not {source}")

    def credentials(self) -> list[Credential]:
        with self.lock:
            rows = self.db.execute(
                "SELECT id, public_key, sign_count FROM credentials"
            ).fetchall()
        return [
            Credential(bytes(row["id"]), bytes(row["public_key"]), row["sign_count"])
            for row in rows
        ]

    def add_credential(
        self,
        identifier: bytes,
        public_key: bytes,
        sign_count: int,
        label: str,
        now: float,
    ) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO credentials (id, public_key, sign_count, label, created) "
                "VALUES (?, ?, ?, ?, ?)",
                (identifier, public_key, sign_count, label, now),
            )

    def update_sign_count(self, identifier: bytes, sign_count: int) -> None:
        with self.lock:
            self.db.execute(
                "UPDATE credentials SET sign_count = ? WHERE id = ?",
                (sign_count, identifier),
            )

    def create_enrollment(self, now: float) -> str:
        token = secrets.token_urlsafe(24)
        with self.lock:
            self.db.execute(
                "INSERT INTO enrollments (token, expires) VALUES (?, ?)",
                (token, now + ENROLLMENT_TTL_SECONDS),
            )
        return token

    def set_enrollment_challenge(
        self, token: str, challenge: bytes, now: float
    ) -> None:
        with self.lock:
            updated = self.db.execute(
                "UPDATE enrollments SET challenge = ? WHERE token = ? AND expires > ?",
                (challenge, token, now),
            ).rowcount
        if not updated:
            raise ApprovalError("Enrollment link is invalid or expired")

    def take_enrollment(self, token: str, now: float) -> bytes:
        """Consume an enrollment token, returning its challenge."""
        with self.lock:
            row = self.db.execute(
                "SELECT challenge FROM enrollments WHERE token = ? AND expires > ?",
                (token, now),
            ).fetchone()
            self.db.execute("DELETE FROM enrollments WHERE token = ?", (token,))
        if row is None or row["challenge"] is None:
            raise ApprovalError("Enrollment link is invalid or expired")
        return bytes(row["challenge"])
