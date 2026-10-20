from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class ChangeKind(StrEnum):
    ADDED = "added"
    COPIED = "copied"
    DELETED = "deleted"
    MODIFIED = "modified"
    RENAMED = "renamed"
    TYPE_CHANGED = "type_changed"
    UNMERGED = "unmerged"
    UNTRACKED = "untracked"


class ChangeSource(StrEnum):
    INDEX = "index"
    WORKTREE = "worktree"
    UNTRACKED = "untracked"
    COMMITTED = "committed"


class TaskStatus(StrEnum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    COMPLETE = "complete"


@dataclass(frozen=True)
class AcceptanceCriterion:
    description: str
    satisfied: bool
    evidence: str


@dataclass(frozen=True)
class VerificationRecord:
    command: str
    exit_code: int
    time: str
    source_fingerprint: str
    evidence_path: str
    stale: bool


@dataclass(frozen=True)
class TaskRecord:
    schema_version: int
    record_revision: int
    task_id: str
    objective: str
    status: TaskStatus
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    owning_agent: str
    session: str
    owned_files: tuple[str, ...]
    base_commit: str
    checkpoint_head: str
    timestamp: str
    dirty_fingerprint: str
    decisions: tuple[str, ...]
    durable_record_links: tuple[str, ...]
    completed_work: tuple[str, ...]
    remaining_work: tuple[str, ...]
    unresolved_failures: tuple[str, ...]
    next_action: str
    verification_records: tuple[VerificationRecord, ...]
    blocked_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChangedPath:
    path: str
    path_bytes: bytes
    kind: ChangeKind
    source: ChangeSource
    old_path: str | None = None
    old_path_bytes: bytes | None = None


@dataclass(frozen=True)
class ChangeCounts:
    index: int
    worktree: int
    untracked: int
    committed: int

    @property
    def staged(self) -> int:
        return self.index

    @property
    def unstaged(self) -> int:
        return self.worktree

    @property
    def total(self) -> int:
        return self.index + self.worktree + self.untracked + self.committed

    @property
    def local_total(self) -> int:
        return self.index + self.worktree + self.untracked


@dataclass(frozen=True)
class GitBase:
    available: bool
    reference: str | None
    resolved: str | None
    merge_base: str | None


@dataclass(frozen=True)
class GitState:
    root: Path
    branch: str | None
    detached: bool
    head: str | None
    base: GitBase
    counts: ChangeCounts
    changes: tuple[ChangedPath, ...]
    dirty: bool
    fingerprint: str

    @property
    def affected_paths(self) -> tuple[str, ...]:
        paths = {change.path for change in self.changes}
        paths.update(change.old_path for change in self.changes if change.old_path)
        return tuple(sorted(paths))
